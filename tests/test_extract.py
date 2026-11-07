import io

from docx import Document

from toc_rag.indexing.blocks import Heading, Image, Paragraph, Table
from toc_rag.indexing.docx_extract import extract_docx
from toc_rag.indexing.markdown_extract import extract_markdown

from .conftest import make_png


def test_markdown_extraction(tmp_path):
    (tmp_path / "crew.png").write_bytes(make_png())
    path = tmp_path / "doc.md"
    path.write_text(
        "# Title\n\nFirst para\nstill first.\n\n"
        "```\n# not a heading\n\ncode\n```\n\n"
        "## Crew\n\n![the crew](crew.png)\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n",
        encoding="utf-8",
    )

    blocks = extract_markdown(path)

    assert blocks[0] == Heading(1, "Title")
    assert blocks[1] == Paragraph("First para\nstill first.")
    assert "# not a heading" in blocks[2].text
    assert "code" in blocks[2].text  # fence content stays one block despite the blank line
    assert blocks[3] == Heading(2, "Crew")
    assert isinstance(blocks[4], Image) and blocks[4].alt == "the crew" and blocks[4].ext == "png"
    assert blocks[5].text.startswith("| a | b |")
    assert sum(isinstance(b, Heading) for b in blocks) == 2


def test_docx_extraction(tmp_path):
    doc = Document()
    doc.add_heading("Intro", level=1)
    doc.add_paragraph("Hello")
    doc.add_heading("Numbers", level=2)
    table = doc.add_table(rows=2, cols=2)
    for r, row in enumerate([["Name", "Value"], ["a|b", "2"]]):
        for c, text in enumerate(row):
            table.cell(r, c).text = text
    doc.add_picture(io.BytesIO(make_png()))
    path = tmp_path / "doc.docx"
    doc.save(path)

    blocks = extract_docx(path)

    assert blocks[0] == Heading(1, "Intro")
    assert blocks[1] == Paragraph("Hello")
    assert blocks[2] == Heading(2, "Numbers")
    assert blocks[3] == Table("| Name | Value |\n| --- | --- |\n| a\\|b | 2 |")
    assert isinstance(blocks[4], Image) and blocks[4].ext == "png"
