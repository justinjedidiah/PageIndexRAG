"""Free previews: a section's first sentences stand in for an LLM summary when summaries are off."""

from ..models import Node
from .blocks import IMAGE_REF
from .split import SENTENCE_END, is_table

MAX_PREVIEW_CHARS = 300
MAX_COVERS_CHARS = 200


def fill_previews(root: Node, sentences: int = 3) -> None:
    """Set every node's summary to a preview of its text, plus the titles of its subsections."""
    for node in root.walk():
        node.summary = make_preview(node, sentences)
        node.summary_source = "preview"


def make_preview(node: Node, sentences: int = 3) -> str:
    own = _first_sentences(node.text, sentences)
    if not node.children:
        return own
    covers = _shorten(f"Covers: {', '.join(child.title for child in node.children)}.", MAX_COVERS_CHARS)
    return f"{own} {covers}" if own else covers


def _first_sentences(text: str, count: int) -> str:
    paragraphs = []
    for paragraph in IMAGE_REF.sub("", text).split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if is_table(paragraph):
            header = paragraph.split("\n", 1)[0].strip("| ").replace(" | ", ", ")
            paragraphs.append(f"[Table: {header}]")
        else:
            paragraphs.append(" ".join(paragraph.split()))
    flat = " ".join(paragraphs)
    return _shorten(" ".join(SENTENCE_END.split(flat)[:count]), MAX_PREVIEW_CHARS) if flat else ""


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
