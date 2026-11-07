"""Word: the document's own outline gives the structure directly, with no LLM.

A paragraph is a heading if Word gives it an outline level: the Heading 1-9 styles carry one, and
so do most custom heading styles. These are exactly the paragraphs Word's Navigation Pane lists,
and what Word builds its own table of contents from.

A table of contents inserted by Word (References > Table of Contents) is kept out of the text:
its entries become TocLine blocks, kept as the document's own ToC for reference. Content controls
(the boxes Word templates use for cover pages and forms) are read like any other content.
"""

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph

from .blocks import Block, Heading, Image, Paragraph, Table, TocLine

_HEADING_STYLE = re.compile(r"^heading (\d)$", re.IGNORECASE)  # fallback when a heading style has no outline level
_TOC_STYLE = re.compile(r"^toc ?(\d)$", re.IGNORECASE)  # Word's table-of-contents entry styles: name "toc 1", id "TOC1"
_BODY_TEXT = 9  # outline level 9 means "body text" (0-8 are heading levels 1-9)
_MAX_STYLE_DEPTH = 20  # styles are based on other styles; this guards against a loop
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
# Parts of a paragraph that are not its own text: formatting, pictures, text boxes (and their
# compatibility copies, which would otherwise repeat the text) and content-control settings.
_NOT_TEXT = {
    qn("w:pPr"), qn("w:rPr"), qn("w:sdtPr"), qn("w:drawing"), qn("w:pict"), qn("w:txbxContent"), _MC_FALLBACK,
}
_TOC_PAGE = re.compile(r"^(?P<title>.*?)[\s.·…_-]*?(?P<page>[0-9]+|[ivxlcdm]+)$", re.IGNORECASE)


class _Context:
    """Per document: the styles by id and the default text size, looked up once rather than per paragraph."""

    def __init__(self, doc: DocxDocument):
        self.doc = doc
        self.styles = {style.style_id: style for style in doc.styles}
        size = doc.styles.element.find(f"{qn('w:docDefaults')}/{qn('w:rPrDefault')}/{qn('w:rPr')}/{qn('w:sz')}")
        self.default_size = _half_points(size)


def extract_docx(path: Path) -> list[Block]:
    doc = Document(str(path))
    blocks: list[Block] = []
    _read(doc.element.body, _Context(doc), blocks, in_toc=False)
    return blocks


def _read(container, ctx: _Context, blocks: list[Block], in_toc: bool) -> None:
    """Read paragraphs and tables in document order, going into content controls."""
    for element in container.iterchildren():
        if element.tag == qn("w:p"):
            _read_paragraph(element, ctx, blocks, in_toc)
        elif element.tag == qn("w:tbl"):
            blocks.extend(_images_in(element, ctx.doc))
            markdown = _table_to_markdown(DocxTable(element, ctx.doc))
            if markdown:
                blocks.append(Table(markdown))
        elif element.tag == qn("w:sdt"):  # a content control: read what is inside it
            content = element.find(qn("w:sdtContent"))
            if content is not None:
                _read(content, ctx, blocks, in_toc or _is_toc_control(element))
        elif element.tag == qn("w:customXml"):  # another wrapper around ordinary content
            _read(element, ctx, blocks, in_toc)


def _read_paragraph(element, ctx: _Context, blocks: list[Block], in_toc: bool) -> None:
    doc = ctx.doc
    blocks.extend(_images_in(element, doc))
    text = paragraph_text(element).strip()
    if not text:
        return
    paragraph = DocxParagraph(element, doc)
    toc_level = _toc_level(paragraph)
    if in_toc or toc_level:
        entry = _toc_line(text, toc_level or 1)
        # Inside Word's ToC box, a line without a page number is the box's title ("Contents"), not an entry.
        if entry.page is not None or not in_toc:
            blocks.append(entry)
        return
    level = _outline_level(paragraph)
    if level:
        blocks.append(Heading(level, text))
        return
    look = _look(element, paragraph, ctx)
    blocks.append(
        Paragraph(
            text,
            indent=_indent(paragraph, paragraph_text(element)),
            bold=look.bold,
            size=look.size,
            listed=_in_list(paragraph),
        )
    )


