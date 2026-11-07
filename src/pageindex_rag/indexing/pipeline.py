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
from ..models import DocumentIndex, FileKind, ImageRecord, IndexedWith, IndexOptions, TocEntry
from ..storage import Store
from .blocks import IMAGE_REF, Block, DocumentRejected, Heading, Paragraph, Table, TocLine
from .docx_extract import extract_docx
from .pages import resolve_pages
from .pdf_cache import read_pdf
from .estimate import estimate_index
from .markdown_extract import extract_markdown
from .options import preset_options
from .preview import fill_previews
from .refine import refine_tree
from .split import estimate_tokens
from .tree import build_tree
from .heading_looks import apply_heading_looks, apply_section_looks
from .typed_toc import apply_typed_toc

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

    Without options, the default preset is used. Raises DocumentRejected for a document this app
    will not index (its message is safe to show).
    """
    path = Path(path)
    options = options or preset_options(settings=settings)
    kind, blocks, page_labels, bookmarks = _extract(path, settings)
    _check_length(blocks, settings.max_doc_tokens)
    # Where the outline comes from, in order: a PDF's bookmarks or the headings, else a typed contents page,
    # else lines formatted like headings, else nothing (the text is then cut by size). The first that finds one wins.
    has_headings = any(isinstance(b, Heading) for b in blocks)
    # A typed contents page: removed, and used if no headings (in a PDF, checked against its page numbers).
    blocks, from_contents_page = apply_typed_toc(blocks, page_labels)
    from_looks = False
    if not has_headings and not from_contents_page:
        blocks, from_looks = apply_heading_looks(blocks)
    headings = [b for b in blocks if isinstance(b, Heading)]
    # The document's own ToC as reference data: its table of contents if it has one, else its headings.
    source_toc = bookmarks or [TocEntry(title=b.title, level=b.level, page=b.page) for b in blocks if isinstance(b, TocLine)] or [
        TocEntry(title=b.title, level=b.level) for b in headings
    ] or None
    if options.split_large and (has_headings or from_contents_page):
        # A section too big to read in one go: look for its own sub-headings before it is cut by size.
        blocks = apply_section_looks(blocks, options.max_leaf_tokens)
    doc_id = make_doc_id(path)

    root, raw_images = build_tree(blocks, title=path.stem, kind=kind)
    refine_tree(root, options)
    if page_labels is not None:
        resolve_pages(root, page_labels)  # page markers become page ranges, and leave the text
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
        structure_source=(
            "bookmarks" if bookmarks else "typed_toc" if from_contents_page else "formatting" if from_looks else "headings" if headings else "none"
        ),
        root=root,
        images=images,
        summary=root.summary,
        options=options,
    )
    _assign_image_nodes(index)
    if page_labels is not None:
        _cite_image_pages(index, {image_id: image.page for image_id, image in raw_images}, page_labels)
    return index


def _cite_image_pages(index: DocumentIndex, pages: dict[str, int | None], labels: list[str]) -> None:
    """A PDF figure is cited by its own page, not its section's whole range."""
    for record in index.images:
        page = pages.get(record.id)
        if page is None or record.location is None:
            continue
        label = labels[page] if page < len(labels) else str(page + 1)
        record.location = record.location.model_copy(
            update={"pdf_page_start": page, "pdf_page_end": page, "printed_page_start": label, "printed_page_end": label}
        )


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
    index.indexed_with = IndexedWith(
        provider=llm.provider,
        index_model=llm.index_model if options.summaries == "llm" else "",
        vision_model=llm.vision_model if options.describe_images else "",
    ) if options.uses_llm else None
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
    return doc_id_for(path.stem, path.read_bytes())


def doc_id_for(stem: str, data: bytes) -> str:
    """A document's id: its name plus a fingerprint of its content, so the same file always gets the same id."""
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:60] or "doc"
    digest = hashlib.sha1(data).hexdigest()[:8]
    return f"{slug}-{digest}"


def _extract(path: Path, settings: Settings) -> tuple[FileKind, list[Block], list[str] | None, list[TocEntry] | None]:
    """The blocks, plus for a PDF its printed page labels and the bookmarks used as its outline (if any)."""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return "docx", extract_docx(path), None, None
    if suffix in (".md", ".markdown"):
        return "markdown", extract_markdown(path), None, None
    if suffix == ".pdf":
        content = read_pdf(path, settings.max_pdf_pages)
        return "pdf", content.blocks, content.page_labels, content.outline
    raise ValueError(f"Unsupported file type: {suffix}")


def _check_length(blocks: list[Block], max_tokens: int) -> None:
    """Refuse a document with more text than this app indexes (any file type)."""
    tokens = sum(
        estimate_tokens(b.text if isinstance(b, Paragraph) else b.markdown if isinstance(b, Table) else b.title)
        for b in blocks
        if isinstance(b, (Paragraph, Table, Heading))
    )
    if tokens > max_tokens:
        words = lambda n: f"{round(n * 0.75, -3):,.0f}"  # noqa: E731 - about 0.75 words per token
        raise DocumentRejected(
            f"This document is too long to index here: about {words(tokens)} words, and the limit is about {words(max_tokens)}."
        )


def _assign_image_nodes(index: DocumentIndex) -> None:
    """Point each image at the node whose text holds its placeholder (after splitting)."""
    records = {record.id: record for record in index.images}
    for node in index.root.walk():
        for image_id in IMAGE_REF.findall(node.text):
            if image_id in records:
                records[image_id].node_id = node.id
                records[image_id].location = node.location
