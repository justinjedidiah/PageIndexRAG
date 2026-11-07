"""Indexing in two stages, so a visitor can see the cost before any LLM call is made.

build_index: extract, build the tree, refine it (merge/fold/split), save images, fill free previews. No LLM, no cost.
enrich_index: image descriptions and section summaries, as the options ask. These are the LLM calls.
"""

import hashlib
import re
import threading
from collections.abc import Callable
from pathlib import Path

from ..config import Settings
from ..providers import LLMConfig
from ..models import DocumentIndex, FileKind, ImageRecord, IndexOptions, TocEntry
from ..storage import Store
from .blocks import IMAGE_REF, Block, Heading
from .docx_extract import extract_docx
from .estimate import estimate_index
from .markdown_extract import extract_markdown
from .options import preset_options
from .preview import fill_previews
from .refine import refine_tree
from .tree import build_tree

OnProgress = Callable[[int, int, str], None]  # done, total, what just finished

_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "emf": "image/emf",
    "wmf": "image/wmf",
}


def build_index(
    path: Path | str, store: Store, settings: Settings, options: IndexOptions | None = None
) -> DocumentIndex:
    """Extract structure and content. Images are written to the store; nothing is saved as an index yet.

    Without options, the default preset is used.
    """
    path = Path(path)
    options = options or preset_options(settings=settings)
    kind, blocks = _extract(path)
    doc_id = make_doc_id(path)

    root, raw_images = build_tree(blocks, title=path.stem, kind=kind)
    source_toc = [TocEntry(title=b.title, level=b.level) for b in blocks if isinstance(b, Heading)] or None
    refine_tree(root, options)
    if options.summaries == "preview":
        fill_previews(root, options.preview_sentences)

    images = [
        ImageRecord(
            id=image_id,
            file=store.save_image(doc_id, image_id, image.data, image.ext),
            mime=_MIME_TYPES.get(image.ext, f"image/{image.ext}"),
            description=image.alt,
        )
        for image_id, image in raw_images
    ]
    index = DocumentIndex(
        doc_id=doc_id,
        title=path.stem,
        source_path=path.name,
        kind=kind,
        source_toc=source_toc,
        root=root,
        images=images,
        summary=root.summary,
        options=options,
    )
    _assign_image_nodes(index)
    return index


def enrich_index(
    index: DocumentIndex,
    store: Store,
    settings: Settings,
    api_key: str | None = None,
    on_progress: OnProgress | None = None,
    options: IndexOptions | None = None,
    cancel: threading.Event | None = None,
    llm: LLMConfig | None = None,
) -> None:
    """Describe images and summarize every section, in place, as far as the options ask. Raises if an LLM call fails.

    `llm` is the provider, key and models to use; without it they come from settings plus `api_key`.
    Options default to the ones the index was built with. Setting `cancel` (from any thread) stops the
    work: calls already sent finish, no new one starts, and IndexingCancelled is raised.
    """
    from ..llm import chat_model, llm_config
    from .summarize import SUMMARY_MAX_TOKENS, check_cancelled, describe_images, summarize_tree

    llm = llm or llm_config(settings, api_key)
    options = options or index.options or preset_options(settings=settings)
    index.options = options
    total = estimate_index(index, store, options).calls
    done = 0
    lock = threading.Lock()  # steps finish on worker threads

    def on_step(label: str) -> None:
        nonlocal done
        with lock:
            done += 1
            if on_progress:
                on_progress(done, total, label)

    if options.describe_images:
        describe_images(
            index.images,
            store.doc_dir(index.doc_id),
            chat_model(llm.vision_model, llm.api_key, provider=llm.provider),
            settings.index_workers,
            on_step,
            cancel,
        )
    check_cancelled(cancel)
    if options.summaries == "llm":
        summarize_tree(
            index.root,
            chat_model(
                llm.index_model,
                llm.api_key,
                max_tokens=llm.output_cap(SUMMARY_MAX_TOKENS[options.summary_length]),
                provider=llm.provider,
            ),
            index.images,
            settings.index_workers,
            on_step,
            length=options.summary_length,
            cancel=cancel,
        )
        index.summary = index.root.summary


def index_document(
    path: Path | str,
    store: Store,
    settings: Settings,
    use_llm: bool = True,
    api_key: str | None = None,
    on_progress: OnProgress | None = None,
    options: IndexOptions | None = None,
) -> DocumentIndex:
    """Index one file and save it.

    With use_llm=False, no LLM call is made: sections get free previews instead of summaries.
    """
    options = options or preset_options(settings=settings)
    if not use_llm:
        options = options.model_copy(update={"summaries": "preview", "describe_images": False})
    index = build_index(path, store, settings, options)
    if options.uses_llm:
        enrich_index(index, store, settings, api_key, on_progress, options)
    store.save_index(index)
    return index


def make_doc_id(path: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")[:60] or "doc"
    digest = hashlib.sha1(path.read_bytes()).hexdigest()[:8]
    return f"{slug}-{digest}"


def _extract(path: Path) -> tuple[FileKind, list[Block]]:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return "docx", extract_docx(path)
    if suffix in (".md", ".markdown"):
        return "markdown", extract_markdown(path)
    if suffix == ".pdf":
        raise NotImplementedError("PDF support is stage 3 (see DESIGN.md).")
    raise ValueError(f"Unsupported file type: {suffix}")


def _assign_image_nodes(index: DocumentIndex) -> None:
    """Point each image at the node whose text holds its placeholder (after splitting)."""
    records = {record.id: record for record in index.images}
    for node in index.root.walk():
        for image_id in IMAGE_REF.findall(node.text):
            if image_id in records:
                records[image_id].node_id = node.id
                records[image_id].location = node.location
