"""A contents page typed by hand: find it, check it against the body, and use it.

Works on extracted blocks, so it is the same for every file type. Run after extraction:

1. Find the block: near the start of the document, a run of at least 3 lines that each end in a
   page number ("Installation ..... 12", "Installation<tab>12", roman numerals too).
2. Work out each entry's level: from its numbering ("1.2" is level 2), else from indentation,
   else all level 1.
3. Find each entry in the body, in order, searching forward from the last one found: a short
   paragraph with the same text (ignoring case, punctuation and leading numbering). Searching in
   order keeps a sentence that only mentions a title, or a second section with the same name,
   from being taken for it. Page numbers are ignored: a .docx has no fixed pages.
4. Trust it only if at least 2 entries, and at least half of them, were found. Otherwise it was
   probably not a contents page (a price list) or is too out of date to follow, and nothing changes.

A trusted contents page is removed from the text and kept as the document's own ToC (TocLine
blocks). If the document has no headings, the paragraphs it points to become its headings.
"""

import math
import re
from dataclasses import dataclass

from .blocks import Block, Heading, Paragraph, TocLine

MIN_ENTRIES = 3
SEARCH_START = 0.2  # the contents page must start within the first fifth of the document...
SEARCH_START_MIN = 40  # ...or the first 40 blocks, whichever is more
MAX_HEADING_CHARS = 150  # a body paragraph longer than this is text, not a heading

# A title, then a tab, dot leaders or a wide gap, then a page number (arabic, or roman as in front matter).
_ENTRY = re.compile(
    r"^(?P<title>.*?[^\W\d_].*?)(?:\t+|\s*(?:\.\s?){2,}\s*|\s*[·…_]{2,}\s*|\s{2,}|\s+)(?P<page>\d{1,4}|[ivxlcdm]{1,7})\s*$",
    re.IGNORECASE,
)
_CONTENTS_TITLE = re.compile(r"^(table of contents|contents|contents page|in this (document|guide|report))\W*$", re.IGNORECASE)
_LIST_TITLE = re.compile(r"^list of (figures|tables|illustrations|charts|images)\b", re.IGNORECASE)
_LIST_ENTRY = re.compile(r"^(figure|fig\.|table|chart|image)\s*\d", re.IGNORECASE)
_NUMBERING = re.compile(r"^(?P<num>\d+(?:\.\d+)*)[.)]?\s+(?=\S)")


@dataclass
class _Entry:
    title: str
    page: str
    indent: float
    level: int = 1


def apply_typed_toc(blocks: list[Block]) -> tuple[list[Block], bool]:
    """Use a typed contents page if there is a trustworthy one.

    Returns the new blocks and whether the contents page built the outline (only when the document
    had no headings). Untrusted or absent: the blocks come back unchanged.
    """
    found = _find_contents(blocks)
    if found is None:
        return blocks, False
    start, end, entries = found
    _set_levels(entries)
    has_headings = any(isinstance(b, Heading) for b in blocks)
    matches = _match(entries, blocks, end)
    found_count = sum(m is not None for m in matches)
    if found_count < 2 or found_count * 2 < len(entries):
        return blocks, False

    toc_lines = [TocLine(e.level, e.title, e.page) for e in entries]
    if any(isinstance(b, TocLine) for b in blocks):
        toc_lines = []  # Word's own ToC is already kept: don't record a second copy
    result: list[Block] = []
    promote = {} if has_headings else {index: entry.level for entry, index in zip(entries, matches) if index is not None}
    for i, block in enumerate(blocks):
        if i == start:
            result.extend(toc_lines)
        if start <= i < end:
            continue  # the contents page itself: not text
        if i in promote:
            result.append(Heading(promote[i], block.text, page=block.page))
        else:
            result.append(block)
    return result, not has_headings


def _find_contents(blocks: list[Block]) -> tuple[int, int, list[_Entry]] | None:
    """The first run of at least 3 entry lines near the start: (first block, block after it, entries)."""
    limit = max(SEARCH_START_MIN, math.ceil(len(blocks) * SEARCH_START))
    i = 0
    while i < min(limit, len(blocks)):
        run_end, entries = _read_run(blocks, i)
        title_above = blocks[i - 1].text if i > 0 and isinstance(blocks[i - 1], Paragraph) else ""
        if len(entries) >= MIN_ENTRIES and not _LIST_TITLE.match(title_above):
            entries = [e for e in entries if not _LIST_ENTRY.match(e.title)]
            if len(entries) >= MIN_ENTRIES:
                start = i - 1 if _CONTENTS_TITLE.match(title_above) else i
                return start, run_end, entries
        i = max(run_end, i + 1)
    return None


def _read_run(blocks: list[Block], start: int) -> tuple[int, list[_Entry]]:
    """Entry lines from `start` on. A line without a page number followed by one with joins it (a wrapped title)."""
    entries: list[_Entry] = []
    i = start
    while i < len(blocks):
        block = blocks[i]
        if not isinstance(block, Paragraph):
            break
        entry = _entry(block)
        if entry is None and entries and i + 1 < len(blocks) and isinstance(blocks[i + 1], Paragraph):
            following = _entry(blocks[i + 1])
            if following is not None and len(block.text) <= MAX_HEADING_CHARS:
                following.title = f"{block.text.strip()} {following.title}"
                following.indent = block.indent
                entries.append(following)
                i += 2
                continue
        if entry is None:
            break
        entries.append(entry)
        i += 1
    return i, entries


def _entry(block: Paragraph) -> _Entry | None:
    text = block.text.strip()
    if len(text) > MAX_HEADING_CHARS + 20:
        return None
    match = _ENTRY.match(text)
    if not match:
        return None
    title = re.sub(r"[\s.·…_\t-]+$", "", match.group("title")).strip()
    return _Entry(title, match.group("page"), block.indent) if title else None


def _set_levels(entries: list[_Entry]) -> None:
    """From numbering if most entries are numbered, else from indentation, else all level 1."""
    numbered = [_NUMBERING.match(e.title) for e in entries]
    if sum(m is not None for m in numbered) * 2 >= len(entries):
        for entry, match in zip(entries, numbered):
            entry.level = match.group("num").count(".") + 1 if match else 1
        return
    steps = sorted({round(e.indent / 6) for e in entries})  # indents within 6 points count as the same step
    for entry in entries:
        entry.level = steps.index(round(entry.indent / 6)) + 1


def _match(entries: list[_Entry], blocks: list[Block], body_start: int) -> list[int | None]:
    """For each entry in order, the block where its section starts, searching forward; None if not found."""
    matches: list[int | None] = []
    cursor = body_start
    for entry in entries:
        wanted = _keys(entry.title)
        found = None
        for i in range(cursor, len(blocks)):
            block = blocks[i]
            text = block.title if isinstance(block, Heading) else block.text if isinstance(block, Paragraph) else None
            if text is not None and len(text) <= MAX_HEADING_CHARS and _keys(text) & wanted:
                found = i
                break
        matches.append(found)
        if found is not None:
            cursor = found + 1
    return matches


def _keys(text: str) -> set[str]:
    """The text in comparable form, with and without its leading numbering ("1.2 Wiring" also matches "Wiring")."""
    def clean(value: str) -> str:
        value = value.lower().replace("’", "'").replace("‘", "'")
        return " ".join(re.sub(r"[^\w\s']", " ", value).split())

    keys = {clean(text)}
    stripped = _NUMBERING.sub("", text.strip())
    keys.add(clean(stripped))
    return {key for key in keys if key}
