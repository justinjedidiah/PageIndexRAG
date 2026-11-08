"""Reflex state: thin glue between the page and the toc_rag package.

Helper methods start with an underscore so Reflex does not turn them into events.
"""

import asyncio
import dataclasses
import itertools
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import reflex as rx
from toc_rag.agent import AnswerResult, ChatTurn, Draft, Step, stream_ask
from toc_rag.errors import friendly_error
from toc_rag.indexing import (
    DEFAULT_PRESET,
    PRESET_NAMES,
    DocumentRejected,
    Estimate,
    IndexingCancelled,
    enrich_index,
    estimate_index,
)
from toc_rag.indexing.estimate import count_describable_images
from toc_rag.indexing.isolated import build_index_isolated
from toc_rag.indexing.options import PRESET_DESCRIPTIONS, PRESET_LABELS, matching_preset, preset_options
from toc_rag.indexing.pdf_cache import forget as forget_saved_read
from toc_rag.indexing.pipeline import doc_id_for
from toc_rag.library import SAMPLE_SCOPE, Library
from toc_rag.providers import PROVIDER_IDS, PROVIDERS, LLMConfig, list_models
from toc_rag.models import DocumentIndex, IndexOptions
from toc_rag.outline import node_markdown, outline_rows
from toc_rag.sessions import session_id_for
from toc_rag.uploads import UploadError, safe_filename, validate_upload

from .runtime import sessions, settings
from .viewmodels import ChatMessage, CitationView, DocView, ImageView, OutlineRowView, PageView, PresetView, StepView

log = logging.getLogger(__name__)

UPLOAD_ID = "doc_upload"
MAX_HISTORY_TURNS = 6  # earlier question/answer pairs the agent gets as context
MAX_SOURCE_PAGES = 30  # pages a PDF section shows in the source panel (a whole chapter can be dozens)
DRAFT_INTERVAL = 0.08  # seconds between streamed-text updates sent to the browser
TREE_OPTIONS = ("split_large", "max_leaf_tokens", "small_sections", "min_leaf_tokens")  # changing these rebuilds the tree
MAX_LEAF_CHOICES = sorted({"500", "1000", "2000", "4000", str(settings.max_leaf_tokens)}, key=int)
MIN_LEAF_CHOICES = sorted({"0", "50", "100", "150", "300", str(settings.min_leaf_tokens)}, key=int)


@dataclasses.dataclass
class IndexingJob:
    """A running "Index it" for one session. Kept outside the state: Reflex state cannot hold events or locks.

    `cancel` asks the job to stop. `lock` is held while the job saves and while "End session" deletes
    the session folder, so a job can never save into (and so recreate) a folder that was just deleted.
    """

    cancel: threading.Event = dataclasses.field(default_factory=threading.Event)
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)


_jobs: dict[str, IndexingJob] = {}  # session token -> its running job

# Reading a document (above all a long PDF) is the one CPU-heavy step, so only this many run at once for
# all visitors together; the others wait their turn instead of slowing the server down for everyone.
_read_slots = asyncio.Semaphore(settings.max_parallel_reads)
READING = "Reading the document..."
WAITING = "Waiting for a free slot: other visitors' documents are being read first..."


def _older_versions(store, doc_id: str, file_name: str) -> list[str]:
    """The visitor's other documents uploaded under this file name: earlier versions of it, which indexing
    this one replaces (the same file again has the same id, so it is not listed)."""
    return [
        entry.doc_id
        for entry in store.list_documents()
        if entry.doc_id != doc_id and store.has_index(entry.doc_id) and store.load_index(entry.doc_id).source_path == file_name
    ]


def _remove_document(token: str, doc_id: str) -> None:
    """A document's index, images, rendered pages and uploaded file."""
    sessions.store(token).delete_document(doc_id)
    shutil.rmtree(sessions.upload_dir(token, doc_id), ignore_errors=True)


async def _read_document(path: Path, store, options: IndexOptions) -> DocumentIndex:
    async with _read_slots:
        return await asyncio.to_thread(build_index_isolated, path, store, settings, options)


def image_url(scope: str, doc_id: str, relative: str) -> str:
    return f"{rx.config.get_config().api_url}/api/files/{scope}/{doc_id}/{relative}"


def pending_path(token: str) -> Path:
    return sessions.session_dir(token) / "pending.json"


# Counts down every element with a data-expires-at attribute (seconds since 1970) in whole hours, then minutes, in the browser,
# so the timer ticks without any traffic to the server. React leaves the text alone: the element has no children.
COUNTDOWN_SCRIPT = """
(function () {
  // Whole hours, rounded up, while an hour or more is left ("2 hours"); then minutes ("59 min").
  function describe(left) {
    if (left <= 0) { return 'now (refresh the page)'; }
    if (left < 60) { return 'under a minute'; }
    if (left >= 3600) { var hours = Math.ceil(left / 3600); return hours + (hours === 1 ? ' hour' : ' hours'); }
    return Math.floor(left / 60) + ' min';
  }
  function tick() {
    document.querySelectorAll('[data-expires-at]').forEach(function (el) {
      var end = parseFloat(el.getAttribute('data-expires-at')) * 1000;
      var text = end ? describe(Math.floor((end - Date.now()) / 1000)) : '';
      // Only write when it changed: writing is itself a change the observer below sees, so writing the same
      // text every time would make the page update itself endlessly.
      if (el.textContent !== text) { el.textContent = text; }
    });
  }
  if (!window.__sessionCountdown) {
    window.__sessionCountdown = setInterval(tick, 1000);
    // Also update at once when a timer appears or its end time changes, so it is never blank for a second.
    new MutationObserver(tick).observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['data-expires-at']});
  }
  tick();
})();
"""


