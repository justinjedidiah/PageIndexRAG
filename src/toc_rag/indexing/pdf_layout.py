"""Page layout for PDF reading: figures (charts, pictures, ruled tables) and columns.

Charts in a PDF are usually vector drawings: hundreds of lines, rectangles and curves, with the axis
labels and legends as loose text. Read as text, they give rows of numbers ("25 0.0 40"). So areas
dense with drawn shapes are found first. A ruled table among them (most cells filled, some with
words) becomes a Markdown table; anything else becomes a picture of that part of the page, rendered
like a screenshot, which works the same way as an image in a Word file. Embedded pictures are cut out
the same way. Text inside a figure is left out of the text.

An area that holds full-size sentences is not a figure but a framed section (a "Box"): its frame is
peeled off and the figures inside it, if any, are looked for again.

Then the remaining text is read in column order: where a vertical gap runs down the middle of the page,
the left column is read before the right one, and lines that cross the gap (a title over both
columns) are read in their place.
"""

import io
import statistics
import threading
from collections.abc import Callable
from dataclasses import dataclass

from .blocks import Block, Image, Table

GAP = 14  # points: shapes closer than this belong to one figure (the panels of one chart too)
MIN_SHAPES = 8
MIN_WIDTH, MIN_HEIGHT = 72, 50  # points
PAD = 6  # points of margin kept around a figure (less where other text is closer)
LABEL_REACH = 8  # points: words this close to a chart's edge (units, axis labels) belong to it
MAX_SIDE = 1568  # pixels: the longest side of a rendered figure (what vision models take without shrinking)
MAX_SCALE = 3.0
MIN_PICTURE_PIXELS = 32  # an embedded picture with a side under this (an icon, a rule) is skipped
MIN_PICTURE_POINTS = 24
MAX_PICTURE_SHARE = 0.8  # a picture covering most of the page is a background
BODY_WORDS = 8  # a line with this many words at the page's body text size is a sentence, not a label
TABLE_FILLED = 0.6  # share of cells with text for a grid to be a table, not a chart's gridlines
TABLE_WORDY = 0.2  # share of filled cells holding a word, so a grid of axis numbers is not a table
COLUMN_RANGE = (0.3, 0.7)  # a column gap is looked for in this part of the page width
COLUMN_CROSSING = 0.1  # at most this share of text rows may cross the gap (titles over both columns)
COLUMN_SIDE = 0.25  # each column holds at least this share of the words
MIN_GUTTER = 8  # points of clear space down the page between two columns
COLUMN_ROW_WORDS = 5  # columns of running text: each side averages at least this many words a row (not a list or a form)

# pdfium is not safe to use from two threads at once, and documents are read in worker threads.
PDFIUM_LOCK = threading.Lock()

Box = tuple[float, float, float, float]  # x0, top, x1, bottom, in points from the page's top left
Render = Callable[[Box], bytes]  # renders an area of the current page to PNG bytes


@dataclass
class Figure:
    top: float
    box: Box
    block: Block  # an Image or a Table


def find_figures(page, render: Render, budget: int) -> list[Figure]:
    """The page's charts, ruled tables and pictures, top to bottom; at most `budget` of them."""
    lines = page.extract_text_lines(return_chars=True, strip=True)
    body = _body_lines(lines)
    notes = [_box(line) for line in lines if _is_sentence(line["text"])]  # notes and sources under a chart
    words = [_box(w) for w in page.extract_words() if not any(_centre_in(w, note) for note in notes)]
    width, height = float(page.width), float(page.height)
    shapes = [s for s in page.rects + page.lines + page.curves if _area(_box(s)) < 0.25 * width * height]
    figures: list[Figure] = []
    for box in _drawn_areas(shapes, body):
        if len(figures) >= budget:
            break
        box = _trim_notes(box, notes)
        if box[2] - box[0] < MIN_WIDTH or box[3] - box[1] < MIN_HEIGHT:
            continue
        box = _clamp(_pad(_take_labels(box, words), words + notes), width, height)
        table = _ruled_table(page, box)
        if table is not None:
            figures.append(Figure(box[1], box, table))
        else:
            figures.append(Figure(box[1], box, Image(render(box), "png")))
    for picture in page.images:
        if len(figures) >= budget:
            break
        box = _clamp(_box(picture), width, height)
        source_w, source_h = picture.get("srcsize") or (0, 0)
        if (
            min(source_w, source_h) < MIN_PICTURE_PIXELS
            or min(box[2] - box[0], box[3] - box[1]) < MIN_PICTURE_POINTS
            or _area(box) > MAX_PICTURE_SHARE * width * height
            or any(_contains(f.box, box) for f in figures)
        ):
            continue
        figures.append(Figure(box[1], box, Image(render(box), "png")))
    return sorted(figures, key=lambda f: f.top)


