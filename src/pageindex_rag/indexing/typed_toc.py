"""A contents page typed by hand: find it, check it against the body, and use it.

Works on extracted blocks, so it is the same for every file type. Run after extraction:

1. Find the block: near the start of the document, a run of at least 3 lines that each end in a
   page number ("Installation ..... 12", "Installation<tab>12", roman numerals too).
2. Work out each entry's level: from its numbering ("1.2" is level 2), else from indentation
   (measured from each page's own left edge: left- and right-hand pages often have different
   margins), else all level 1.
3. Find each entry in the body, in order, searching forward from the last one found: a short
   paragraph with the same text (ignoring case, punctuation and leading numbering), or up to 3
   short paragraphs in a row that together give it (a small "Box A" over its title). Searching in
   order keeps a sentence that only mentions a title, or a second section with the same name,
   from being taken for it.
4. In a PDF, the page numbers are checked too: an entry only matches a title on the page its
   number points to, give or take one. The printed page labels say which page that is ("iv", or a
   "1" that is really the 11th page); when there are none, the offset between printed numbers and
   pages is learnt from the entries found by text. A .docx has no fixed pages, so there this is skipped.
5. Trust it only if at least 2 entries, and at least half of them, were found. Otherwise it was
   probably not a contents page (a price list) or is too out of date to follow, and nothing changes.

A trusted contents page is removed from the text and kept as the document's own ToC (TocLine
blocks). If the document has no headings, the paragraphs it points to become its headings.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass

from .blocks import Block, Heading, Paragraph, TocLine

MIN_ENTRIES = 3
SEARCH_START = 0.2  # the contents page must start within the first fifth of the document...
SEARCH_START_MIN = 40  # ...or the first 40 blocks, whichever is more
MAX_HEADING_CHARS = 150  # a body paragraph longer than this is text, not a heading
MAX_TITLE_PARTS = 3  # a title may be printed over this many short paragraphs in a row
PAGE_SLACK = 1  # an entry's title may be this many pages from where its number points

# A title, then a tab, dot leaders or a wide gap, then a page number (arabic, or roman as in front matter).
_ENTRY = re.compile(
    r"^(?P<title>.*?[^\W\d_].*?)(?:\t+|\s*(?:\.\s?){2,}\s*|\s*[·…_]{2,}\s*|\s{2,}|\s+)(?P<page>\d{1,4}|[ivxlcdm]{1,7})\s*$",
    re.IGNORECASE,
)
_CONTENTS_TITLE = re.compile(r"^(table of contents|contents|contents page|in this (document|guide|report))\W*$", re.IGNORECASE)
_LIST_TITLE = re.compile(r"^list of (figures|tables|illustrations|charts|images)\b", re.IGNORECASE)
_LIST_ENTRY = re.compile(r"^(figure|fig\.|table|chart|image)\s*\d", re.IGNORECASE)
_NUMBERING = re.compile(r"^(?P<num>\d+(?:\.\d+)*)[.)]?\s+(?=\S)")
# A chapter or part: "I. Progress and peril", "Chapter 3 Costs", "Part II Wiring".
_CHAPTER = re.compile(r"^(?:[IVXLC]+\.\s|(?:chapter|part)\s+(?:\d+|[ivxlc]+)\b)", re.IGNORECASE)


@dataclass
class _Entry:
    title: str
    page: str
    indent: float
    on_page: int | None = None  # the PDF page the entry itself is printed on (for its indentation)
    level: int = 1


Span = tuple[int, int]  # the first and last block of a match


def apply_typed_toc(blocks: list[Block], page_labels: list[str] | None = None) -> tuple[list[Block], bool]:
    """Use a typed contents page if there is a trustworthy one.

    `page_labels` are a PDF's printed page numbers, by page (blocks then carry their page). Returns
    the new blocks and whether the contents page built the outline (only when the document had no
    headings). Untrusted or absent: the blocks come back unchanged.
    """
    found = _find_contents(blocks)
    if found is None:
        return blocks, False
    start, end, entries = found
    _set_levels(entries)
    has_headings = any(isinstance(b, Heading) for b in blocks)
    matches = _match(entries, blocks, end)
    if any(b.page is not None for b in blocks if isinstance(b, Paragraph)):
        matches = _match_on_pages(entries, blocks, end, matches, page_labels)
    found_count = sum(m is not None for m in matches)
    if found_count < 2 or found_count * 2 < len(entries):
        return blocks, False

    toc_lines = [TocLine(e.level, e.title, e.page) for e in entries]
    if any(isinstance(b, TocLine) for b in blocks):
        toc_lines = []  # Word's own ToC is already kept: don't record a second copy
    result: list[Block] = []
    promote = {} if has_headings else {span[0]: (entry, span) for entry, span in zip(entries, matches) if span is not None}
    skip_until = -1
    for i, block in enumerate(blocks):
        if i == start:
            result.extend(toc_lines)
        if start <= i < end or i <= skip_until:
            continue  # the contents page itself, or the rest of a title printed over several lines
        if i in promote:
            entry, (first, last) = promote[i]
            # A title printed over several lines ("Box A" over its subtitle) takes the contents page's wording.
            title = block.text if first == last else entry.title
            result.append(Heading(entry.level, title, page=block.page))
            skip_until = last
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
                following.on_page = block.page
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
    return _Entry(title, match.group("page"), block.indent, block.page) if title else None


def _set_levels(entries: list[_Entry]) -> None:
    """From numbering if most entries are numbered, else from indentation, else all level 1.

    Entries named like chapters ("I. ...", "Chapter 3") go one level above the rest when only some are:
    contents pages often set chapters apart by name alone, at the same indent as their sections.
    """
    _set_levels_by_layout(entries)
    chapters = [bool(_CHAPTER.match(e.title)) for e in entries]
    if any(chapters) and not all(chapters):
        for entry, chapter in zip(entries, chapters):
            entry.level = 1 if chapter else entry.level + 1


def _set_levels_by_layout(entries: list[_Entry]) -> None:
    numbered = [_NUMBERING.match(e.title) for e in entries]
    if sum(m is not None for m in numbered) * 2 >= len(entries):
        for entry, match in zip(entries, numbered):
            entry.level = match.group("num").count(".") + 1 if match else 1
        return
    # Indentation from each page's own left edge: left- and right-hand pages often have different margins.
    left_edge: dict[int | None, float] = {}
    for entry in entries:
        left_edge[entry.on_page] = min(left_edge.get(entry.on_page, entry.indent), entry.indent)
    indents = [entry.indent - left_edge[entry.on_page] for entry in entries]
    steps = sorted({round(indent / 6) for indent in indents})  # indents within 6 points count as the same step
    for entry, indent in zip(entries, indents):
        entry.level = steps.index(round(indent / 6)) + 1


def _match(
    entries: list[_Entry], blocks: list[Block], body_start: int, expected: list[int | None] | None = None
) -> list[Span | None]:
    """For each entry in order, the blocks where its title is, searching forward; None if not found.

    With `expected` pages (PDF), an entry that has one only matches a title on that page or, failing
    that, within PAGE_SLACK pages of it: the exact page first, so a mention on the page before loses.
    """
    matches: list[Span | None] = []
    cursor = body_start
    for n, entry in enumerate(entries):
        wanted = _keys(entry.title)
        page = expected[n] if expected else None
        if page is None:
            found = _search(blocks, cursor, wanted, None)
        else:
            found = _search(blocks, cursor, wanted, (page, page)) or _search(
                blocks, cursor, wanted, (page - PAGE_SLACK, page + PAGE_SLACK)
            )
        matches.append(found)
        if found is not None:
            cursor = found[1] + 1
    return matches


def _search(blocks: list[Block], cursor: int, wanted: set[str], pages: tuple[int, int] | None) -> Span | None:
    """The first title match from `cursor` on, only on pages within `pages` when given."""
    for i in range(cursor, len(blocks)):
        page = blocks[i].page if isinstance(blocks[i], (Heading, Paragraph)) else None
        if pages is not None and page is not None:
            if page > pages[1]:
                return None  # past where it can be
            if page < pages[0]:
                continue
        found = _title_at(blocks, i, wanted)
        if found is not None:
            return found
    return None


def _title_at(blocks: list[Block], start: int, wanted: set[str]) -> Span | None:
    """The blocks from `start` that give the title: one short paragraph, or up to 3 in a row."""
    joined = ""
    for end in range(start, min(start + MAX_TITLE_PARTS, len(blocks))):
        block = blocks[end]
        text = block.title if isinstance(block, Heading) else block.text if isinstance(block, Paragraph) else None
        if text is None or len(text) > MAX_HEADING_CHARS:
            return None
        if end == start and _keys(text) & wanted:
            return start, end
        if isinstance(block, Heading):
            return None  # a heading is a whole title on its own
        joined = f"{joined} {text.strip()}".strip()
        if end > start and _keys(joined) & wanted:
            return start, end
        if not any(key.startswith(_clean(joined)) for key in wanted):
            return None
    return None


def _match_on_pages(
    entries: list[_Entry], blocks: list[Block], body_start: int, by_text: list[Span | None], labels: list[str] | None
) -> list[Span | None]:
    """Match again, each entry only near the page its number points to.

    Where each number points: the page carrying that printed label, else the number as a page count,
    shifted by the offset most matches by text agree on (a cover and front matter before page 1).
    Entries whose number cannot be placed keep matching by text alone.
    """
    pointed = _pointed_pages(entries, labels)
    shifts = Counter(
        blocks[span[0]].page - page
        for span, page in zip(by_text, pointed)
        if span is not None and page is not None and blocks[span[0]].page is not None
    )
    if not shifts:
        return by_text
    shift, votes = shifts.most_common(1)[0]
    if votes < 2:
        return by_text
    return _match(entries, blocks, body_start, [None if page is None else page + shift for page in pointed])


def _pointed_pages(entries: list[_Entry], labels: list[str] | None) -> list[int | None]:
    """The page (0-based) each entry's number names: by printed label, else as a count from 1.

    Labels can repeat (each chapter restarting at 1), so each entry takes the first page with its label
    at or after the previous entry's page.
    """
    by_label: dict[str, list[int]] = {}
    for index, label in enumerate(labels or []):
        by_label.setdefault(label.strip().lower(), []).append(index)
    pages: list[int | None] = []
    previous = 0
    for entry in entries:
        key = entry.page.strip().lower()
        page: int | None = None
        if key in by_label:
            later = [index for index in by_label[key] if index >= previous]
            page = later[0] if later else by_label[key][0]
        elif key.isdigit():
            page = int(key) - 1
        pages.append(page)
        if page is not None:
            previous = page
    return pages


def _clean(value: str) -> str:
    value = value.lower().replace("’", "'").replace("‘", "'")
    return " ".join(re.sub(r"[^\w\s']", " ", value).split())


def _keys(text: str) -> set[str]:
    """The text in comparable form, with and without its leading numbering ("1.2 Wiring" also matches "Wiring")."""
    keys = {_clean(text), _clean(_NUMBERING.sub("", text.strip()))}
    return {key for key in keys if key}
