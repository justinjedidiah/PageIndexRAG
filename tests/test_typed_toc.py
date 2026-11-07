"""A contents page typed by hand: found near the start, checked against the body, used or ignored."""

from docx import Document
from docx.shared import Pt

from toc_rag.indexing import build_index, preset_options
from toc_rag.indexing.blocks import Heading, Paragraph, Table, TocLine
from toc_rag.indexing.typed_toc import apply_typed_toc
from toc_rag.storage import Store

P = Paragraph
BODY = "This part explains the topic in detail and gives practical advice."


def headings(blocks):
    return [(b.level, b.title) for b in blocks if isinstance(b, Heading)]


def test_a_numbered_contents_page_builds_the_outline():
    blocks = [
        P("Home Solar Guide"),
        P("Contents"),
        P("1 Getting started ........ 2"),
        P("1.1 Installation ........ 2"),
        P("1.2 Wiring\t3"),
        P("2 Maintenance ........ 5"),
        P("1 Getting started"), P(BODY),
        P("1.1 Installation"), P(BODY),
        P("1.2 Wiring"), P(BODY),
        P("2 Maintenance"), P(BODY),
    ]
    result, used = apply_typed_toc(blocks)
    assert used
    assert headings(result) == [(1, "1 Getting started"), (2, "1.1 Installation"), (2, "1.2 Wiring"), (1, "2 Maintenance")]
    assert [b for b in result if isinstance(b, TocLine)] == [
        TocLine(1, "1 Getting started", "2"), TocLine(2, "1.1 Installation", "2"),
        TocLine(2, "1.2 Wiring", "3"), TocLine(1, "2 Maintenance", "5"),
    ]
    text = [b.text for b in result if isinstance(b, Paragraph)]
    assert "Contents" not in text and not any("........" in t for t in text)  # the contents page is gone from the text
    assert text[0] == "Home Solar Guide"


def test_levels_come_from_indentation_when_entries_are_not_numbered():
    blocks = [
        P("Getting started ....... 2", indent=0), P("Installation ....... 2", indent=18),
        P("Wiring ....... 3", indent=18), P("Maintenance ....... 5", indent=0),
        P("Getting started"), P(BODY), P("Installation"), P(BODY), P("Wiring"), P(BODY), P("Maintenance"), P(BODY),
    ]
    result, used = apply_typed_toc(blocks)
    assert used and headings(result) == [(1, "Getting started"), (2, "Installation"), (2, "Wiring"), (1, "Maintenance")]


def test_numbering_may_differ_between_the_contents_page_and_the_body():
    blocks = [
        P("1. Getting started ... 2"), P("2. Maintenance ... 5"), P("3. Faults ... 7"),
        P("Getting started"), P(BODY), P("Maintenance"), P(BODY), P("Faults"), P(BODY),
    ]
    result, used = apply_typed_toc(blocks)
    assert used and headings(result) == [(1, "Getting started"), (1, "Maintenance"), (1, "Faults")]  # the body's own text


def test_roman_page_numbers_and_wrapped_titles():
    blocks = [
        P("Foreword ....... iii"),
        P("Choosing panels for a roof that faces"), P("east or west ....... 4"),  # one title, wrapped onto two lines
        P("Maintenance ....... 9"),
        P("Foreword"), P(BODY), P("Choosing panels for a roof that faces east or west"), P(BODY), P("Maintenance"), P(BODY),
    ]
    result, used = apply_typed_toc(blocks)
    assert used
    assert [b.title for b in result if isinstance(b, TocLine)] == ["Foreword", "Choosing panels for a roof that faces east or west", "Maintenance"]
    assert headings(result)[1] == (1, "Choosing panels for a roof that faces east or west")


def test_entries_are_found_in_order_so_a_mention_or_a_repeat_is_not_mistaken():
    blocks = [
        P("Overview ... 1"), P("Wiring ... 2"), P("Overview ... 5"),
        P("Overview"), P("This guide covers wiring and safety."),  # mentions wiring: not the section
        P("Wiring"), P(BODY),
        P("Overview"), P(BODY),  # the second "Overview" belongs to the third entry
    ]
    result, used = apply_typed_toc(blocks)
    assert used and headings(result) == [(1, "Overview"), (1, "Wiring"), (1, "Overview")]
    wiring = next(i for i, b in enumerate(result) if isinstance(b, Heading) and b.title == "Wiring")
    assert isinstance(result[wiring - 1], Paragraph) and result[wiring - 1].text.startswith("This guide covers")


def test_a_list_that_only_looks_like_a_contents_page_changes_nothing():
    blocks = [P("Price list"), P("Panel ..... 200"), P("Inverter ..... 900"), P("Cable ..... 30"), P(BODY), P(BODY)]
    result, used = apply_typed_toc(blocks)
    assert not used and result == blocks  # its entries are not section starts in the body


