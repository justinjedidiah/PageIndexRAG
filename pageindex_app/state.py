"""Reflex state: thin glue between the page and the pageindex_rag package.

Helper methods start with an underscore so Reflex does not turn them into events.
"""

import asyncio
import dataclasses
import logging
import time
from pathlib import Path
from typing import Any

import reflex as rx
from pageindex_rag.agent import AnswerResult, ChatTurn, Draft, Step, stream_ask
from pageindex_rag.errors import friendly_error
from pageindex_rag.indexing import build_index, enrich_index, estimate_index
from pageindex_rag.indexing.pipeline import make_doc_id
from pageindex_rag.library import SAMPLE_SCOPE, Library
from pageindex_rag.llm import validate_key
from pageindex_rag.models import DocumentIndex
from pageindex_rag.outline import node_markdown, outline_rows
from pageindex_rag.sessions import describe_duration
from pageindex_rag.uploads import UploadError, safe_filename, validate_upload

from .runtime import sessions, settings
from .viewmodels import ChatMessage, CitationView, DocView, ImageView, OutlineRowView, StepView

log = logging.getLogger(__name__)

UPLOAD_ID = "doc_upload"
MAX_HISTORY_TURNS = 6  # earlier question/answer pairs the agent gets as context
DRAFT_INTERVAL = 0.08  # seconds between streamed-text updates sent to the browser


def image_url(scope: str, doc_id: str, relative: str) -> str:
    return f"{rx.config.get_config().api_url}/api/files/{scope}/{doc_id}/{relative}"


def pending_path(token: str) -> Path:
    return sessions.session_dir(token) / "pending.json"


def scroll_chat() -> rx.event.EventSpec:
    return rx.call_script("document.getElementById('chat-end')?.scrollIntoView({behavior: 'smooth', block: 'end'})")


