"""Headings formatted by hand: lines that look like headings, in a document with no heading styles.

Only used when a document has no headings and no trustworthy contents page. Works on extracted
blocks, using how each paragraph looks (bold, size) and its text:

1. Candidate lines: short, not ending like a sentence (a question mark or colon is fine), and
   standing out: all bold, bigger than the document's normal text, numbered like a section
   ("1.2", "Chapter 3", "A."), or in capitals. Not captions ("Figure 3"), not Word list items.
2. Group them by look (size, bold, capitals, question or not).
3. Keep only looks that recur: at least 3 lines (2 in a short document) with body text between
   them. Headings are a pattern that repeats through a document; a pull quote, a warning or one
   big title at the top is not, and stays text.
4. If the kept lines hold a large share of the document's text, or are most of its paragraphs, the
   document just uses a lot of bold: give up (the text is then cut by size). Measured by text, not
   by paragraphs: in an FAQ (a question, then a one-paragraph answer) half the paragraphs are headings,
   yet they are a small part of the text.
5. Levels from numbering if most are numbered, else by look (biggest first), at most 3 levels.
"""

import re
from collections import Counter
from dataclasses import dataclass

from .blocks import Block, Heading, Paragraph

MAX_WORDS = 12
MAX_CHARS = 100
BIGGER = 1.15  # at least this many times the normal text size counts as bigger
MIN_REPEATS = 3
MIN_REPEATS_SHORT = 2  # in a document with fewer than SHORT_DOCUMENT paragraphs
SHORT_DOCUMENT = 30
MAX_IN_A_ROW = 3  # more lines of one look in a row, with nothing between them, are a list
MAX_TEXT_SHARE = 0.3  # headings holding more than this share of the text are not a heading pattern
MAX_PARAGRAPH_SHARE = 2 / 3  # nor are headings that are most of the paragraphs
MAX_LEVELS = 3

_SENTENCE_END = re.compile(r"[.!;,]$")
_DECIMAL = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+\S")
_NAMED = re.compile(r"^(chapter|part|section|appendix)\s+(\d+|[ivxlcdm]+|[a-z])\b", re.IGNORECASE)
_LETTERED = re.compile(r"^[A-Z][.)]\s+\S")
_CAPTION = re.compile(r"^(figure|fig\.|table|chart|image|photo)\s*\d", re.IGNORECASE)


@dataclass(frozen=True)
class _Look:
    size: int  # rounded size when bigger than normal text, else 0
    bold: bool
    caps: bool
    question: bool
    numbered_only: bool  # stands out only by its numbering (plain text otherwise)


def apply_heading_looks(blocks: list[Block]) -> tuple[list[Block], bool]:
    """Turn lines formatted like headings into headings. Returns the blocks and whether any were found."""
    paragraphs = [i for i, block in enumerate(blocks) if isinstance(block, Paragraph)]
    if len(paragraphs) < 4:
        return blocks, False
    normal = _normal_size(blocks, paragraphs)
    looks = {i: look for i in paragraphs if (look := _candidate(blocks[i], normal)) is not None}
    kept = _recurring(blocks, looks, short=len(paragraphs) < SHORT_DOCUMENT)
    if len(kept) < 2 or len(kept) > MAX_PARAGRAPH_SHARE * len(paragraphs):
        return blocks, False
    total = sum(len(blocks[i].text) for i in paragraphs)
    if sum(len(blocks[i].text) for i in kept) > MAX_TEXT_SHARE * total:
        return blocks, False

    levels = _levels(blocks, {i: looks[i] for i in kept})
    result = list(blocks)
    for i, level in levels.items():
        result[i] = Heading(level, blocks[i].text.strip())
    return result, True


def _normal_size(blocks: list[Block], paragraphs: list[int]) -> float | None:
    """The size most of the text uses (weighted by length): what "normal" means in this document."""
    sizes = Counter()
    for i in paragraphs:
        if blocks[i].size is not None:
            sizes[blocks[i].size] += len(blocks[i].text)
    return sizes.most_common(1)[0][0] if sizes else None


def _candidate(block: Paragraph, normal: float | None) -> _Look | None:
    text = block.text.strip()
    if "\n" in text or len(text) > MAX_CHARS or len(text.split()) > MAX_WORDS:
        return None
    if _SENTENCE_END.search(text) or _CAPTION.match(text) or block.listed:
        return None
    letters = [c for c in text if c.isalpha()]
    caps = len(text.split()) >= 2 and bool(letters) and all(c.isupper() for c in letters)
    bigger = normal is not None and block.size is not None and block.size >= normal * BIGGER
    numbered = bool(_DECIMAL.match(text) or _NAMED.match(text) or _LETTERED.match(text))
    if not (block.bold or bigger or caps or numbered):
        return None
    return _Look(
        size=round(block.size) if bigger else 0,
        bold=block.bold,
        caps=caps,
        question=text.endswith("?"),
        numbered_only=numbered and not (block.bold or bigger or caps),
    )


def _recurring(blocks: list[Block], looks: dict[int, _Look], short: bool) -> list[int]:
    """The candidate lines whose look repeats through the document, with text between them."""
    needed = MIN_REPEATS_SHORT if short else MIN_REPEATS
    by_look: dict[_Look, list[int]] = {}
    for i, look in looks.items():
        by_look.setdefault(look, []).append(i)
    kept: list[int] = []
    for indexes in by_look.values():
        spread = _drop_lists(blocks, indexes, looks)
        if len(spread) >= needed:
            kept.extend(spread)
    return sorted(kept)


def _drop_lists(blocks: list[Block], indexes: list[int], looks: dict[int, _Look]) -> list[int]:
    """Drop runs of more than MAX_IN_A_ROW lines of one look with nothing between them (a list, not headings).

    Lines numbered at different depths may follow each other directly: "1 Setup" then "1.1 Tools" is nesting.
    """
    kept: list[int] = []
    run: list[int] = []
    for i in indexes:
        if run and i == run[-1] + 1 and not _nested(blocks[run[-1]], blocks[i]):
            run.append(i)
            continue
        if len(run) <= MAX_IN_A_ROW:
            kept.extend(run)
        run = [i]
    if len(run) <= MAX_IN_A_ROW:
        kept.extend(run)
    return kept


def _nested(before: Paragraph, after: Paragraph) -> bool:
    first, second = _DECIMAL.match(before.text.strip()), _DECIMAL.match(after.text.strip())
    return bool(first and second and first.group(1).count(".") != second.group(1).count("."))


def _levels(blocks: list[Block], kept: dict[int, _Look]) -> dict[int, int]:
    """From numbering if most kept lines are numbered "1.2"-style, else by look: biggest, then bold, then capitals."""
    numbers = {i: _DECIMAL.match(blocks[i].text.strip()) for i in kept}
    if sum(m is not None for m in numbers.values()) * 2 >= len(kept):
        return {i: min(MAX_LEVELS, m.group(1).count(".") + 1) if m else 1 for i, m in numbers.items()}
    weights = sorted({(look.size, look.bold, look.caps) for look in kept.values()}, reverse=True)
    return {i: min(MAX_LEVELS, weights.index((look.size, look.bold, look.caps)) + 1) for i, look in kept.items()}
