"""Read a PDF into blocks: paragraphs with their look and page, and its bookmarks as headings.

A PDF has no paragraphs, only positioned lines of text. Lines are grouped into paragraphs by the gap
between them and by a change of look (size or bold). Each paragraph keeps its font size and boldness,
so the same structure detectors as for Word work on it (typed contents page, lines formatted like
headings).

Bookmarks (the outline panel of a PDF viewer) are the PDF's own outline: when they are believable,
each becomes a heading, placed at the line on its page that carries its title, else where it points.

Two libraries, both with permissive licences: pypdfium2 (bookmarks, printed page labels, page count,
password check) and pdfplumber (text with fonts and positions).
"""

import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium

from ..models import TocEntry
from .blocks import Block, DocumentRejected, Heading, Paragraph

BOLD_FONT = re.compile(r"bold|black|heavy|semibold|demibold", re.IGNORECASE)
# Bookmarks some tools add that say nothing about the content: "Page 3", "12", "Untitled", "Bookmark 4".
JUNK_BOOKMARK = re.compile(r"^((page|p\.)\s*)?\d+$|^untitled\b|^bookmark\s*\d*$", re.IGNORECASE)
PARAGRAPH_GAP = 0.5  # a gap between lines over this share of the line height starts a new paragraph
SIZE_CHANGE = 0.5  # points
MIN_TEXT_PER_PAGE = 20  # characters; fewer and the page counts as having no text (a scan or a picture)
MARGIN = 0.1  # running headers and footers sit in the top or bottom tenth of the page
RUNNING_MIN_PAGES = 3  # a margin line recurring on at least this many pages,
RUNNING_MIN_SHARE = 0.2  # and on at least this share of them, is a running header or footer
PAGE_NUMBER = re.compile(r"^[\W#]*((page|p\.)\s*)?#(\s*(of|/)\s*#)?[\W#]*$", re.IGNORECASE)


@dataclass
class PdfContent:
    blocks: list[Block]
    page_labels: list[str]  # the page number printed on each page ("iv", "12"), by page index
    outline: list[TocEntry] | None  # the bookmarks, when they were believable and used for headings


@dataclass
class _Line:
    text: str
    top: float
    bottom: float
    x0: float
    size: float
    bold: bool


@dataclass
class _Para:
    text: str
    top: float
    x0: float
    size: float
    bold: bool
    lines: list[str] = field(default_factory=list)


@dataclass
class _Bookmark:
    level: int
    title: str
    page: int | None
    top: float | None  # distance from the top of the page it points to, when it says


def extract_pdf(path: Path | str, max_pages: int | None = None) -> PdfContent:
    """Read a PDF. Raises DocumentRejected for a password-protected, scanned or too long one."""
    path = Path(path)
    try:
        pdf = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise DocumentRejected("This PDF is password-protected. Remove the password and upload it again.") from exc
        raise
    try:
        count = len(pdf)
        if max_pages and count > max_pages:
            raise DocumentRejected(f"This PDF has {count:,} pages. The limit here is {max_pages:,}.")
        labels = [pdf.get_page_label(i) or str(i + 1) for i in range(count)]
        heights = [pdf.get_page_size(i)[1] for i in range(count)]
        bookmarks = _bookmarks(pdf, heights)
    finally:
        pdf.close()

    page_lines: list[list[_Line]] = []
    with pdfplumber.open(path) as document:
        for page in document.pages:
            page_lines.append(_lines(page))
            page.close()  # frees the page's parsed objects: long documents stay light on memory
    _drop_running_lines(page_lines, heights)
    pages = [_paragraphs(lines) for lines in page_lines]
    _check_text_layer(pages)

    outline = _believable(bookmarks)
    blocks: list[Block] = []
    for number, paragraphs in enumerate(pages):
        on_page = [b for b in outline if b.page == number] if outline else []
        blocks.extend(_page_blocks(paragraphs, on_page, number))
    toc = [TocEntry(title=b.title, level=b.level, page=labels[b.page]) for b in outline] if outline else None
    return PdfContent(blocks=blocks, page_labels=labels, outline=toc)


# ---- text ----


def _lines(page) -> list[_Line]:
    lines = []
    for line in page.extract_text_lines(return_chars=True, strip=True):
        chars = [c for c in line["chars"] if c["text"].strip()]
        if not chars or not line["text"].strip():
            continue
        lines.append(
            _Line(
                text=" ".join(line["text"].split()),
                top=line["top"],
                bottom=line["bottom"],
                x0=line["x0"],
                size=round(statistics.median(c["size"] for c in chars), 1),
                bold=all(BOLD_FONT.search(c.get("fontname") or "") for c in chars),
            )
        )
    return lines


def _running_key(text: str) -> str:
    """A margin line without its numbers, so "10 Annual Report 2026" and "Annual Report 2026 11" are the same footer."""
    return " ".join(re.sub(r"\d+", " ", text.lower()).split())


def _is_page_number(text: str) -> bool:
    return bool(PAGE_NUMBER.match(re.sub(r"\d+", "#", " ".join(text.lower().split()))))


def _drop_running_lines(page_lines: list[list[_Line]], heights: list[float]) -> None:
    """Remove running headers and footers, and page numbers, in place.

    A line in the top or bottom tenth of the page goes when its text, numbers aside, recurs on several pages
    (a book title, a chapter name, "Annual Report 2026 11"), or when it is only a page number.
    """
    def in_margin(line: _Line, height: float) -> bool:
        return line.bottom <= height * MARGIN or line.top >= height * (1 - MARGIN)

    pages_with: dict[str, int] = {}
    for lines, height in zip(page_lines, heights):
        for key in {_running_key(line.text) for line in lines if in_margin(line, height)}:
            pages_with[key] = pages_with.get(key, 0) + 1
    needed = max(RUNNING_MIN_PAGES, RUNNING_MIN_SHARE * len(page_lines))
    running = {key for key, count in pages_with.items() if count >= needed}
    for lines, height in zip(page_lines, heights):
        lines[:] = [
            line for line in lines
            if not (in_margin(line, height) and (_running_key(line.text) in running or _is_page_number(line.text)))
        ]