def paragraph_text(element) -> str:
    """A paragraph's text, including text inside inline content controls, links and tracked insertions.

    (python-docx's own `paragraph.text` reads only plain runs and links, so it misses some text.)
    """
    parts: list[str] = []

    def visit(node) -> None:
        for child in node:
            if child.tag in _NOT_TEXT:
                continue
            if child.tag == qn("w:t"):
                parts.append(child.text or "")
            elif child.tag == qn("w:tab"):
                parts.append("\t")
            elif child.tag in (qn("w:br"), qn("w:cr")):
                parts.append("\n")
            else:
                visit(child)

    visit(element)
    return "".join(parts)


def _indent(paragraph: DocxParagraph, raw_text: str) -> float:
    """Points from the left margin: the paragraph's own left indent, plus leading tabs and spaces typed in it."""
    try:
        left = paragraph.paragraph_format.left_indent
    except (ValueError, TypeError):  # a malformed value in the file
        left = None
    leading = raw_text[: len(raw_text) - len(raw_text.lstrip())]
    return (left.pt if left is not None else 0) + 36 * leading.count("\t") + 4 * leading.count(" ")


@dataclass
class _Look:
    bold: bool  # every piece of text in the paragraph is bold
    size: float | None  # the font size (points) most of its text uses; None if it cannot be told


def _look(element, paragraph: DocxParagraph, ctx: _Context) -> _Look:
    """How the paragraph looks, as Word works it out: the text's own formatting, else its character
    style, else the paragraph's style (and the styles that one is based on), else the document's defaults.
    """
    runs = [(run, text) for run, text in _text_runs(element) if text.strip()]
    if not runs:
        return _Look(False, None)
    sizes: Counter = Counter()
    bold = True
    for run, text in runs:
        rpr = run.find(qn("w:rPr"))
        char_style = ctx.styles.get(_val(rpr.find(qn("w:rStyle")))) if rpr is not None and rpr.find(qn("w:rStyle")) is not None else None
        run_bold = _on_off(rpr.find(qn("w:b"))) if rpr is not None else None
        if run_bold is None:
            run_bold = _from_styles(char_style, lambda style: style.font.bold)
        if run_bold is None:
            run_bold = _from_styles(paragraph.style, lambda style: style.font.bold)
        bold = bold and bool(run_bold)
        size = _half_points(rpr.find(qn("w:sz"))) if rpr is not None else None
        if size is None:
            size = _from_styles(char_style, lambda style: style.font.size.pt if style.font.size else None)
        if size is None:
            size = _from_styles(paragraph.style, lambda style: style.font.size.pt if style.font.size else None)
        sizes[size if size is not None else ctx.default_size] += len(text.strip())
    common = sizes.most_common(1)[0][0]
    return _Look(bold, common)


def _text_runs(element):
    """The runs that hold the paragraph's own text (the same text paragraph_text reads), with their text."""
    for child in element:
        if child.tag in _NOT_TEXT:
            continue
        if child.tag == qn("w:r"):
            yield child, "".join(t.text or "" for t in child.iter(qn("w:t")))
        else:
            yield from _text_runs(child)


def _in_list(paragraph: DocxParagraph) -> bool:
    """True for an item of a Word numbered or bulleted list (on the paragraph, or through its style)."""
    ppr = paragraph._p.pPr
    if ppr is not None and ppr.numPr is not None:
        return True
    style = paragraph.style
    for _ in range(_MAX_STYLE_DEPTH):
        if style is None:
            return False
        style_ppr = style.element.pPr
        if style_ppr is not None and style_ppr.numPr is not None:
            return True
        style = style.base_style
    return False