def test_an_out_of_date_contents_page_is_not_followed():
    blocks = [
        P("Introduction ... 1"), P("Setup ... 2"), P("Cleaning ... 3"), P("Faults ... 4"),
        P("Introduction"), P(BODY), P("Installing the panels"), P(BODY), P("Looking after them"), P(BODY),
    ]
    result, used = apply_typed_toc(blocks)
    assert not used and result == blocks  # only 1 of 4 entries found: too stale to trust


def test_a_list_far_from_the_start_is_not_a_contents_page():
    body = [P(f"{BODY} {i}.") for i in range(60)]
    late = [P("Panels ... 12"), P("Inverters ... 14"), P("Cables ... 16"), P("Panels"), P("Inverters"), P("Cables")]
    result, used = apply_typed_toc(body + late)
    assert not used and result == body + late


def test_a_list_of_figures_is_skipped():
    blocks = [
        P("List of figures"), P("Figure 1 Roof angles ..... 3"), P("Figure 2 Wiring diagram ..... 4"),
        P("Figure 3 Inverter ..... 6"),
        P("Figure 1 Roof angles"), P(BODY), P("Figure 2 Wiring diagram"), P(BODY), P("Figure 3 Inverter"), P(BODY),
    ]
    result, used = apply_typed_toc(blocks)
    assert not used and result == blocks  # captions must not become headings


def test_with_headings_the_contents_page_is_only_removed():
    blocks = [
        P("Contents"), P("Getting started ... 2"), P("Wiring ... 3"), P("Maintenance ... 5"),
        Heading(1, "Getting started"), P(BODY), Heading(2, "Wiring"), P(BODY), Heading(1, "Maintenance"), P(BODY),
        Table("| a | b |\n| --- | --- |\n| 1 | 2 |"),
    ]
    result, used = apply_typed_toc(blocks)
    assert not used  # the headings built the outline, not the contents page
    assert headings(result) == [(1, "Getting started"), (2, "Wiring"), (1, "Maintenance")]
    assert not any(isinstance(b, Paragraph) and "..." in b.text for b in result) and P("Contents") not in result
    assert [b.title for b in result if isinstance(b, TocLine)] == ["Getting started", "Wiring", "Maintenance"]


def test_a_word_document_with_only_a_typed_contents_page(tmp_path, settings):
    doc = Document()
    doc.add_paragraph("Home Solar Guide")
    doc.add_paragraph("Contents")
    for title, page, indent in (("Getting started", 2, 0), ("Installation", 2, 18), ("Wiring", 3, 18), ("Maintenance", 5, 0)):
        doc.add_paragraph(f"{title}\t{page}").paragraph_format.left_indent = Pt(indent)
    for title in ("Getting started", "Installation", "Wiring", "Maintenance"):
        doc.add_paragraph().add_run(title).bold = True  # formatted by hand: no heading style
        doc.add_paragraph(BODY * 3)
    path = tmp_path / "guide.docx"
    doc.save(path)

    index = build_index(path, Store(tmp_path / "data"), settings, preset_options("free", settings))

    assert index.structure_source == "typed_toc"
    assert [(n.title, [c.title for c in n.children]) for n in index.root.children] == [
        ("Getting started", ["Installation", "Wiring"]), ("Maintenance", []),
    ]
    assert index.root.text == "Home Solar Guide"
    assert [(e.title, e.level, e.page) for e in index.source_toc][:2] == [("Getting started", 1, "2"), ("Installation", 2, "2")]


def test_a_title_printed_over_two_lines_is_found():
    blocks = [
        P("Overview ... 1"), P("Box A: Global supply chains ... 2"), P("Outlook ... 3"),
        P("Overview"), P(BODY), P("Box A"), P("Global supply chains"), P(BODY), P("Outlook"), P(BODY),
    ]
    result, used = apply_typed_toc(blocks)
    assert used and headings(result) == [(1, "Overview"), (1, "Box A: Global supply chains"), (1, "Outlook")]
    assert P("Box A") not in result  # both lines became the one heading


def test_chapters_named_by_numeral_go_above_their_sections():
    blocks = [
        P("Overview ... 1"), P("I. Progress ... 2"), P("Resilience ... 2"), P("II. Debt ... 5"), P("Introduction ... 5"),
        P("Overview"), P(BODY), P("I. Progress"), P(BODY), P("Resilience"), P(BODY), P("II. Debt"), P(BODY),
        P("Introduction"), P(BODY),
    ]
    result, used = apply_typed_toc(blocks)
    assert used and headings(result) == [
        (2, "Overview"), (1, "I. Progress"), (2, "Resilience"), (1, "II. Debt"), (2, "Introduction"),
    ]