def reading_lines(page, figures: list[Figure]) -> list[dict]:
    """The page's text lines (pdfplumber's, with their characters) in reading order, figures' text left out."""
    boxes = [f.box for f in figures]
    text = page.filter(lambda o: o.get("object_type") != "char" or not any(_centre_in(o, b) for b in boxes))
    gutter = _column_gap(text)
    if gutter is None:
        return text.extract_text_lines(return_chars=True, strip=True)
    lines: list[dict] = []
    for top, bottom, full in _bands(text, gutter):
        if full:
            lines += _lines_in(text, (0, top, float(page.width), bottom))
        else:
            lines += _lines_in(text, (0, top, gutter, bottom))
            lines += _lines_in(text, (gutter, top, float(page.width), bottom))
    return lines


def render_area(pdf_page, box: Box, page_height: float) -> bytes:
    """A PNG of one area of a pdfium page."""
    x0, top, x1, bottom = box
    width, _ = pdf_page.get_size()
    scale = min(MAX_SCALE, MAX_SIDE / max(x1 - x0, bottom - top, 1))
    with PDFIUM_LOCK:
        bitmap = pdf_page.render(scale=scale, crop=(x0, page_height - bottom, width - x1, top))
        picture = bitmap.to_pil()
    buffer = io.BytesIO()
    picture.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


# ---- figures ----


def _body_lines(lines: list[dict]) -> list[dict]:
    """Lines that are sentences: many words, at the size most long lines on the page use."""
    sizes = [round(c["size"], 1) for line in lines if len(line["text"].split()) >= BODY_WORDS for c in line["chars"]]
    if not sizes:
        return []
    body_size = statistics.mode(sizes)
    return [
        line for line in lines
        if len(line["text"].split()) >= BODY_WORDS
        and statistics.median(c["size"] for c in line["chars"]) >= body_size - 0.3
    ]


def _drawn_areas(shapes: list[dict], body: list[dict], depth: int = 0) -> list[Box]:
    found: list[Box] = []
    for box, count in _clusters([_box(s) for s in shapes]):
        if count < MIN_SHAPES or box[2] - box[0] < MIN_WIDTH or box[3] - box[1] < MIN_HEIGHT:
            continue
        if not any(_contains(box, _box(line)) for line in body):
            found.append(box)
        elif depth < 2:
            # Full sentences inside: a framed section, not a figure. Drop its frame (shapes running along
            # most of its width or height) and look for figures within it.
            width, height = box[2] - box[0], box[3] - box[1]
            inner = [
                s for s in shapes
                if _contains(box, _box(s)) and s["x1"] - s["x0"] < 0.9 * width and s["bottom"] - s["top"] < 0.9 * height
            ]
            found += _drawn_areas(inner, body, depth + 1)
    return found


def _clusters(boxes: list[Box]) -> list[tuple[Box, int]]:
    """Group boxes lying within GAP of each other; each group's outer box and how many shapes it holds."""
    groups: list[list] = []
    for box in sorted(boxes, key=lambda b: (b[1], b[0])):
        groups.append([*box, 1])
    merged = True
    while merged:
        merged = False
        out: list[list] = []
        for group in groups:
            for other in out:
                if _near(group, other):
                    other[0], other[1] = min(other[0], group[0]), min(other[1], group[1])
                    other[2], other[3] = max(other[2], group[2]), max(other[3], group[3])
                    other[4] += group[4]
                    merged = True
                    break
            else:
                out.append(group)
        groups = out
    return [((g[0], g[1], g[2], g[3]), g[4]) for g in groups]


def _near(a, b) -> bool:
    return a[0] <= b[2] + GAP and b[0] <= a[2] + GAP and a[1] <= b[3] + GAP and b[1] <= a[3] + GAP


def _ruled_table(page, box: Box) -> Table | None:
    """A Markdown table, if the area is a ruled table: most cells filled, and some of them words, not numbers."""
    try:
        tables = page.within_bbox(box).find_tables()
    except ValueError:
        return None
    for table in tables:
        rows = [[" ".join((cell or "").split()) for cell in row] for row in table.extract()]
        cells = [cell for row in rows for cell in row]
        filled = [cell for cell in cells if cell]
        if len(rows) < 2 or len(rows[0]) < 2 or not cells or len(filled) < TABLE_FILLED * len(cells):
            continue
        wordy = sum(1 for cell in filled if sum(c.isalpha() for c in cell) >= 2)
        if wordy < TABLE_WORDY * len(filled):
            continue
        return Table(_markdown(rows))
    return None