def _from_styles(style, read):
    """The first value a style, or a style it is based on, sets; None if none sets it."""
    for _ in range(_MAX_STYLE_DEPTH):
        if style is None:
            return None
        value = read(style)
        if value is not None:
            return value
        style = style.base_style
    return None


def _on_off(element) -> bool | None:
    """A Word on/off setting such as <w:b/> or <w:b w:val="0"/>; None when not set."""
    if element is None:
        return None
    return (element.get(qn("w:val")) or "true").lower() not in ("0", "false", "off")


def _half_points(element) -> float | None:
    """A Word size setting (in half-points) as points; None when not set or malformed."""
    try:
        return int(element.get(qn("w:val"))) / 2 if element is not None else None
    except (TypeError, ValueError):
        return None


def _val(element) -> str | None:
    return element.get(qn("w:val")) if element is not None else None


def _toc_level(paragraph: DocxParagraph) -> int | None:
    """The level of a table-of-contents entry (styles "toc 1", "toc 2"...), or None for other paragraphs.

    Read from the style's id as well as its name: a document can use the id "TOC2" without defining the style.
    """
    style_id = paragraph._p.pPr.pStyle.val if paragraph._p.pPr is not None and paragraph._p.pPr.pStyle is not None else ""
    name = paragraph.style.name if paragraph.style is not None and paragraph.style.name else ""
    for candidate in (name, style_id):
        if match := _TOC_STYLE.match(candidate or ""):
            return int(match.group(1))
    return None


def _outline_level(paragraph: DocxParagraph) -> int | None:
    """The heading level (1-9) Word gives this paragraph, or None for body text.

    Set on the paragraph itself, or on its style, or on a style that one is based on.
    """
    value = _outline_value(paragraph._p.pPr)
    style = paragraph.style
    for _ in range(_MAX_STYLE_DEPTH):
        if value is not None or style is None:
            break
        value = _outline_value(style.element.pPr)
        if value is None and style.name and (match := _HEADING_STYLE.match(style.name)):
            return int(match.group(1))
        style = style.base_style
    return None if value is None or value >= _BODY_TEXT else value + 1


def _outline_value(ppr) -> int | None:
    if ppr is None:
        return None
    element = ppr.find(qn("w:outlineLvl"))
    try:
        return int(element.get(qn("w:val"))) if element is not None else None
    except (TypeError, ValueError):
        return None


def _is_toc_control(sdt) -> bool:
    """True for the content control Word puts around a table of contents it inserted."""
    gallery = sdt.find(f"{qn('w:sdtPr')}/{qn('w:docPartObj')}/{qn('w:docPartGallery')}")
    return gallery is not None and (gallery.get(qn("w:val")) or "").lower() == "table of contents"


def _toc_line(text: str, level: int) -> TocLine:
    """'Installation<tab>12' or 'Installation ..... 12' -> title and page."""
    title, page = text, None
    if "\t" in text:
        title, _, last = text.rpartition("\t")
        page = last.strip() or None
    elif match := _TOC_PAGE.match(text):
        title, page = match.group("title"), match.group("page")
    return TocLine(level=level, title=re.sub(r"[\s.·…_\t-]+$", "", title).strip() or text, page=page)


def _images_in(element, doc: DocxDocument) -> list[Image]:
    images = []
    for blip in element.iter(qn("a:blip")):
        part = doc.part.related_parts.get(blip.get(qn("r:embed")))
        if part is not None:
            images.append(Image(data=part.blob, ext=part.partname.ext.lower()))
    return images


def _table_to_markdown(table: DocxTable) -> str:
    rows = [[_cell_text(cell.text) for cell in row.cells] for row in table.rows]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = [_row(rows[0]), _row(["---"] * width)]
    lines.extend(_row(row) for row in rows[1:])
    return "\n".join(lines)


def _cell_text(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"