# Click a picture in the chat or the source panel (anything inside [data-zoom-area]) to see it large, over the
# page. Plain browser code: the pictures in a source section are rendered from Markdown, so they have no Reflex
# events, and opening a picture needs no trip to the server. The popup fits the picture to the screen; Escape, the
# close button or a click anywhere closes it.
ZOOM_SCRIPT = """
(function () {
  if (window.__imageZoom) { return; }
  window.__imageZoom = true;
  var opener = null;
  function close() {
    var overlay = document.getElementById('image-zoom');
    if (overlay) { overlay.remove(); }
    document.removeEventListener('keydown', onKey);
    if (opener) { opener.focus && opener.focus(); opener = null; }
  }
  function onKey(event) { if (event.key === 'Escape') { close(); } }
  function zoomable(target) {
    var img = target && target.closest ? target.closest('[data-zoom-area] img') : null;
    return img && !img.closest('#image-zoom') ? img : null;
  }
  document.addEventListener('mouseover', function (event) {
    var img = zoomable(event.target);
    if (img && !img.title) { img.title = 'Click to enlarge'; }
  });
  document.addEventListener('click', function (event) {
    var img = zoomable(event.target);
    if (!img) { return; }
    event.preventDefault();
    opener = img;
    var overlay = document.createElement('div');
    overlay.id = 'image-zoom';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'Enlarged picture');
    var big = document.createElement('img');
    big.src = img.currentSrc || img.src;
    big.alt = img.alt || '';
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'image-zoom-close';
    button.setAttribute('aria-label', 'Close');
    // Drawn, not the "x" character: a character sits on the text baseline, so it is never quite centred.
    button.innerHTML = '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">'
      + '<path d="M3 3 L13 13 M13 3 L3 13" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
    button.addEventListener('click', function (e) { e.stopPropagation(); close(); });
    overlay.appendChild(big);
    if (img.alt) {
      var caption = document.createElement('div');
      caption.className = 'image-zoom-caption';
      caption.textContent = img.alt;
      caption.addEventListener('click', function (e) { e.stopPropagation(); });
      overlay.appendChild(caption);
    }
    overlay.appendChild(button);
    overlay.addEventListener('click', close);
    document.body.appendChild(overlay);
    document.addEventListener('keydown', onKey);
    button.focus();
  });
})();
"""


def scroll_chat(force: bool = False) -> rx.event.EventSpec:
    """Keep the newest text in view, but only if the visitor is already near the bottom.

    Someone who scrolled up to re-read an earlier answer is not pulled down while a new one streams.
    `force` scrolls anyway (used when they send a question).
    """
    return rx.call_script(
        "(() => { const end = document.getElementById('chat-end'); if (!end) return;"
        " const view = end.closest('[data-radix-scroll-area-viewport]');"
        f" if ({'false' if force else 'true'} && view && view.scrollHeight - view.scrollTop - view.clientHeight > 250) return;"
        f" end.scrollIntoView({{behavior: '{'smooth' if force else 'auto'}', block: 'end'}}); }})()"
    )


