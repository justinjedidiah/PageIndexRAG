"""Headings formatted by hand: a recurring look becomes headings; one-off or list-like lines stay text."""

from docx import Document
from docx.shared import Pt

from pageindex_rag.indexing import build_index, preset_options
from pageindex_rag.indexing.blocks import Heading, Paragraph
from pageindex_rag.indexing.heading_looks import apply_heading_looks
from pageindex_rag.storage import Store

BODY = "This part explains the topic in detail and gives practical advice for owners of home solar systems."


def text(value: str) -> Paragraph:
    return Paragraph(value, size=11)


def bold(value: str, size: float = 11) -> Paragraph:
    return Paragraph(value, bold=True, size=size)


def headings(blocks):
    return [(b.level, b.title) for b in blocks if isinstance(b, Heading)]


def test_numbered_bold_lines_nest_by_their_numbers():
    blocks = [text("Home Solar Guide"), bold("1 Getting started"), bold("1.1 Installation"), text(BODY),
              bold("1.2 Wiring"), text(BODY), bold("2 Maintenance"), text(BODY)]
    result, found = apply_heading_looks(blocks)
    assert found
    assert headings(result) == [(1, "1 Getting started"), (2, "1.1 Installation"), (2, "1.2 Wiring"), (1, "2 Maintenance")]


def test_without_numbers_bigger_lines_are_above_bold_ones():
    blocks = [bold("Getting started", 16), text(BODY), bold("Installation"), text(BODY), bold("Wiring"), text(BODY),
              bold("Maintenance", 16), text(BODY), bold("Cleaning"), text(BODY), bold("Upkeep", 16), text(BODY)]
    result, found = apply_heading_looks(blocks)
    assert found
    assert headings(result) == [(1, "Getting started"), (2, "Installation"), (2, "Wiring"), (1, "Maintenance"),
                                (2, "Cleaning"), (1, "Upkeep")]


def test_recurring_bold_questions_are_an_faq():
    blocks = []
    for question in ("Is the climate warming?", "What are the risks?", "What can I do?", "Is there hope?"):
        blocks += [bold(question), text(BODY)]
    result, found = apply_heading_looks(blocks)
    assert found and [t for _, t in headings(result)] == ["Is the climate warming?", "What are the risks?",
                                                          "What can I do?", "Is there hope?"]


def test_a_one_off_bold_question_is_a_quote_not_a_heading():
    blocks = [bold("Getting started"), text(BODY), bold("“Why did nobody tell us sooner?”", 14), text(BODY),
              bold("Installation"), text(BODY), bold("Maintenance"), text(BODY)]
    result, found = apply_heading_looks(blocks)
    assert found and [t for _, t in headings(result)] == ["Getting started", "Installation", "Maintenance"]
    quote = next(b for b in result if "nobody tell" in getattr(b, "text", getattr(b, "title", "")))
    assert isinstance(quote, Paragraph)  # still ordinary text


def test_a_big_title_at_the_top_stays_text():
    blocks = [bold("Home Solar Guide", 24), text(BODY), bold("Getting started"), text(BODY), bold("Installation"),
              text(BODY), bold("Maintenance"), text(BODY)]
    result, found = apply_heading_looks(blocks)
    assert found and "Home Solar Guide" not in [t for _, t in headings(result)]


def test_a_run_of_bold_lines_with_nothing_between_is_a_list():
    names = [bold(n) for n in ("Ada Okonkwo", "Luis Vargas", "Priya Nair", "Tomasz Wrona", "Mei Chen")]
    blocks = [text(BODY), *names, text(BODY), text(BODY), text(BODY)]
    result, found = apply_heading_looks(blocks)
    assert not found and result == blocks


def test_lines_that_do_not_qualify():
    blocks = [
        bold("This bold line ends like a sentence."), text(BODY),
        Paragraph("Only a part of this is bold", size=11), text(BODY),  # partly bold: not "all bold"
        Paragraph("Step one", bold=True, size=11, listed=True), text(BODY),  # a Word list item
        bold("Figure 3 Roof angles"), text(BODY),  # a caption
        bold("This bold line is far too long to be a heading because it keeps going on and on"), text(BODY),
    ] * 2
    result, found = apply_heading_looks(blocks)
    assert not found and result == blocks


def test_a_document_that_bolds_everything_is_left_alone():
    blocks = []
    for i in range(6):
        blocks += [bold(f"Note number {i} about panels"), text("Short.")]
    result, found = apply_heading_looks(blocks)
    assert not found and result == blocks  # the "headings" would be most of the text


def test_a_short_document_needs_only_two():
    blocks = [bold("Feeding"), text(BODY), bold("Habitat"), text(BODY)]
    result, found = apply_heading_looks(blocks)
    assert found and [t for _, t in headings(result)] == ["Feeding", "Habitat"]


def word_doc(tmp_path, styled_headings: bool):
    doc = Document()
    doc.add_paragraph("Climate Change").runs[0].font.size = Pt(24)
    for question in ("Is the climate warming?", "What are the risks?", "What can I do?"):
        if styled_headings:
            doc.add_heading(question, level=1)
        else:
            doc.add_paragraph().add_run(question).bold = True
        doc.add_paragraph(BODY * 2)
        tip = doc.add_paragraph()
        tip.add_run("Tip").bold = True  # a recurring bold line inside each section
        doc.add_paragraph(BODY)
    path = tmp_path / f"climate-{styled_headings}.docx"
    doc.save(path)
    return path