def _markdown(rows: list[list[str]]) -> str:
    def row(cells: list[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"

    return "\n".join([row(rows[0]), "| " + " | ".join("---" for _ in rows[0]) + " |", *(row(r) for r in rows[1:])])


# ---- columns ----


def _column_gap(page) -> float | None:
    """The x of a gap running down the page between two columns of text, or None for a single column.

    The gap must be clear for at least MIN_GUTTER points (wider than a space between words, which can line
    up by chance), crossed by only a few rows of text (titles over both columns; in a single column almost
    every row crosses the middle), and leave a fair share of the words on each side, in rows of running text.
    """
    words = page.extract_words()
    if len(words) < 40:
        return None
    width = float(page.width)
    rows = len({round(w["top"]) for w in words})
    clear: list[int] = []
    for x in range(int(width * COLUMN_RANGE[0]), int(width * COLUMN_RANGE[1])):
        crossing_rows = {round(w["top"]) for w in words if w["x0"] < x < w["x1"]}
        if len(crossing_rows) <= COLUMN_CROSSING * rows:
            clear.append(x)
    runs: list[tuple[int, int]] = []
    for x in clear:
        if runs and x == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], x)
        else:
            runs.append((x, x))
    best: float | None = None
    for first, last in runs:
        if last - first < MIN_GUTTER:
            continue
        middle = (first + last) / 2
        left = [w for w in words if w["x1"] <= middle]
        right = [w for w in words if w["x0"] >= middle]
        if min(len(left), len(right)) < COLUMN_SIDE * len(words):
            continue
        if min(_words_per_row(left), _words_per_row(right)) < COLUMN_ROW_WORDS:
            continue  # a list in columns ("FR France  MY Malaysia") reads better row by row
        if best is None or abs(middle - width / 2) < abs(best - width / 2):
            best = middle
    return best


def _words_per_row(words: list[dict]) -> float:
    return len(words) / max(1, len({round(w["top"]) for w in words}))


def _bands(page, gutter: float) -> list[tuple[float, float, bool]]:
    """Horizontal bands of the page, top to bottom: (top, bottom, crosses the gap)."""
    crossing = sorted(
        (w["top"] - 1, w["bottom"] + 1) for w in page.extract_words() if w["x0"] < gutter < w["x1"]
    )
    spans: list[list[float]] = []
    for top, bottom in crossing:
        if spans and top <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], bottom)
        else:
            spans.append([top, bottom])
    bands: list[tuple[float, float, bool]] = []
    cursor = 0.0
    for top, bottom in spans:
        if top > cursor:
            bands.append((cursor, top, False))
        bands.append((top, bottom, True))
        cursor = bottom
    bands.append((cursor, float(page.height), False))
    return bands


def _lines_in(page, box: Box) -> list[dict]:
    part = page.filter(lambda o: o.get("object_type") != "char" or _centre_in(o, box))
    return part.extract_text_lines(return_chars=True, strip=True)


# ---- geometry ----


def _box(obj) -> Box:
    return (float(obj["x0"]), float(obj["top"]), float(obj["x1"]), float(obj["bottom"]))


def _area(box: Box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _contains(outer: Box, inner: Box, slack: float = 2) -> bool:
    return (
        inner[0] >= outer[0] - slack and inner[1] >= outer[1] - slack
        and inner[2] <= outer[2] + slack and inner[3] <= outer[3] + slack
    )


def _centre_in(obj, box: Box) -> bool:
    x = (obj["x0"] + obj["x1"]) / 2
    y = (obj["top"] + obj["bottom"]) / 2
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _is_sentence(text: str) -> bool:
    """A line of prose (a note, a source): many words, with a full stop."""
    return len(text.split()) >= BODY_WORDS and (text.rstrip().endswith(".") or ". " in text)


def _trim_notes(box: Box, notes: list[Box]) -> Box:
    """Cut the notes and sources off a figure's top or bottom edge: they are text, not part of the picture."""
    x0, top, x1, bottom = box
    middle = (top + bottom) / 2
    for note in notes:
        if not _centre_in({"x0": note[0], "x1": note[2], "top": note[1], "bottom": note[3]}, box):
            continue
        if (note[1] + note[3]) / 2 > middle:
            bottom = min(bottom, note[1] - 2)
        else:
            top = max(top, note[3] + 2)
    return (x0, top, x1, bottom)


def _take_labels(box: Box, words: list[Box]) -> Box:
    """Grow a chart's area over the words at its edge (units above the plot, axis labels beside it)."""
    # One pass, measured from the drawn shapes only: a second would creep on to the titles and notes beyond.
    reach = (box[0] - LABEL_REACH, box[1] - LABEL_REACH, box[2] + LABEL_REACH, box[3] + LABEL_REACH)
    grown = box
    for word in words:
        if _overlaps(reach, word) and not _contains(box, word, slack=0):
            grown = (min(grown[0], word[0]), min(grown[1], word[1]), max(grown[2], word[2]), max(grown[3], word[3]))
    return grown


def _pad(box: Box, words: list[Box]) -> Box:
    """A margin around a figure, stopping short of any text outside it."""
    x0, top, x1, bottom = box[0] - PAD, box[1] - PAD, box[2] + PAD, box[3] + PAD
    for word in words:
        if _contains(box, word, slack=0) or not _overlaps((x0, top, x1, bottom), word):
            continue
        if word[3] <= box[1]:
            top = max(top, word[3] + 2)
        elif word[1] >= box[3]:
            bottom = min(bottom, word[1] - 2)
        elif word[2] <= box[0]:
            x0 = max(x0, word[2] + 2)
        elif word[0] >= box[2]:
            x1 = min(x1, word[0] - 2)
    return (x0, top, x1, bottom)


def _overlaps(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _clamp(box: Box, width: float, height: float) -> Box:
    return (max(0.0, box[0]), max(0.0, box[1]), min(width, box[2]), min(height, box[3]))
