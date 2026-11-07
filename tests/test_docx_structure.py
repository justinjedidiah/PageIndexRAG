"""How a Word document's structure is read: outline levels, content controls and Word's own ToC."""

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

from pageindex_rag.indexing import build_index, preset_options
from pageindex_rag.indexing.blocks import Heading, Paragraph, TocLine
from pageindex_rag.indexing.docx_extract import extract_docx
from pageindex_rag.storage import Store

W = nsdecls("w")
MC = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'


def outline(element_ppr, level_value: int) -> None:
    element_ppr.append(parse_xml(f'<w:outlineLvl {W} w:val="{level_value}"/>'))


def save(doc, tmp_path, name="doc.docx"):
    path = tmp_path / name
    doc.save(path)
    return path


def test_heading_styles_still_count(tmp_path):
    doc = Document()
    doc.add_heading("Intro", level=1)
    doc.add_heading("Detail", level=3)
    doc.add_paragraph("Text")
    assert extract_docx(save(doc, tmp_path)) == [Heading(1, "Intro"), Heading(3, "Detail"), Paragraph("Text")]


def test_a_custom_style_with_an_outline_level_is_a_heading(tmp_path):
    doc = Document()
    chapter = doc.styles.add_style("Chapter Title", WD_STYLE_TYPE.PARAGRAPH)
    outline(chapter.element.get_or_add_pPr(), 0)  # what "Outline level: Level 1" in Word's paragraph dialog sets
    point = doc.styles.add_style("Sub Point", WD_STYLE_TYPE.PARAGRAPH)
    point.base_style = doc.styles["Heading 2"]  # inherits its outline level
    doc.add_paragraph("Getting started", style="Chapter Title")
    doc.add_paragraph("Wiring", style="Sub Point")
    doc.add_paragraph("Body", style="Normal")
    assert extract_docx(save(doc, tmp_path)) == [Heading(1, "Getting started"), Heading(2, "Wiring"), Paragraph("Body")]


def test_an_outline_level_set_on_the_paragraph_wins(tmp_path):
    doc = Document()
    promoted = doc.add_paragraph("Made a heading by hand")
    outline(promoted._p.get_or_add_pPr(), 1)
    demoted = doc.add_heading("Styled as a heading, set to body text", level=1)
    outline(demoted._p.get_or_add_pPr(), 9)  # level 9 = body text
    assert extract_docx(save(doc, tmp_path)) == [
        Heading(2, "Made a heading by hand"),
        Paragraph("Styled as a heading, set to body text"),
    ]


def test_content_controls_are_read_like_normal_content(tmp_path):
    doc = Document()
    doc.add_paragraph("Before")
    block_control = parse_xml(
        f'<w:sdt {W}><w:sdtPr><w:alias w:val="Cover"/></w:sdtPr><w:sdtContent>'
        f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Cover page</w:t></w:r></w:p>'
        f'<w:p><w:r><w:t>Prepared for the board.</w:t></w:r></w:p>'
        f'</w:sdtContent></w:sdt>'
    )
    doc.element.body.insert(1, block_control)
    inline = doc.add_paragraph("Client: ")
    inline._p.append(parse_xml(
        f'<w:sdt {W}><w:sdtPr><w:text/></w:sdtPr><w:sdtContent><w:r><w:t>Acme Ltd</w:t></w:r></w:sdtContent></w:sdt>'
    ))
    assert extract_docx(save(doc, tmp_path)) == [
        Paragraph("Before"),
        Heading(1, "Cover page"),
        Paragraph("Prepared for the board."),
        Paragraph("Client: Acme Ltd"),
    ]


def test_tracked_changes_read_as_the_document_now_stands(tmp_path):
    doc = Document()
    paragraph = doc.add_paragraph("The pool is ")
    paragraph._p.append(parse_xml(
        f'<w:ins {W} w:id="1" w:author="a" w:date="2026-01-01T00:00:00Z"><w:r><w:t>two</w:t></w:r></w:ins>'
    ))
    paragraph._p.append(parse_xml(
        f'<w:del {W} w:id="2" w:author="a" w:date="2026-01-01T00:00:00Z"><w:r><w:delText>three</w:delText></w:r></w:del>'
    ))
    paragraph.add_run(" meters wide.")
    assert extract_docx(save(doc, tmp_path)) == [Paragraph("The pool is two meters wide.")]


