"""Display helpers: a document's outline, and a section's content as markdown."""

import re
from collections.abc import Callable
from dataclasses import dataclass

from .indexing.blocks import IMAGE_REF
from .models import DocumentIndex, ImageRecord, Node


@dataclass(frozen=True)
class OutlineRow:
    ref: str  # "doc_id:node_id", the same form the agent cites
    title: str
    summary: str
    depth: int  # 0 for top-level sections


def outline_rows(index: DocumentIndex) -> list[OutlineRow]:
    rows: list[OutlineRow] = []

    def visit(node: Node, depth: int) -> None:
        for child in node.children:
            rows.append(OutlineRow(f"{index.doc_id}:{child.id}", child.title, child.summary, depth))
            visit(child, depth + 1)

    visit(index.root, 0)
    return rows


def node_markdown(index: DocumentIndex, node: Node, image_url: Callable[[ImageRecord], str]) -> str:
    """The section's own text with image placeholders turned into images.

    A section that only holds subsections shows them as a list instead of an empty page.
    """
    records = {record.id: record for record in index.images}

    def image(match: re.Match) -> str:
        record = records.get(match.group(1))
        if record is None:
            return ""
        alt = re.sub(r"[\[\]\s]+", " ", record.description).strip()[:200] or "image"
        return f"![{alt}]({image_url(record)})"

    text = IMAGE_REF.sub(image, node.text)
    if not text.strip() and node.children:
        items = "\n".join(f"- **{child.title}**: {child.summary or 'no summary'}" for child in node.children)
        return f"_This section is divided into subsections:_\n\n{items}"
    return text