def _paragraphs(lines: list[_Line]) -> list[_Para]:
    """Group lines into paragraphs: a new one starts after a wide gap, or where the look changes."""
    paragraphs: list[_Para] = []
    previous: _Line | None = None
    for line in lines:
        height = max(line.bottom - line.top, previous.bottom - previous.top if previous else 0, 1)
        starts_new = (
            previous is None
            or line.top - previous.bottom > PARAGRAPH_GAP * height
            or line.top < previous.top  # moved up the page: a new column or a text box
            or abs(line.size - previous.size) > SIZE_CHANGE
            or line.bold != previous.bold
        )
        if starts_new:
            paragraphs.append(_Para(line.text, line.top, line.x0, line.size, line.bold, [line.text]))
        else:
            current = paragraphs[-1]
            current.text = _join_lines(current.text, line.text)
            current.lines.append(line.text)
        previous = line
    return paragraphs


def _join_lines(text: str, line: str) -> str:
    """Join a wrapped line on: a word hyphenated at the line end is put back together."""
    if re.search(r"[a-z]-$", text) and line[:1].islower():
        return text[:-1] + line
    return f"{text} {line}"


def _check_text_layer(pages: list[list[_Para]]) -> None:
    with_text = sum(1 for paragraphs in pages if sum(len(p.text) for p in paragraphs) >= MIN_TEXT_PER_PAGE)
    if with_text == 0:
        raise DocumentRejected(
            "This PDF has no text to read: it looks like scanned pages. Scanned documents are not supported yet."
        )
    if with_text < len(pages) / 2:
        raise DocumentRejected(
            "Most pages of this PDF have no text to read: they look like scanned pages. "
            "Scanned documents are not supported yet."
        )


# ---- bookmarks ----


def _bookmarks(pdf, heights: list[float]) -> list[_Bookmark]:
    found = []
    for item in pdf.get_toc():
        title = " ".join((item.get_title() or "").split())
        dest = item.get_dest()
        page = dest.get_index() if dest is not None else None
        top = None
        if dest is not None and page is not None and 0 <= page < len(heights):
            mode, position = dest.get_view()
            if mode == 1 and len(position) >= 2 and position[1]:  # "XYZ": a point on the page
                top = heights[page] - position[1]  # PDF measures from the bottom; text positions from the top
        found.append(_Bookmark(item.level + 1, title, page if page is not None and 0 <= page < len(heights) else None, top))
    return found


def _believable(bookmarks: list[_Bookmark]) -> list[_Bookmark] | None:
    """The bookmarks worth using as the outline: at least 2, and most of them real titles pointing at a page."""
    usable = [b for b in bookmarks if b.title and b.page is not None and not JUNK_BOOKMARK.match(b.title)]
    if len(usable) < 2 or len(usable) < len(bookmarks) / 2:
        return None
    return usable


def _page_blocks(paragraphs: list[_Para], bookmarks: list[_Bookmark], page: int) -> list[Block]:
    """The page's paragraphs, with each bookmark's heading where its title is, else where it points."""
    items: list[Block] = [
        Paragraph(p.text, indent=p.x0, bold=p.bold, size=p.size, page=page) for p in paragraphs
    ]
    tops = [p.top for p in paragraphs]
    for bookmark in bookmarks:
        heading = Heading(bookmark.level, bookmark.title, page=page)
        after = 0 if bookmark.top is None else next((i for i, top in enumerate(tops) if top >= bookmark.top - 5), len(tops))
        found = next(
            (span for i in range(after, len(items)) if (span := _title_span(items, i, bookmark.title)) is not None),
            None,
        )
        if found is None:
            # No line carries the title: the heading goes where the bookmark points (or the top of the page).
            at = next((i for i in range(len(items)) if i >= after and isinstance(items[i], Paragraph)), len(items))
            items.insert(at, heading)
            tops.insert(at, bookmark.top or 0)
            continue
        start, end = found  # the paragraphs carrying the title (a title can be printed over 2-3 lines of different looks)
        last = items[end]
        rest = _after_title(" ".join(items[i].text for i in range(start, end + 1)), bookmark.title)
        items[start: end + 1] = [heading] + ([Paragraph(rest, indent=last.indent, size=last.size, page=page)] if rest else [])
        tops[start: end + 1] = [tops[start]] * (2 if rest else 1)
    return items


def _key(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def _title_span(items: list[Block], start: int, title: str, most: int = 3) -> tuple[int, int] | None:
    """The paragraphs from `start` that together begin with the title, as (first, last), or None."""
    title_key = _key(title)
    if not title_key:
        return None
    joined = ""
    for end in range(start, min(start + most, len(items))):
        if not isinstance(items[end], Paragraph):
            return None
        joined = f"{joined} {_key(items[end].text)}".strip()
        if joined == title_key or joined.startswith(title_key + " "):
            return start, end
        if not title_key.startswith(joined + " "):
            return None
    return None


def _after_title(text: str, title: str) -> str:
    """What follows the title in a paragraph that starts with it (a heading run into its first line)."""
    words = len(_key(title).split())
    kept = text.split()
    taken = 0
    for i, word in enumerate(kept):
        if _key(word):
            taken += len(_key(word).split())
        if taken >= words:
            return " ".join(kept[i + 1 :])
    return ""