class State(rx.State):
    # The visitor's provider and key. The key is a backend variable: it is never sent to the browser,
    # and nothing here writes it to disk or to a log.
    provider: str = "anthropic"
    _api_key: str = ""
    key_set: bool = False
    key_error: str = ""
    key_checking: bool = False
    # The chat models this key can use, listed by the provider when the key was checked, and the
    # visitor's choice for indexing (summaries and images) and for the chat agent. Nothing is
    # pre-selected: the visitor picks both ("" until then).
    models: list[str] = []
    index_model: str = ""
    agent_model: str = ""

    docs: list[DocView] = []
    has_samples: bool = False
    # Documents the visitor turned off: left out of answers, still listed and browsable. Samples and uploads alike.
    disabled_docs: list[str] = []
    # The samples are turned off once, when the visitor's first own document is indexed, so answers come from
    # it. Only that once: a sample turned back on stays on through later uploads.
    _samples_turned_off: bool = False
    # When this visitor's uploads are deleted (seconds since 1970), or 0 before the first upload.
    # The page counts down to it in the browser; the server only sends the time once.
    session_expires_at: float = 0

    messages: list[ChatMessage] = []  # finished messages only; changes once per question and once per answer
    thinking: bool = False
    # The answer being written. Kept apart from `messages` so each streamed update sends only this, not
    # the whole conversation again (Reflex sends a changed list in full). Added to `messages` when done.
    live_steps: list[StepView] = []
    live_text: str = ""
    _chat_generation: int = 0  # bumped when the chat is cleared, so an answer still streaming is dropped

    upload_error: str = ""
    building: bool = False
    building_label: str = READING
    # The unsummarized index waits on disk (pending.json in the session folder), not in state:
    # Reflex wraps objects held in state in proxies that worker threads must not mutate.
    _pending_id: str = ""
    _pending_file: str = ""
    has_pending: bool = False
    pending_name: str = ""
    replace_note: str = ""  # set when the pending upload is already in the library: indexing it again replaces it
    pending_detail: str = ""
    # The cost of the options chosen now, in parts, for the confirmation shown before anything is spent.
    pending_calls: int = 0
    pending_tokens: str = ""  # "about 10,029 in, 1,760 out"
    pending_size: str = ""  # "16 sections, 0 images to describe"
    confirm_open: bool = False
    rebuilding: bool = False
    # Indexing options, as plain values (see IndexOptions). The preset is "custom" once they match none.
    preset: str = DEFAULT_PRESET
    preset_choices: list[PresetView] = []
    # Estimates for every combination of the LLM options on the pending tree, worked out once per tree,
    # so choosing a preset or a summary option never touches the disk. Keyed by _estimate_key.
    _estimates: dict[str, dict[str, int]] = {}
    show_advanced: bool = False
    show_agent_settings: bool = False
    opt_split: bool = True
    opt_max_leaf: str = str(settings.max_leaf_tokens)
    opt_small: str = "merge"
    opt_min_leaf: str = str(settings.min_leaf_tokens)
    opt_summaries: str = "llm"
    opt_summary_length: str = "detailed"
    opt_images: bool = True
    indexing: bool = False
    index_done: int = 0
    index_total: int = 0
    index_label: str = ""

    source_open: bool = False
    source_title: str = ""
    source_location: str = ""
    source_summary: str = ""
    source_text: str = ""
    # An uploaded PDF's section can also be shown as its pages ("Text" / "Pages" in the source panel).
    source_pages: list[PageView] = []
    source_pages_note: str = ""  # "First 30 of 33 pages." when a section has more than are shown
    source_tab: str = "text"

    browse_open: bool = False
    browse_title: str = ""
    browse_structure: str = ""  # where the outline came from, in words ("" for older indexes)
    browse_rows: list[OutlineRowView] = []

    listing_depth: str = str(settings.agent_listing_depth)  # tree levels one agent listing shows

    # ---- derived ----

    @rx.var
    def can_chat(self) -> bool:
        return self.key_set and self.agent_model != "" and self.enabled_doc_count > 0 and not self.thinking

    @rx.var
    def enabled_doc_count(self) -> int:
        return sum(1 for doc in self.docs if doc.enabled)

    @rx.var
    def samples_enabled(self) -> bool:
        """Whether any sample is on (the sample questions are only offered then)."""
        return any(doc.sample and doc.enabled for doc in self.docs)

    @rx.var
    def enabled_sample_names(self) -> list[str]:
        """The samples turned on, by doc id without its content hash ("meridian-station-operations-handbook"),
        so each sample question is offered only while its document is on."""
        return [doc.doc_id.rsplit("-", 1)[0] for doc in self.docs if doc.sample and doc.enabled]

    @rx.var
    def chat_hint(self) -> str:
        """What is missing before chatting, or "" when nothing is."""
        if not self.key_set:
            return "Add your API key to start chatting. Without one you can still browse the library."
        if not self.agent_model:
            return "Choose a model for chat (under your API key) to start chatting."
        if self.docs and self.enabled_doc_count == 0:
            return "Every document is turned off. Turn one on in the library to ask about it."
        return ""

    @rx.var
    def index_hint(self) -> str:
        """Why "Index it" is not available with the chosen options, or "" when it is."""
        if not (self.opt_summaries == "llm" or self.opt_images):
            return ""
        if not self.key_set:
            return "These options use your API key: add it above, or pick Free."
        if not self.index_model:
            return "Choose a model for indexing above, or pick Free."
        return ""

    @rx.var
    def index_percent(self) -> int:
        return int(100 * self.index_done / self.index_total) if self.index_total else 0

    @rx.var
    def own_doc_count(self) -> int:
        return sum(1 for doc in self.docs if not doc.sample)

    @rx.var
    def provider_label(self) -> str:
        return PROVIDERS[self.provider].label

    @rx.var
    def key_placeholder(self) -> str:
        return PROVIDERS[self.provider].key_placeholder

    @rx.var
    def key_url(self) -> str:
        return PROVIDERS[self.provider].key_url

    @rx.var
    def preset_label(self) -> str:
        return PRESET_LABELS.get(self.preset, "Custom options")

    @rx.var
    def needs_key_to_index(self) -> bool:
        return self.opt_summaries == "llm" or self.opt_images

    # ---- helpers ----

    def _llm(self) -> LLMConfig:
        """The visitor's provider, key and models, for one indexing run or one answer."""
        return LLMConfig(self.provider, self._api_key, index_model=self.index_model, agent_model=self.agent_model)

    def _forget_key(self) -> None:
        self._api_key = ""
        self.key_set = False
        self.models = []
        self.index_model = ""
        self.agent_model = ""

    def _token(self) -> str:
        """This visitor's session id (names their folder, appears in file URLs). Never the client token itself."""
        return session_id_for(self.router.session.client_token)

    def _library(self) -> Library:
        return Library.for_session(settings, sessions, self._token() or None)

    def _refresh(self) -> None:
        docs = self._library().documents()
        present = {d.doc_id for d in docs}
        self.disabled_docs = [doc_id for doc_id in self.disabled_docs if doc_id in present]  # deleted or expired: forget
        off = set(self.disabled_docs)
        self.docs = [DocView(d.doc_id, d.title, d.kind, d.summary, d.sample, d.doc_id not in off) for d in docs]
        self.has_samples = any(d.sample for d in docs)
        session = sessions.get(self._token())
        self.session_expires_at = session.expires_at if session else 0

    def _clear_pending(self, delete_files: bool) -> None:
        if delete_files and self._pending_id:
            token = self._token()
            store = sessions.store(token)
            # A new upload has image files and its uploaded file but no saved index yet: remove them. The same
            # file uploaded again shares both with the indexed document: keep them, so cancelling (or a failed
            # run) leaves the existing version as it was, its pages included.
            if not store.has_index(self._pending_id):
                _remove_document(token, self._pending_id)
            pending_path(token).unlink(missing_ok=True)
        self._pending_id = ""
        self._pending_file = ""
        self.has_pending = False
        self.pending_name = ""
        self.replace_note = ""
        self.pending_detail = ""
        self.pending_calls = 0
        self.pending_tokens = ""
        self.pending_size = ""
        self.confirm_open = False
        self.preset_choices = []
        self._estimates = {}

    def _restore_pending(self) -> None:
        """Bring back an upload that was waiting for "Index it" when the state was lost (e.g. a server restart).

        The waiting index is on disk, so nothing needs to be uploaded again. Its options come back with it.
        """
        token = self._token()
        if self.has_pending or self.indexing or self.building or not token:
            return
        try:
            path = pending_path(token)
            if not path.is_file():
                return
            index = DocumentIndex.model_validate_json(path.read_text(encoding="utf-8"))
            if not (sessions.upload_dir(token, index.doc_id) / index.source_path).is_file():
                return
        except Exception:
            log.exception("Could not restore a pending document")
            return
        self._pending_id = index.doc_id
        self._pending_file = f"{index.doc_id}/{index.source_path}"
        self.has_pending = True
        self.pending_name = index.title
        self.replace_note = self._replace_note(index.doc_id, index.source_path)
        if index.options:
            options = index.options
            self.opt_split = options.split_large
            self.opt_max_leaf = str(options.max_leaf_tokens)
            self.opt_small = options.small_sections
            self.opt_min_leaf = str(options.min_leaf_tokens)
            self.opt_summaries = options.summaries
            self.opt_summary_length = options.summary_length
            self.opt_images = options.describe_images
        self._cache_estimates(index)

    def _replace_note(self, doc_id: str, file_name: str) -> str:
        """If this file is already in the visitor's library, as it is or as another version (the same file name):
        say so, and that indexing replaces it."""
        token = self._token()
        store = sessions.store(token) if token and sessions.get(token) else None
        if store is None:
            return ""
        if store.has_index(doc_id):
            existing = store.load_index(doc_id)
            return f"Already in your library (indexed with {_indexed_with_text(existing)}). Indexing it again replaces that version."
        if _older_versions(store, doc_id, file_name):
            return f"A different version of {file_name} is in your library. Indexing this one replaces it."
        return ""

    def _options(self) -> IndexOptions:
        return IndexOptions(
            split_large=self.opt_split,
            max_leaf_tokens=int(self.opt_max_leaf),
            small_sections=self.opt_small,
            min_leaf_tokens=int(self.opt_min_leaf),
            summaries=self.opt_summaries,
            summary_length=self.opt_summary_length,
            describe_images=self.opt_images,
        )

    def _pending_lost(self) -> bool:
        """The waiting upload is gone from disk: its session expired (the sweep deleted the folder) while the
        page stayed open. Then it is cleared and the visitor told, instead of "Index it" failing with an error."""
        token = self._token()
        if pending_path(token).is_file() and (sessions.uploads_dir(token) / self._pending_file).is_file():
            return False
        self._clear_pending(delete_files=False)
        self.upload_error = "That upload was deleted when your session expired. Upload it again."
        self._refresh()
        return True

    def _load_pending(self) -> DocumentIndex:
        return DocumentIndex.model_validate_json(pending_path(self._token()).read_text(encoding="utf-8"))

    def _cache_estimates(self, index: DocumentIndex) -> None:
        """Work out the cost of every LLM option combination on this tree (images are opened once), then show it."""
        store = sessions.store(self._token())
        tree = self._options().model_dump(include=set(TREE_OPTIONS))
        images = count_describable_images(index, store)
        cache = {}
        for summaries, length, describe in itertools.product(("llm", "preview"), ("short", "detailed"), (True, False)):
            options = IndexOptions(**tree, summaries=summaries, summary_length=length, describe_images=describe)
            cache[_estimate_key(options)] = dataclasses.asdict(estimate_index(index, store, options, images))
        self._estimates = cache
        self._show_estimates()

    def _show_estimates(self) -> None:
        """The cost of each preset, and of the options chosen now, from the cache."""
        if not self._estimates:
            return
        options = self._options()

        def estimate(opts: IndexOptions) -> Estimate:
            return Estimate(**self._estimates[_estimate_key(opts)])

        self.preset_choices = [
            PresetView(name, PRESET_LABELS[name], PRESET_DESCRIPTIONS[name], _cost_text(estimate(preset_options(name, settings))))
            for name in PRESET_NAMES
        ]
        current = estimate(options)
        self.preset = matching_preset(options) or "custom"
        images = f"{current.images} image{'' if current.images == 1 else 's'} to describe"
        self.pending_detail = f"{current.sections} sections, {images}. {_cost_text(current)}"
        self.pending_calls = current.calls
        self.pending_tokens = f"about {current.input_tokens:,} in, {current.output_tokens:,} out"
        self.pending_size = f"{current.sections} sections, {images}"

    async def _options_changed(self, tree_changed: bool):
        """Refresh the estimates; rebuild the pending tree first when a tree option changed (free, no LLM)."""
        self.preset = matching_preset(self._options()) or "custom"
        if not self.has_pending:
            return
        if not tree_changed:
            self._show_estimates()  # summary and image options only change the cost, which is cached
            return
        if self._pending_lost():
            return
        index = self._load_pending()
        if index.options != self._options():
            self.rebuilding = True
            yield
            try:
                path = sessions.uploads_dir(self._token()) / self._pending_file
                index = await _read_document(path, sessions.store(self._token()), self._options())
                pending_path(self._token()).write_text(index.model_dump_json(), encoding="utf-8")
                self.upload_error = ""
            except Exception:
                log.exception("Rebuilding a pending document failed")
                self.upload_error = "Could not apply those options to this document."
            finally:
                self.rebuilding = False
        self._cache_estimates(index)

    # ---- page load and session ----

    @rx.event
    def on_load(self):
        self._restore_pending()
        self._refresh()
        # The session timer, and click-to-enlarge for pictures, both run in the browser (set up once per page).
        return [rx.call_script(COUNTDOWN_SCRIPT), rx.call_script(ZOOM_SCRIPT)]

    @rx.event
    def end_session(self):
        """Delete everything this visitor added, stop any indexing that uses their key, and forget the key."""
        token = self._token()
        job = _jobs.get(token)
        if job is None:
            sessions.delete(token)
        else:
            with job.lock:  # brief: the job only holds it while it saves
                job.cancel.set()  # no new model call starts; the few already sent finish and are discarded
                sessions.delete(token)
        self._forget_key()
        self.key_error = ""
        self.disabled_docs = []  # a fresh start: every sample on, and turned off again by the next first upload
        self._samples_turned_off = False
        self.messages = []
        self.thinking = False
        self.live_steps = []
        self.live_text = ""
        self._chat_generation += 1
        self.upload_error = ""
        self.indexing = False
        self.index_done = self.index_total = 0
        self.index_label = ""
        self._clear_pending(delete_files=False)
        self._refresh()
        stopped = " Indexing was stopped." if job else ""
        return rx.toast.info(f"Session ended. Your uploads and chat were deleted.{stopped}")

    # ---- API key ----

    @rx.event
    def set_provider(self, value: str):
        if value in PROVIDER_IDS and not self.key_set:
            self.provider = value
            self.key_error = ""

    @rx.event
    async def save_key(self, form_data: dict[str, Any]):
        key = str(form_data.get("api_key", "")).strip()
        info = PROVIDERS[self.provider]
        self.key_error = ""
        if not key:
            self.key_error = f"Paste your {info.label} API key first."
            return
        self.key_checking = True
        yield
        try:
            # Listing the models is free, so it doubles as the key check.
            models = await asyncio.to_thread(list_models, self.provider, key)
        except Exception as exc:  # the message shown never contains the exception text
            self.key_error = friendly_error(exc, info.label)
        else:
            if not models:
                self.key_error = f"{info.label} accepted the key but lists no chat models for it."
            else:
                self._api_key = key
                self.key_set = True
                self.models = models
                self.index_model = ""
                self.agent_model = ""
        finally:
            self.key_checking = False

    @rx.event
    def set_index_model(self, value: str):
        if value in self.models:
            self.index_model = value

    @rx.event
    def set_agent_model(self, value: str):
        if value in self.models:
            self.agent_model = value

    @rx.event
    def clear_key(self):
        self._forget_key()

    # ---- documents ----

    @rx.event
    async def handle_upload(self, files: list[rx.UploadFile]):
        self.upload_error = ""
        if self.building or self.indexing or self.has_pending:
            return
        if not files:  # the drop zone already refused the file (wrong type or too big) and said so
            return

        upload = files[0]
        name = safe_filename(upload.filename or "upload")
        data = await upload.read(settings.max_upload_bytes + 1)  # never read more than the limit allows
        try:
            kind = validate_upload(name, data, settings.max_upload_bytes)
        except UploadError as exc:
            self.upload_error = str(exc)
            return

        # Checked before anything is written, so a refused upload does not start a session (and its clock).
        doc_id = doc_id_for(Path(name).stem, data)
        library = self._library()
        if library.is_sample(doc_id):
            self.upload_error = "That's one of the sample documents, already in the library."
            return
        # Indexing this replaces a document in the library (the same file again, or another version of it under
        # the same name), so it does not count as one more.
        replacing = bool(library.session) and (
            library.session.has_index(doc_id) or bool(_older_versions(library.session, doc_id, name))
        )
        if not replacing and self.own_doc_count >= settings.max_session_docs:
            self.upload_error = f"You can keep up to {settings.max_session_docs} documents in a session. Delete one first."
            return
        token = self._token()
        if sessions.get(token) is None and sessions.count() >= settings.max_sessions:
            self.upload_error = "The server is full right now. Try again later; the sample library still works."
            return

        self.building = True
        self.building_label = WAITING if _read_slots.locked() else READING
        yield
        path: Path | None = None

        def discard_upload() -> None:
            # Unless it is the very file an indexed document was built from (the same file uploaded again).
            if not sessions.store(token).has_index(doc_id):
                _remove_document(token, doc_id)

        try:
            sessions.ensure(token)
            directory = sessions.upload_dir(token, doc_id)  # its own folder: never overwrites another document's file
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / name
            path.write_bytes(data)
            store = sessions.store(token)
            async with _read_slots:
                if self.building_label != READING:
                    self.building_label = READING
                    yield
                index = await asyncio.to_thread(build_index_isolated, path, store, settings, self._options())
        except DocumentRejected as exc:  # password, scanned, too long: the message says which
            log.info("Upload refused: %s", exc)
            self.upload_error = str(exc)
            if path:
                discard_upload()
            return
        except Exception:
            log.exception("Reading an uploaded document failed")
            self.upload_error = "Could not read that document. It may be damaged or use unsupported features."
            if path:
                discard_upload()
            return
        finally:
            self.building = False

        pending_path(token).write_text(index.model_dump_json(), encoding="utf-8")
        self._pending_id = index.doc_id
        self._pending_file = f"{doc_id}/{name}"
        self.has_pending = True
        self.pending_name = index.title
        self.replace_note = self._replace_note(index.doc_id, name)
        self._cache_estimates(index)
        self._refresh()
        # No rx.clear_selected_files here: the drop zone sends the file straight away (on_drop), so it keeps
        # no list of selected files, and asking to clear one is a frontend error.

    # ---- indexing options ----

    @rx.event
    def set_preset(self, name: str):
        if name not in PRESET_NAMES:
            return
        chosen = preset_options(name, settings)
        self.opt_summaries = chosen.summaries
        self.opt_summary_length = chosen.summary_length
        self.opt_images = chosen.describe_images
        self.preset = name
        self._show_estimates()

    @rx.event
    def toggle_advanced(self):
        self.show_advanced = not self.show_advanced

    @rx.event
    def toggle_agent_settings(self):
        self.show_agent_settings = not self.show_agent_settings

    @rx.event
    async def set_opt_split(self, value: bool):
        self.opt_split = bool(value)
        async for update in self._options_changed(tree_changed=True):
            yield update

    @rx.event
    async def set_opt_max_leaf(self, value: str):
        if value in MAX_LEAF_CHOICES:
            self.opt_max_leaf = value
            async for update in self._options_changed(tree_changed=True):
                yield update

    @rx.event
    async def set_opt_small(self, value: str):
        if value in ("off", "merge", "fold"):
            self.opt_small = value
            async for update in self._options_changed(tree_changed=True):
                yield update

    @rx.event
    async def set_opt_min_leaf(self, value: str):
        if value in MIN_LEAF_CHOICES:
            self.opt_min_leaf = value
            async for update in self._options_changed(tree_changed=True):
                yield update

    @rx.event
    async def set_opt_summaries(self, value: str):
        if value in ("llm", "preview"):
            self.opt_summaries = value
            async for update in self._options_changed(tree_changed=False):
                yield update

    @rx.event
    async def set_opt_summary_length(self, value: str):
        if value in ("short", "detailed"):
            self.opt_summary_length = value
            async for update in self._options_changed(tree_changed=False):
                yield update

    @rx.event
    async def set_opt_images(self, value: bool):
        self.opt_images = bool(value)
        async for update in self._options_changed(tree_changed=False):
            yield update

    @rx.event
    def ask_to_index(self):
        """"Index it": straight away when it costs nothing (Free), else first a confirmation showing the cost."""
        if self.index_hint or self.indexing or self.rebuilding or not self.has_pending:
            return
        if not self.needs_key_to_index:
            return State.confirm_index
        self.confirm_open = True

    @rx.event
    def set_confirm_open(self, value: bool):
        self.confirm_open = value

    @rx.event
    def start_indexing(self):
        self.confirm_open = False
        return State.confirm_index

    @rx.event
    def cancel_pending(self):
        self._clear_pending(delete_files=True)
        self._refresh()

    @rx.event(background=True)
    async def confirm_index(self):
        async with self:
            if not self._pending_id or self.indexing or self.rebuilding:
                return
            if self._pending_lost():
                return
            options = self._options()
            if options.uses_llm and not self.key_set:
                self.upload_error = "Add your API key first, or choose the Free preset (it needs no key)."
                return
            if options.uses_llm and not self.index_model:
                self.upload_error = "Choose a model for indexing first, or choose the Free preset."
                return
            llm, token = self._llm(), self._token()
            source = sessions.uploads_dir(token) / self._pending_file
            index = self._load_pending()
            total = estimate_index(index, sessions.store(token), options).calls
            self.indexing = True
            self.upload_error = ""
            self.index_done = 0
            self.index_total = total
            self.index_label = "Starting"
            job = _jobs[token] = IndexingJob()

        store = sessions.store(token)
        progress = {"done": 0, "total": total, "label": "Starting"}

        def on_progress(done: int, total: int, label: str) -> None:  # runs on worker threads
            progress.update(done=done, total=total, label=label)

        def run(index: DocumentIndex) -> DocumentIndex:
            if index.options != options:  # e.g. switched between previews and summaries after the upload
                if job.cancel.is_set():
                    raise IndexingCancelled
                index = build_index_isolated(source, store, settings, options)
            if options.uses_llm:
                enrich_index(index, store, settings, None, on_progress, options, cancel=job.cancel, llm=llm)
            with job.lock:
                if job.cancel.is_set():
                    raise IndexingCancelled
                store.save_index(index)
            return index

        work = asyncio.create_task(asyncio.to_thread(run, index))
        error = ""
        try:
            while not work.done():
                await asyncio.wait({work}, timeout=0.4)
                if job.cancel.is_set():
                    continue  # the session was ended: its page state is no longer this job's to update
                async with self:
                    self.index_done, self.index_total, self.index_label = progress["done"], progress["total"], progress["label"]
            index = work.result()  # raises if an LLM call failed
        except Exception as exc:
            if not job.cancel.is_set():
                error = friendly_error(exc, llm.info.label)
                log.warning("Indexing failed: %s", type(exc).__name__)  # the type only: the text could hold request details
        finally:
            if _jobs.get(token) is job:
                del _jobs[token]

        if job.cancel.is_set():
            # "End session" already reset the page and deleted the folder. A rebuild may have written image
            # files after that; remove them unless the visitor has started a new session since.
            with job.lock:
                if sessions.get(token) is None:
                    sessions.delete(token)
            return

        async with self:
            self.indexing = False
            if error:
                self.upload_error = f"Indexing failed. {error}"
                self._clear_pending(delete_files=True)
            else:
                pending_path(token).unlink(missing_ok=True)
                forget_saved_read(source)  # indexed: options can no longer change, so the saved read is done with
                self._clear_pending(delete_files=False)
                # Another version of this file (same name, other content) is replaced by this one, not kept beside it.
                for older in _older_versions(store, index.doc_id, index.source_path):
                    _remove_document(token, older)
            self._refresh()
            samples_off = False
            if not error:
                # Just indexed: it is on, even if an earlier version of it had been turned off.
                self.disabled_docs = [d for d in self.disabled_docs if d != index.doc_id]
                if not self._samples_turned_off:  # the visitor's first own document: answers should come from it
                    self._samples_turned_off = True
                    samples = [doc.doc_id for doc in self.docs if doc.sample]
                    self.disabled_docs = [*self.disabled_docs, *(s for s in samples if s not in self.disabled_docs)]
                    samples_off = bool(samples)
                self._refresh()
        if not error:
            note = " The sample documents are turned off; turn them back on in the library." if samples_off else ""
            yield rx.toast.success(f"Indexed {index.title}. You can ask about it now.{note}")

    @rx.event
    def toggle_doc(self, doc_id: str):
        """Turn a document on or off for answers."""
        if doc_id not in {doc.doc_id for doc in self.docs}:
            return
        if doc_id in self.disabled_docs:
            self.disabled_docs = [d for d in self.disabled_docs if d != doc_id]
        else:
            self.disabled_docs = [*self.disabled_docs, doc_id]
        off = set(self.disabled_docs)
        self.docs = [dataclasses.replace(doc, enabled=doc.doc_id not in off) for doc in self.docs]

    @rx.event
    def delete_doc(self, doc_id: str):
        library = self._library()
        if library.is_sample(doc_id) or not self._token():
            return
        _remove_document(self._token(), doc_id)  # its uploaded file too, not only the index
        self._refresh()

    # ---- viewing sources ----

    @rx.event
    def open_source(self, ref: str):
        doc_id, _, node_id = ref.partition(":")
        library = self._library()
        index = library.load(doc_id)
        node = index.node_map().get(node_id) if index else None
        if index is None or node is None:
            return rx.toast.error("That section is no longer available.")
        scope = SAMPLE_SCOPE if library.is_sample(doc_id) else self._token()
        self.source_title = index.title if node.id == "root" else node.title
        self.source_location = f"{index.title} · {node.location.label()}"
        self.source_summary = node.summary
        self.source_text = node_markdown(index, node, lambda record: image_url(scope, doc_id, record.file))
        self.source_pages, self.source_pages_note = self._section_pages(index, node, scope)
        self.source_tab = "text"
        self.source_open = True

    def _section_pages(self, index: DocumentIndex, node, scope: str) -> tuple[list[PageView], str]:
        """The pages of an uploaded PDF's section, for the "Pages" view; none for other documents."""
        start, end = node.location.pdf_page_start, node.location.pdf_page_end
        if index.kind != "pdf" or not index.page_labels or start is None or scope == SAMPLE_SCOPE:
            return [], ""
        end = start if end is None else end
        shown = range(start, min(end, start + MAX_SOURCE_PAGES - 1) + 1)
        pages = [PageView(image_url(scope, index.doc_id, f"pages/{page}.png"), index.page_labels[page]) for page in shown]
        total = end - start + 1
        return pages, (f"First {len(pages)} of {total} pages." if total > len(pages) else "")

    @rx.event
    def set_source_tab(self, value: str | list[str]):  # the segmented control may send a list (multi-select)
        if isinstance(value, list):
            value = value[0] if value else ""
        if value in ("text", "pages"):
            self.source_tab = value

    @rx.event
    def set_source_open(self, value: bool):
        self.source_open = value

    @rx.event
    def open_browse(self, doc_id: str):
        index = self._library().load(doc_id)
        if index is None:
            return rx.toast.error("That document is no longer available.")
        self.browse_title = index.title
        self.browse_structure = STRUCTURE_NOTES.get(index.structure_source or "", "")
        self.browse_rows = [
            OutlineRowView(row.ref, row.title, row.summary, f"{row.depth * 1.2}em") for row in outline_rows(index)
        ]
        self.browse_open = True

    @rx.event
    def set_browse_open(self, value: bool):
        self.browse_open = value

    @rx.event
    def set_listing_depth(self, value: str):
        if value in ("1", "2", "3"):
            self.listing_depth = value

    # ---- chat ----

    @rx.event
    def ask_example(self, question: str):
        if not self.key_set:
            return rx.toast.info("Add your API key first, then try a sample question.")
        if not self.agent_model:
            return rx.toast.info("Choose a model for chat (under your API key) first.")
        return State.ask({"question": question})

    @rx.event(background=True)
    async def ask(self, form_data: dict[str, Any]):
        question = str(form_data.get("question", "")).strip()
        if not question:
            return
        async with self:
            if self.thinking or not self.key_set or not self.agent_model:
                return
            llm, token = self._llm(), self._token()
            turned_off = set(self.disabled_docs)
            chat_settings = dataclasses.replace(settings, agent_listing_depth=int(self.listing_depth))
            history = [
                ChatTurn(role=m.role, content=m.text)
                for m in self.messages
                if m.text and (m.role == "user" or not m.error)
            ][-MAX_HISTORY_TURNS * 2 :]
            generation = self._chat_generation
            self.thinking = True
            self.live_steps = []
            self.live_text = ""
            self.messages = [*self.messages, ChatMessage(role="user", text=question)]
        yield scroll_chat(force=True)

        steps: list[StepView] = []  # this answer's steps, kept here too so the final message can carry them
        try:
            library = Library.for_session(settings, sessions, token)
            indexes = [index for index in library.indexes() if index.doc_id not in turned_off]
            if not indexes:
                raise EmptyLibrary
            events = stream_ask(indexes, question, chat_settings, history, llm_config=llm)
            last_draft = 0.0
            while (event := await asyncio.to_thread(next, events, None)) is not None:
                if isinstance(event, Step):
                    steps.append(StepView(event.label, event.note))
                    async with self:
                        if self._chat_generation != generation:
                            return
                        # Any draft text so far was the model's note before this step; the step carries it now.
                        self.live_steps = list(steps)
                        self.live_text = ""
                elif isinstance(event, Draft):
                    if time.monotonic() - last_draft < DRAFT_INTERVAL:
                        continue  # the final answer replaces the text anyway; skip updates that come too fast
                    last_draft = time.monotonic()
                    async with self:
                        if self._chat_generation != generation:
                            return
                        self.live_text = event.text
                else:
                    message = ChatMessage(role="assistant", steps=list(steps), **_answer_fields(event, library, token))
                    async with self:
                        self._finish_answer(generation, message)
                yield scroll_chat()
        except EmptyLibrary:
            async with self:
                self._finish_answer(
                    generation,
                    ChatMessage(
                        role="assistant",
                        text="There are no documents to search: every document is turned off, or none is indexed yet.",
                        found=False,
                        error=True,
                    ),
                )
        except Exception as exc:
            log.warning("Answering failed: %s", type(exc).__name__)  # the type only: the text could hold request details
            async with self:
                self._finish_answer(
                    generation,
                    ChatMessage(role="assistant", steps=list(steps), text=friendly_error(exc, llm.info.label), found=False, error=True),
                )
        finally:
            async with self:
                if self._chat_generation == generation and self.thinking:
                    self._finish_answer(generation, None)  # the agent produced nothing; just stop "thinking"
        yield scroll_chat()

    def _finish_answer(self, generation: int, message: ChatMessage | None) -> None:
        """Add the finished answer to the chat and clear the live one, in a single update so nothing flickers.

        Does nothing if the chat was cleared (End session) while this answer was being written.
        """
        if self._chat_generation != generation:
            return
        if message is not None:
            self.messages = [*self.messages, message]
        self.live_steps = []
        self.live_text = ""
        self.thinking = False


