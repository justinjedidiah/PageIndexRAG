"""What was read from a PDF, saved next to it, so changing an option does not read the PDF again.

Reading is the slow part (about a tenth of a second a page), and each change of a structure option
rebuilds the tree from the same file. The result is saved as `<file>.read.json` in the same folder as
the PDF: for an upload, the visitor's own session folder, so it is deleted with the session like
everything else of theirs. Nothing is kept in shared memory.

It is only used for the very file it sits next to, and only if a fingerprint of that file's bytes
(and the page limit) matches the one saved with it, so a changed file is always read again.
"""

import base64
import hashlib
import json
from pathlib import Path

from ..models import TocEntry
from .blocks import Block, Heading, Image, Paragraph, Table, TocLine
from .pdf_extract import PdfContent, extract_pdf

FORMAT = 1  # bump when the saved shape or the reading changes, so old files are read again


def saved_read_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.read.json")


def read_pdf(path: Path, max_pages: int | None) -> PdfContent:
    """The PDF's content: from the saved read when it matches this exact file, else read now and saved."""
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    saved = saved_read_path(path)
    if saved.is_file():
        try:
            data = json.loads(saved.read_text(encoding="utf-8"))
            if (data.get("format"), data.get("sha256"), data.get("max_pages")) == (FORMAT, fingerprint, max_pages):
                return _load(data)
        except (ValueError, KeyError, TypeError):
            pass  # unreadable: read the PDF again
    content = extract_pdf(path, max_pages)
    data = {"format": FORMAT, "sha256": fingerprint, "max_pages": max_pages, **_dump(content)}
    saved.write_text(json.dumps(data), encoding="utf-8")
    return content


def forget(path: Path) -> None:
    """Delete the saved read of this file, if any (the upload is done with or was refused)."""
    saved_read_path(path).unlink(missing_ok=True)


def _dump(content: PdfContent) -> dict:
    return {
        "page_labels": content.page_labels,
        "outline": [entry.model_dump() for entry in content.outline] if content.outline is not None else None,
        "blocks": [_dump_block(block) for block in content.blocks],
    }


def _dump_block(block: Block) -> dict:
    if isinstance(block, Paragraph):
        return {"type": "paragraph", "text": block.text, "indent": block.indent, "bold": block.bold,
                "size": block.size, "listed": block.listed, "page": block.page}
    if isinstance(block, Heading):
        return {"type": "heading", "level": block.level, "title": block.title, "page": block.page}
    if isinstance(block, Table):
        return {"type": "table", "markdown": block.markdown, "page": block.page}
    if isinstance(block, Image):
        return {"type": "image", "data": base64.b64encode(block.data).decode("ascii"), "ext": block.ext,
                "alt": block.alt, "page": block.page}
    return {"type": "toc", "level": block.level, "title": block.title, "page": block.page}


def _load(data: dict) -> PdfContent:
    return PdfContent(
        blocks=[_load_block(item) for item in data["blocks"]],
        page_labels=list(data["page_labels"]),
        outline=[TocEntry(**entry) for entry in data["outline"]] if data["outline"] is not None else None,
    )


def _load_block(item: dict) -> Block:
    kind = item["type"]
    if kind == "paragraph":
        return Paragraph(item["text"], indent=item["indent"], bold=item["bold"], size=item["size"],
                         listed=item["listed"], page=item["page"])
    if kind == "heading":
        return Heading(item["level"], item["title"], page=item["page"])
    if kind == "table":
        return Table(item["markdown"], page=item["page"])
    if kind == "image":
        return Image(base64.b64decode(item["data"]), item["ext"], alt=item["alt"], page=item["page"])
    if kind == "toc":
        return TocLine(item["level"], item["title"], item["page"])
    raise ValueError(f"unknown block type {kind!r}")
