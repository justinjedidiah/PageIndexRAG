"""Page ranges for PDF sections, from the page markers left in node text while the tree was built.

Markers survive every change refine.py makes (merge, fold, split), since those only move text around
in document order. So one walk in document order, carrying the current page along, gives each node
the pages its content spans. The markers are then removed: nothing after this point sees them.
"""

import re

from ..models import Node
from .blocks import PAGE_REF

_MARKER = re.compile(r"\[page:\d+\][ \t]*\n?\n?")
_BLANK_LINES = re.compile(r"\n{3,}")


def resolve_pages(root: Node, labels: list[str]) -> None:
    """Set each node's PDF page range (index and printed label) and strip the markers from its text."""
    current = 0

    def visit(node: Node) -> tuple[int, int]:
        nonlocal current
        start: int | None = None
        end: int | None = None
        pieces = PAGE_REF.split(node.text)  # text, page, text, page, ..., text
        for i, piece in enumerate(pieces):
            if i % 2:  # a page marker: where the section starts if nothing came before it
                current = int(piece)
                if start is None:
                    start = current
            elif piece.strip():
                start = current if start is None else start
                end = current
        for child in node.children:
            child_start, child_end = visit(child)
            start = child_start if start is None else start
            end = child_end
        start = current if start is None else start
        end = max(start, current if end is None else end)
        _set_pages(node, start, end, labels)
        node.text = strip_page_markers(node.text)
        return start, end

    visit(root)


def strip_page_markers(text: str) -> str:
    if "[page:" not in text:
        return text
    return _BLANK_LINES.sub("\n\n", _MARKER.sub("", text)).strip()


def _set_pages(node: Node, start: int, end: int, labels: list[str]) -> None:
    location = node.location
    location.pdf_page_start, location.pdf_page_end = start, end
    location.printed_page_start = labels[start] if start < len(labels) else str(start + 1)
    location.printed_page_end = labels[end] if end < len(labels) else str(end + 1)
