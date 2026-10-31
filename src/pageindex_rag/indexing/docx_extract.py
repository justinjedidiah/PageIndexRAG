"""Word: heading styles (Heading 1/2/3...) give the structure directly."""

import re
from pathlib import Path

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph

from .blocks import Block, Heading, Image, Paragraph, Table

_HEADING_STYLE = re.compile(r"^heading (\d)$", re.IGNORECASE)


def extract_docx(path: Path) -> list[Block]:
    doc = Document(str(path))
    blocks: list[Block] = []
    for element in doc.element.body.iterchildren():
        if element.tag == qn("w:p"):
            blocks.extend(_images_in(element, doc))
            paragraph = DocxParagraph(element, doc)
            text = paragraph.text.strip()
            if not text:
                continue
            level = _heading_level(paragraph)
            blocks.append(Heading(level, text) if level else Paragraph(text))
        elif element.tag == qn("w:tbl"):
            blocks.extend(_images_in(element, doc))
            markdown = _table_to_markdown(DocxTable(element, doc))
            if markdown:
                blocks.append(Table(markdown))
    return blocks


def _heading_level(paragraph: DocxParagraph) -> int | None:
    style = paragraph.style
    match = _HEADING_STYLE.match(style.name) if style is not None and style.name else None
    return int(match.group(1)) if match else None


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