def test_a_word_document_without_heading_styles(tmp_path, settings):
    index = build_index(word_doc(tmp_path, False), Store(tmp_path / "data"), settings, preset_options("free", settings))
    assert index.structure_source == "formatting"
    titles = [n.title for n in index.root.walk() if n.id != "root" and n.origin == "original"]
    assert titles[:2] == ["Is the climate warming?", "Tip"] and "Climate Change" not in titles


def test_formatting_is_not_used_when_the_document_has_headings(tmp_path, settings):
    index = build_index(word_doc(tmp_path, True), Store(tmp_path / "data"), settings, preset_options("free", settings))
    assert index.structure_source == "headings"
    assert "Tip" not in [n.title for n in index.root.walk()]  # bold lines inside styled sections stay text


# ---- inside a section too big to read in one go (documents with headings) ----

from pageindex_rag.indexing.heading_looks import apply_section_looks  # noqa: E402

LONG = BODY * 12  # about 300 tokens: over the small limit used below


def test_a_big_section_gets_its_bold_lines_as_sub_headings():
    blocks = [text("Guide"), Heading(1, "Setup"), text("Intro to the setup."),
              bold("Installing the rails"), text(LONG), bold("Mounting the panels"), text(LONG),
              bold("Connecting the cables"), text(LONG), Heading(1, "Upkeep"), text(BODY)]
    result = apply_section_looks(blocks, max_tokens=200)
    assert headings(result) == [(1, "Setup"), (2, "Installing the rails"), (2, "Mounting the panels"),
                                (2, "Connecting the cables"), (1, "Upkeep")]
    assert result[2] == text("Intro to the setup.")  # the text before the first one stays the section's own
    assert result[0] == text("Guide")  # text before the first heading is not touched


def test_a_single_bold_line_in_a_big_section_stays_text():
    blocks = [Heading(1, "Setup"), text(LONG), bold("Safety warning"), text(LONG), Heading(1, "Upkeep"), text(BODY)]
    assert apply_section_looks(blocks, max_tokens=200) == blocks  # cut by size later, not at the warning


def test_a_section_small_enough_to_read_is_left_alone():
    blocks = [Heading(1, "Setup"), bold("Rails"), text(BODY), bold("Panels"), text(BODY), Heading(1, "Upkeep")]
    assert apply_section_looks(blocks, max_tokens=2000) == blocks


def test_sub_heading_levels_go_at_most_two_below_the_section():
    blocks = [Heading(2, "Setup"),
              bold("Roof work", 16), text(LONG), bold("Rails"), text(LONG), Paragraph("NOTE ON SAFETY", size=11), text(LONG),
              bold("Roof checks", 16), text(LONG), bold("Panels"), text(LONG), Paragraph("NOTE ON WIRES", size=11), text(LONG)]
    result = apply_section_looks(blocks, max_tokens=200)
    assert headings(result) == [(2, "Setup"), (3, "Roof work"), (4, "Rails"), (4, "NOTE ON SAFETY"),
                                (3, "Roof checks"), (4, "Panels"), (4, "NOTE ON WIRES")]  # a third look is capped


def test_numbered_sub_headings_count_from_the_section():
    blocks = [Heading(1, "3 Setup"), bold("3.1 Rails"), text(LONG), bold("3.1.1 Bolts"), text(LONG), bold("3.2 Panels"), text(LONG)]
    result = apply_section_looks(blocks, max_tokens=200)
    assert headings(result) == [(1, "3 Setup"), (2, "3.1 Rails"), (3, "3.1.1 Bolts"), (2, "3.2 Panels")]


def big_section_doc(tmp_path):
    doc = Document()
    doc.add_heading("Setup", level=1)
    doc.add_paragraph("What this chapter covers.")
    for title in ("Installing the rails", "Mounting the panels", "Connecting the cables"):
        doc.add_paragraph().add_run(title).bold = True
        doc.add_paragraph(LONG)
    doc.add_heading("Upkeep", level=1)
    doc.add_paragraph(BODY)
    path = tmp_path / "big-section.docx"
    doc.save(path)
    return path


def test_a_word_document_with_a_big_section(tmp_path, settings):
    import dataclasses

    roomy = dataclasses.replace(settings, max_leaf_tokens=400)
    index = build_index(big_section_doc(tmp_path), Store(tmp_path / "data"), roomy, preset_options("free", roomy))
    setup = index.root.children[0]
    assert index.structure_source == "headings"
    assert [c.title for c in setup.children] == ["Installing the rails", "Mounting the panels", "Connecting the cables"]
    assert all(c.origin == "original" for c in setup.children)  # real titles from the document: never renamed
    assert setup.text == "What this chapter covers."


def test_not_used_when_splitting_is_switched_off(tmp_path, settings):
    import dataclasses

    roomy = dataclasses.replace(settings, max_leaf_tokens=400)
    options = preset_options("free", roomy, split_large=False)
    index = build_index(big_section_doc(tmp_path), Store(tmp_path / "data"), roomy, options)
    assert index.root.children[0].children == []  # the section is kept whole, as asked


def test_rows_of_numbers_are_never_headings():
    """A chart's axis labels can be bold and recur, but they are data, not titles."""
    blocks = []
    for axis in ("25 0.0 40", "20 −0.3 20", "0 20 40 60 80 Growth (%)"):
        blocks += [bold(axis), text(BODY)]
    result, found = apply_heading_looks(blocks)
    assert not found and result == blocks
