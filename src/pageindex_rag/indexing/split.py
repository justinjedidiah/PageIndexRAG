"""Split sections whose own text is too big to read in one go into generated parts.

Cuts happen at the best boundary available: paragraph, then sentence, then a hard cut. A big table
is cut between rows, with its header repeated in every piece. Parts are balanced, so there is no tiny
last piece, and an image stays with the paragraph next to it.
"""

import math
import re

from ..models import Node
from .blocks import IMAGE_REF, PAGE_REF, is_anchor_only

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
TABLE_SEPARATOR = re.compile(r"^\|\s*:?-{3,}")
TITLE_CHARS = 60


def estimate_tokens(text: str) -> int:
    return len(text) // 4


def split_large_nodes(node: Node, max_tokens: int) -> None:
    """Move oversized node text into generated child nodes, deepest nodes first.

    Generated children come before any existing children, matching where the text sat in the
    document. Each gets a title from its first sentence; LLM summaries give them better ones later.
    """
    for child in node.children:
        split_large_nodes(child, max_tokens)
    if estimate_tokens(node.text) <= max_tokens:
        return
    chunks = split_text(node.text, max_tokens)
    if len(chunks) < 2:
        return

    prefix = "" if node.id == "root" else f"{node.id}."
    generated = [
        Node(
            id=f"{prefix}p{i}",
            title=part_title(node.title, chunk, i, len(chunks)),
            level=node.level + 1,
            origin="generated",
            text=chunk,
            location=node.location.model_copy(deep=True),
        )
        for i, chunk in enumerate(chunks, start=1)
    ]
    node.text = ""
    node.children = [*generated, *node.children]


def split_text(text: str, max_tokens: int) -> list[str]:
    """Cut text into balanced chunks of at most about max_tokens each."""
    pieces: list[str] = []
    for paragraph in text.split("\n\n"):
        if estimate_tokens(paragraph) <= max_tokens:
            pieces.append(paragraph)
        elif is_table(paragraph):
            pieces.extend(_split_table(paragraph, max_tokens))
        else:
            pieces.extend(_split_paragraph(paragraph, max_tokens))
    return _pack(_attach_images(pieces, max_tokens), max_tokens, "\n\n")


def part_title(base: str, chunk: str, number: int, total: int) -> str:
    """A free title for a generated part: the parent's title and the part's first sentence."""
    text = PAGE_REF.sub("", IMAGE_REF.sub("", chunk)).strip()
    if is_table(text):
        header = [cell.strip() for cell in text.split("\n", 1)[0].strip("|").split("|") if cell.strip()]
        first = f"table ({', '.join(header)})" if header else "table"
    else:
        first = " ".join(SENTENCE_END.split(text, 1)[0].split()) if text else ""
    if len(first) > TITLE_CHARS:
        first = first[:TITLE_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return f"{base}: {first}" if first else f"{base} (part {number} of {total})"


def is_table(text: str) -> bool:
    lines = text.strip().split("\n")
    return len(lines) >= 2 and all(line.startswith("|") for line in lines) and bool(TABLE_SEPARATOR.match(lines[1]))


def _split_table(table: str, max_tokens: int) -> list[str]:
    lines = table.strip().split("\n")
    header = "\n".join(lines[:2])
    budget = max(1, max_tokens - estimate_tokens(header))
    rows: list[str] = []
    for row in lines[2:]:
        rows.extend([row] if estimate_tokens(row) <= budget else _hard_cut(row, budget))
    return [f"{header}\n{group}" for group in _pack(rows, budget, "\n")]


def _split_paragraph(paragraph: str, max_tokens: int) -> list[str]:
    sentences: list[str] = []
    for sentence in SENTENCE_END.split(paragraph):
        sentences.extend([sentence] if estimate_tokens(sentence) <= max_tokens else _hard_cut(sentence, max_tokens))
    return _pack(sentences, max_tokens, " ")


def _hard_cut(text: str, max_tokens: int) -> list[str]:
    """Last resort: cut at the limit, backing off to the last space when there is one."""
    size = max(1, max_tokens * 4)
    pieces = []
    while len(text) > size:
        cut = text.rfind(" ", 0, size)
        cut = cut if cut > size // 2 else size
        pieces.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        pieces.append(text)
    return pieces


def _attach_images(pieces: list[str], max_tokens: int) -> list[str]:
    """Keep a paragraph that is only image placeholders or page markers with the paragraph after it (or before, at the end)."""
    out: list[str] = []
    pending = ""
    for piece in pieces:
        if is_anchor_only(piece):
            pending = f"{pending}\n\n{piece}" if pending else piece
            continue
        if pending and estimate_tokens(pending) + estimate_tokens(piece) <= max_tokens:
            piece = f"{pending}\n\n{piece}"
        elif pending:
            out.append(pending)
        pending = ""
        out.append(piece)
    if pending:
        if out and estimate_tokens(out[-1]) + estimate_tokens(pending) <= max_tokens:
            out[-1] = f"{out[-1]}\n\n{pending}"
        else:
            out.append(pending)
    return out


def _pack(pieces: list[str], max_tokens: int, joiner: str) -> list[str]:
    """Group pieces (each within the limit) into as few chunks as the limit needs, of roughly equal size."""
    sizes = [estimate_tokens(piece) for piece in pieces]
    count = max(1, math.ceil(sum(sizes) / max_tokens))
    target = sum(sizes) / count
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for piece, tokens in zip(pieces, sizes):
        over_limit = size + tokens > max_tokens
        # Past the target, close the chunk if that lands nearer the target than adding this piece would.
        past_target = size + tokens > target and (size + tokens - target) > (target - size)
        if current and (over_limit or (past_target and len(chunks) < count - 1)):
            chunks.append(joiner.join(current))
            current, size = [], 0
        current.append(piece)
        size += tokens
    if current:
        chunks.append(joiner.join(current))
    return chunks