class EmptyLibrary(Exception):
    """There is nothing to search."""


def _estimate_key(options: IndexOptions) -> str:
    return f"{options.summaries}|{options.summary_length}|{options.describe_images}"


# Shown above a document's outline, so a flat outline explains itself.
STRUCTURE_NOTES = {
    "bookmarks": "Outline from the PDF's bookmarks.",
    "headings": "Outline from the document's headings.",
    "typed_toc": "Outline from the document's contents page (it has no headings).",
    "formatting": "Outline from lines formatted like headings (it has no heading styles).",
    "none": "No headings found in this document, so its text is split into parts by size.",
}


def _indexed_with_text(index: DocumentIndex) -> str:
    """How a saved index was made, for a visitor: "Budget · OpenAI gpt-6-luna", "Free"."""
    preset = matching_preset(index.options) if index.options else None
    how = PRESET_LABELS[preset] if preset else ("custom options" if index.options else "an earlier version of the app")
    used = index.indexed_with
    model = used.index_model or used.vision_model if used else ""
    return f"{how} · {PROVIDERS[used.provider].label} {model}" if used and model else how


def _cost_text(estimate: Estimate) -> str:
    if not estimate.calls:
        return "No model calls: free, and no API key needed."
    return (
        f"About {estimate.calls} model calls on your key (roughly {estimate.input_tokens:,} input and "
        f"{estimate.output_tokens:,} output tokens)."
    )


def _where(title: str, location: str) -> str:
    """The location without the section's own title, which the chip already shows.

    "Crew > Roster" -> "Crew" for the section Roster; "Crew > Captain, Deputy" -> "Captain, Deputy"
    for the section Crew that small sections were folded into.
    """
    return " > ".join(part for part in location.split(" > ") if part != title)


def _answer_fields(result: AnswerResult, library: Library, token: str) -> dict[str, Any]:
    """The parts of the assistant message that come from the final answer (the steps are already there)."""

    def scope(doc_id: str) -> str:
        return SAMPLE_SCOPE if library.is_sample(doc_id) else token

    return {
        "text": result.text,
        "found": result.found,
        "error": result.error,
        "citations": [
            CitationView(f"{c.doc_id}:{c.node_id}", c.doc_title, c.title, _where(c.title, c.location))
            for c in result.citations
        ],
        "images": [ImageView(image_url(scope(i.doc_id), i.doc_id, i.file), i.description) for i in result.images],
    }
