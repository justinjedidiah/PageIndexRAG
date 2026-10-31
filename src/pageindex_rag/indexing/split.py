"""Split nodes whose own text is too large into generated sub-nodes."""

from ..models import Node


def estimate_tokens(text: str) -> int:
    return len(text) // 4


def split_large_nodes(node: Node, max_tokens: int) -> None:
    """Move oversized node text into generated child nodes, on paragraph boundaries.

    Generated children come before any existing children, matching where the
    text sat in the document. The summarizer gives them real titles later.
    """
    for child in node.children:
        split_large_nodes(child, max_tokens)
    if estimate_tokens(node.text) <= max_tokens:
        return

    chunks = _chunk_paragraphs(node.text, max_tokens)
    prefix = "" if node.id == "root" else f"{node.id}."
    generated = [
        Node(
            id=f"{prefix}p{i}",
            title=f"{node.title} (part {i} of {len(chunks)})",
            level=node.level + 1,
            origin="generated",
            text=chunk,
            location=node.location.model_copy(deep=True),
        )
        for i, chunk in enumerate(chunks, start=1)
    ]
    node.text = ""
    node.children = [*generated, *node.children]


def _chunk_paragraphs(text: str, max_tokens: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for paragraph in text.split("\n\n"):
        tokens = estimate_tokens(paragraph)
        if current and size + tokens > max_tokens:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(paragraph)
        size += tokens
    if current:
        chunks.append("\n\n".join(current))
    return chunks