class State(rx.State):
    # The visitor's Anthropic key. A backend variable: it is never sent to the browser,
    # and nothing here writes it to disk or to a log.
    _api_key: str = ""
    key_set: bool = False
    key_error: str = ""
    key_checking: bool = False

    docs: list[DocView] = []
    has_samples: bool = False
    session_note: str = ""

    messages: list[ChatMessage] = []
    thinking: bool = False

    upload_error: str = ""
    building: bool = False
    # The unsummarized index waits on disk (pending.json in the session folder), not in state:
    # Reflex wraps objects held in state in proxies that worker threads must not mutate.
    _pending_id: str = ""
    _pending_file: str = ""
    has_pending: bool = False
    pending_name: str = ""
    pending_detail: str = ""
    indexing: bool = False
    index_done: int = 0
    index_total: int = 0
    index_label: str = ""

    source_open: bool = False
    source_title: str = ""
    source_location: str = ""
    source_summary: str = ""
    source_text: str = ""

    browse_open: bool = False
    browse_title: str = ""
    browse_rows: list[OutlineRowView] = []

    # ---- derived ----

    @rx.var
    def can_chat(self) -> bool:
        return self.key_set and bool(self.docs) and not self.thinking

    @rx.var
    def index_percent(self) -> int:
        return int(100 * self.index_done / self.index_total) if self.index_total else 0

    @rx.var
    def own_doc_count(self) -> int:
        return sum(1 for doc in self.docs if not doc.sample)

    # ---- helpers ----

    def _token(self) -> str:
        return self.router.session.client_token

    def _library(self) -> Library:
        return Library.for_session(settings, sessions, self._token() or None)

    def _refresh(self) -> None:
        docs = self._library().documents()
        self.docs = [DocView(d.doc_id, d.title, d.kind, d.summary, d.sample) for d in docs]
        self.has_samples = any(d.sample for d in docs)
        session = sessions.get(self._token())
        if session:
            self.session_note = f"Your uploads are deleted automatically in {describe_duration(session.expires_at - time.time())}."
        else:
            hours = describe_duration(settings.session_ttl_seconds)
            self.session_note = f"Your uploads are deleted automatically {hours} after you add the first one."

    def _clear_pending(self, delete_files: bool) -> None:
        if delete_files and self._pending_id:
            token = self._token()
            sessions.store(token).delete_document(self._pending_id)
            (sessions.uploads_dir(token) / self._pending_file).unlink(missing_ok=True)
            pending_path(token).unlink(missing_ok=True)
        self._pending_id = ""
        self._pending_file = ""
        self.has_pending = False
        self.pending_name = ""
        self.pending_detail = ""

    def _replace_last(self, **changes: Any) -> None:
        self.messages = [*self.messages[:-1], dataclasses.replace(self.messages[-1], **changes)]

    # ---- page load and session ----

    @rx.event
    def on_load(self):
        self._refresh()

    @rx.event
    def end_session(self):
        """Delete everything this visitor added, and forget their key."""
        sessions.delete(self._token())
        self._api_key = ""
        self.key_set = False
        self.key_error = ""
        self.messages = []
        self.thinking = False
        self.upload_error = ""
        self.indexing = False
        self._clear_pending(delete_files=False)
        self._refresh()
        return rx.toast.info("Session ended. Your uploads and chat were deleted.")

    # ---- API key ----

    @rx.event
    async def save_key(self, form_data: dict[str, Any]):
        key = str(form_data.get("api_key", "")).strip()
        self.key_error = ""
        if not key:
            self.key_error = "Paste your Anthropic API key first."
            return
        self.key_checking = True
        yield
        try:
            await asyncio.to_thread(validate_key, key, settings)
        except Exception as exc:  # the message shown never contains the exception text
            self.key_error = friendly_error(exc)
        else:
            self._api_key = key
            self.key_set = True
        finally:
            self.key_checking = False

    @rx.event
    def clear_key(self):
        self._api_key = ""
        self.key_set = False

    # ---- documents ----

    @rx.event
    async def handle_upload(self, files: list[rx.UploadFile]):
        self.upload_error = ""
        if not self.key_set:
            self.upload_error = "Add your Anthropic API key first. Indexing uses it."
            return
        if self.building or self.indexing or self.has_pending:
            return
        if not files:
            self.upload_error = "Choose a file first."
            return

        upload = files[0]
        name = safe_filename(upload.filename or "upload")
        data = await upload.read(settings.max_upload_bytes + 1)  # never read more than the limit allows
        try:
            kind = validate_upload(name, data, settings.max_upload_bytes)
        except UploadError as exc:
            self.upload_error = str(exc)
            yield rx.clear_selected_files(UPLOAD_ID)
            return
        if kind == "pdf":
            self.upload_error = "PDF indexing is not available yet. Word (.docx) files work."
            yield rx.clear_selected_files(UPLOAD_ID)
            return

        library = self._library()
        if self.own_doc_count >= settings.max_session_docs:
            self.upload_error = f"You can keep up to {settings.max_session_docs} documents in a session. Delete one first."
            return

        self.building = True
        yield
        token = self._token()
        try:
            sessions.ensure(token)
            directory = sessions.uploads_dir(token)
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / name
            path.write_bytes(data)
            if library.load(make_doc_id(path)):
                path.unlink(missing_ok=True)
                self.upload_error = "That document is already in your library."
                return
            store = sessions.store(token)
            index = await asyncio.to_thread(build_index, path, store, settings)
            estimate = estimate_index(index, store)
        except Exception:
            log.exception("Reading an uploaded document failed")
            self.upload_error = "Could not read that document. It may be damaged or use unsupported features."
            return
        finally:
            self.building = False

        pending_path(token).write_text(index.model_dump_json(), encoding="utf-8")
        self._pending_id = index.doc_id
        self._pending_file = name
        self.has_pending = True
        self.pending_name = index.title
        self.pending_detail = (
            f"{estimate.sections} sections and {estimate.images} images to describe. Indexing makes about "
            f"{estimate.calls} Claude calls on your key (roughly {estimate.input_tokens:,} input and "
            f"{estimate.output_tokens:,} output tokens)."
        )
        self._refresh()
        yield rx.clear_selected_files(UPLOAD_ID)

    @rx.event
    def cancel_pending(self):
        self._clear_pending(delete_files=True)
        self._refresh()

    @rx.event(background=True)
    async def confirm_index(self):
        async with self:
            if not self._pending_id or self.indexing:
                return
            key, token = self._api_key, self._token()
            index = DocumentIndex.model_validate_json(pending_path(token).read_text(encoding="utf-8"))
            total = estimate_index(index, sessions.store(token)).calls
            self.indexing = True
            self.upload_error = ""
            self.index_done = 0
            self.index_total = total
            self.index_label = "Starting"

        store = sessions.store(token)
        progress = {"done": 0, "total": total, "label": "Starting"}

        def on_progress(done: int, total: int, label: str) -> None:  # runs on worker threads
            progress.update(done=done, total=total, label=label)

        work = asyncio.create_task(asyncio.to_thread(enrich_index, index, store, settings, key, on_progress))
        error = ""
        try:
            while not work.done():
                await asyncio.wait({work}, timeout=0.4)
                async with self:
                    self.index_done, self.index_total, self.index_label = progress["done"], progress["total"], progress["label"]
            work.result()  # raises if an LLM call failed
            await asyncio.to_thread(store.save_index, index)
        except Exception as exc:
            error = friendly_error(exc)
            if not isinstance(exc, (OSError, ValueError)):
                log.warning("Indexing failed: %s", type(exc).__name__)

        async with self:
            self.indexing = False
            if error:
                self.upload_error = f"Indexing failed. {error}"
                self._clear_pending(delete_files=True)
            else:
                pending_path(token).unlink(missing_ok=True)
                self._clear_pending(delete_files=False)
            self._refresh()
        if not error:
            yield rx.toast.success(f"Indexed {index.title}. You can ask about it now.")

    @rx.event
    def delete_doc(self, doc_id: str):
        library = self._library()
        if library.is_sample(doc_id) or not self._token():
            return
        sessions.store(self._token()).delete_document(doc_id)
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
        self.source_open = True

    @rx.event
    def set_source_open(self, value: bool):
        self.source_open = value

    @rx.event
    def open_browse(self, doc_id: str):
        index = self._library().load(doc_id)
        if index is None:
            return rx.toast.error("That document is no longer available.")
        self.browse_title = index.title
        self.browse_rows = [
            OutlineRowView(row.ref, row.title, row.summary, f"{row.depth * 1.2}em") for row in outline_rows(index)
        ]
        self.browse_open = True

    @rx.event
    def set_browse_open(self, value: bool):
        self.browse_open = value

    # ---- chat ----

    @rx.event
    def ask_example(self, question: str):
        return State.ask({"question": question})

    @rx.event(background=True)
    async def ask(self, form_data: dict[str, Any]):
        question = str(form_data.get("question", "")).strip()
        if not question:
            return
        async with self:
            if self.thinking or not self.key_set:
                return
            key, token = self._api_key, self._token()
            history = [
                ChatTurn(role=m.role, content=m.text)
                for m in self.messages
                if m.text and not m.pending and (m.role == "user" or m.found)
            ][-MAX_HISTORY_TURNS * 2 :]
            self.thinking = True
            self.messages = [*self.messages, ChatMessage(role="user", text=question), ChatMessage(role="assistant", pending=True)]
        yield scroll_chat()

        try:
            library = Library.for_session(settings, sessions, token)
            indexes = library.indexes()
            if not indexes:
                raise EmptyLibrary
            events = stream_ask(indexes, question, settings, history, api_key=key)
            last_draft = 0.0
            while (event := await asyncio.to_thread(next, events, None)) is not None:
                if isinstance(event, Step):
                    async with self:
                        # Any draft text so far was the model's note before this step; the step carries it now.
                        self._replace_last(steps=[*self.messages[-1].steps, StepView(event.label, event.note)], text="")
                elif isinstance(event, Draft):
                    if time.monotonic() - last_draft < DRAFT_INTERVAL:
                        continue  # the final answer replaces the text anyway; skip updates that come too fast
                    last_draft = time.monotonic()
                    async with self:
                        self._replace_last(text=event.text)
                else:
                    fields = _answer_fields(event, library, token)
                    async with self:
                        self._replace_last(**fields)
                yield scroll_chat()
        except EmptyLibrary:
            async with self:
                self._replace_last(text="There are no documents to search yet.", found=False, pending=False)
        except Exception as exc:
            if not isinstance(exc, (OSError, ValueError)):
                log.warning("Answering failed: %s", type(exc).__name__)
            async with self:
                self._replace_last(text=friendly_error(exc), found=False, pending=False)
        finally:
            async with self:
                self.thinking = False
                if self.messages and self.messages[-1].pending:
                    self._replace_last(pending=False)
        yield scroll_chat()


class EmptyLibrary(Exception):
    """There is nothing to search."""


def _where(title: str, location: str) -> str:
    """The location without the section's own title, which the chip already shows ("Crew > Roster" -> "Crew")."""
    if location == title:
        return ""
    return location.removesuffix(f" > {title}")


def _answer_fields(result: AnswerResult, library: Library, token: str) -> dict[str, Any]:
    """The parts of the assistant message that come from the final answer (the steps are already there)."""

    def scope(doc_id: str) -> str:
        return SAMPLE_SCOPE if library.is_sample(doc_id) else token

    return {
        "text": result.text,
        "found": result.found,
        "pending": False,
        "citations": [
            CitationView(f"{c.doc_id}:{c.node_id}", c.doc_title, c.title, _where(c.title, c.location))
            for c in result.citations
        ],
        "images": [ImageView(image_url(scope(i.doc_id), i.doc_id, i.file), i.description) for i in result.images],
    }