def test_text_box_copies_do_not_repeat_in_the_paragraph(tmp_path):
    doc = Document()
    paragraph = doc.add_paragraph("Before the box.")
    box = '<w:txbxContent><w:p><w:r><w:t>Boxed note</w:t></w:r></w:p></w:txbxContent>'
    paragraph._p.append(parse_xml(
        f'<w:r {W} {MC}><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing>{box}</w:drawing></mc:Choice>'
        f'<mc:Fallback><w:pict>{box}</w:pict></mc:Fallback></mc:AlternateContent></w:r>'
    ))
    assert extract_docx(save(doc, tmp_path)) == [Paragraph("Before the box.")]


def word_toc() -> object:
    """A table of contents as Word inserts it: a content control marked as one, with TOC-styled entries."""
    entries = [("TOC1", "Getting started", "2"), ("TOC2", "Installation", "2"), ("TOC1", "Maintenance", "5")]
    rows = "".join(
        f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t>{title}</w:t></w:r><w:r><w:tab/></w:r>'
        f'<w:r><w:t>{page}</w:t></w:r></w:p>'
        for style, title, page in entries
    )
    return parse_xml(
        f'<w:sdt {W}><w:sdtPr><w:docPartObj><w:docPartGallery w:val="Table of Contents"/><w:docPartUnique/>'
        f'</w:docPartObj></w:sdtPr><w:sdtContent><w:p><w:r><w:t>Contents</w:t></w:r></w:p>{rows}</w:sdtContent></w:sdt>'
    )


def test_a_word_toc_becomes_reference_entries_not_text(tmp_path):
    doc = Document()
    doc.add_paragraph("Home Solar Guide")
    doc.element.body.insert(1, word_toc())
    doc.add_heading("Getting started", level=1)
    doc.add_paragraph("Check the roof.")
    blocks = extract_docx(save(doc, tmp_path))
    assert [b for b in blocks if isinstance(b, TocLine)] == [
        TocLine(1, "Getting started", "2"), TocLine(2, "Installation", "2"), TocLine(1, "Maintenance", "5"),
    ]  # levels from the style ids TOC1 / TOC2, which this file uses without defining the styles
    text = " ".join(b.text for b in blocks if isinstance(b, Paragraph))
    assert "Contents" not in text and "Maintenance" not in text


def test_toc_styled_paragraphs_outside_a_content_control_are_entries_too(tmp_path):
    doc = Document()
    for name in ("toc 1", "toc 2"):
        doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    doc.add_paragraph("Getting started\t2", style="toc 1")
    doc.add_paragraph("Installation ........ 3", style="toc 2")
    doc.add_heading("Getting started", level=1)
    assert extract_docx(save(doc, tmp_path)) == [
        TocLine(1, "Getting started", "2"), TocLine(2, "Installation", "3"), Heading(1, "Getting started"),
    ]


def test_the_index_records_where_its_structure_came_from(tmp_path, settings):
    store = Store(tmp_path / "data")
    free = preset_options("free", settings)

    doc = Document()
    doc.add_paragraph("Home Solar Guide")
    doc.element.body.insert(1, word_toc())
    doc.add_heading("Getting started", level=1)
    doc.add_paragraph("Check the roof. " * 5)
    doc.add_heading("Maintenance", level=1)
    doc.add_paragraph("Rinse the panels. " * 5)
    index = build_index(save(doc, tmp_path, "with-toc.docx"), store, settings, free)
    assert index.structure_source == "headings"
    assert [(e.title, e.page) for e in index.source_toc] == [("Getting started", "2"), ("Installation", "2"), ("Maintenance", "5")]
    assert "Contents" not in index.root.text and index.root.text == "Home Solar Guide"

    plain = Document()
    plain.add_paragraph("Just text. " * 5)
    index = build_index(save(plain, tmp_path, "plain.docx"), store, settings, free)
    assert index.structure_source == "none" and index.source_toc is None
