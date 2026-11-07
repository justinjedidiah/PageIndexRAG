"""PDF documents: paragraphs from positioned lines, bookmarks as the outline, page ranges, refusals.

Test PDFs are drawn in code with reportlab, so no files are committed and no key is needed.
"""

import dataclasses

import pytest
from PIL import Image as PILImage
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

from toc_rag.indexing import DocumentRejected, build_index, preset_options
from toc_rag.indexing.blocks import Heading, Paragraph
from toc_rag.indexing.pdf_extract import extract_pdf
from toc_rag.storage import Store

BODY = "Clean the panels with plain water in the early morning, when the glass is cool. "
TOP = 780
WIDTH = 450


def make_pdf(path, pages, bookmarks=True, labels=None, encrypt=None):
    """Draw pages of ("h", title) headings (bold 16 pt) and ("p", text) paragraphs (11 pt, wrapped; ("p", text, indent)).

    With bookmarks on, each heading gets a bookmark pointing at it; a level can be given as ("h", title, 2).
    `labels` is a list of (first page index, style, start) for printed page numbers.
    """
    c = canvas.Canvas(str(path), encrypt=encrypt)
    count = 0
    for items in pages:
        y = TOP
        for item in items:
            kind, text = item[0], item[1]
            if kind == "h":
                if bookmarks:
                    key = f"b{count}"
                    count += 1
                    c.bookmarkPage(key, fit="XYZ", top=y + 18)
                    c.addOutlineEntry(text, key, level=(item[2] if len(item) > 2 else 1) - 1)
                c.setFont("Helvetica-Bold", 16)
                c.drawString(72, y, text)
                y -= 30
            else:
                indent = item[2] if len(item) > 2 else 0  # ("p", text, indent) for an indented line
                c.setFont("Helvetica", 11)
                for line in simpleSplit(text, "Helvetica", 11, WIDTH):
                    c.drawString(72 + indent, y, line)
                    y -= 14
                y -= 12  # the gap between paragraphs
        c.showPage()
    for first, style, start in labels or []:
        c.addPageLabel(first, style=style, start=start)
    c.save()
    return path


def index_of(tmp_path, settings, path, **options):
    roomy = dataclasses.replace(settings, max_leaf_tokens=2000)  # the test settings' limit is tiny
    return build_index(path, Store(tmp_path / "data"), roomy, preset_options("free", roomy, **options))


def all_text(index) -> str:
    return " ".join(f"{n.title} {n.text} {n.summary}" for n in index.root.walk())


def test_lines_become_paragraphs(tmp_path):
    path = make_pdf(tmp_path / "p.pdf", [[("p", BODY * 3), ("p", "A second paragraph."), ("p", "Wiring needs a well-insu-"),]])
    blocks = extract_pdf(path).blocks
    texts = [b.text for b in blocks if isinstance(b, Paragraph)]
    assert texts[0] == (BODY * 3).strip()  # three wrapped lines joined back into one paragraph
    assert texts[1] == "A second paragraph."
    assert all(b.page == 0 for b in blocks)


def test_a_word_hyphenated_at_the_line_end_is_put_back_together(tmp_path):
    c = canvas.Canvas(str(tmp_path / "h.pdf"))
    c.setFont("Helvetica", 11)
    c.drawString(72, 700, "Panels need regular main-")
    c.drawString(72, 686, "tenance to work well.")
    c.showPage()
    c.save()
    assert extract_pdf(tmp_path / "h.pdf").blocks == [Paragraph("Panels need regular maintenance to work well.")]


def test_bookmarks_build_the_outline_with_page_ranges(tmp_path, settings):
    long_text = BODY * 30  # more than a page
    path = make_pdf(
        tmp_path / "guide.pdf",
        [
            [("p", "Home Solar Guide. " + BODY)],
            [("p", "Preface text. " + BODY * 4)],
            [("h", "Getting started"), ("p", long_text[: len(long_text) // 2])],
            [("p", long_text[len(long_text) // 2 :])],
            [("h", "Wiring"), ("p", BODY * 6), ("h", "Cables", 2), ("p", BODY * 6), ("h", "Fuses", 2), ("p", BODY * 6)],
        ],
        labels=[(0, "ROMAN_LOWER", 1), (2, "ARABIC", 1)],
    )
    index = index_of(tmp_path, settings, path, small_sections="off")
    assert index.kind == "pdf" and index.structure_source == "bookmarks"
    assert [(n.title, [c.title for c in n.children]) for n in index.root.children] == [
        ("Getting started", []), ("Wiring", ["Cables", "Fuses"]),
    ]
    getting_started, wiring = index.root.children
    assert (getting_started.location.pdf_page_start, getting_started.location.pdf_page_end) == (2, 3)
    assert getting_started.location.label() == "pp. 1-2"  # the printed numbers: the first pages are i and ii
    assert wiring.children[1].location.label() == "p. 3"
    assert index.root.text.startswith("Home Solar Guide.")  # the text before the first bookmark
    assert index.root.location.label() == "pp. i-3"
    assert [(e.title, e.level, e.page) for e in index.source_toc] == [
        ("Getting started", 1, "1"), ("Wiring", 1, "3"), ("Cables", 2, "3"), ("Fuses", 2, "3"),
    ]
    assert "[page:" not in all_text(index)  # page markers never reach the text, titles or previews


def test_split_parts_get_their_own_pages(tmp_path, settings):
    pages = [[("h", "Maintenance"), ("p", f"Page one text. {BODY * 8}")]] + [
        [("p", f"Page {n} text. {BODY * 8}")] for n in ("two", "three", "four")
    ] + [[("h", "Repairs"), ("p", BODY)]]
    roomy = dataclasses.replace(settings, max_leaf_tokens=300)
    index = build_index(make_pdf(tmp_path / "m.pdf", pages), Store(tmp_path / "data"), roomy, preset_options("free", roomy))
    parts = index.root.children[0].children
    assert len(parts) >= 3 and all(p.origin == "generated" for p in parts)
    assert parts[0].location.pdf_page_start == 0 and parts[-1].location.pdf_page_end == 3
    starts = [p.location.pdf_page_start for p in parts]
    assert starts == sorted(starts) and len(set(starts)) > 1  # each part says where it is, not the whole section
    assert "[page:" not in all_text(index)


def test_a_bookmark_whose_title_is_not_on_the_page_goes_where_it_points(tmp_path):
    c = canvas.Canvas(str(tmp_path / "b.pdf"))
    for title, body in (("Introduction", "Welcome to the guide."), ("Safety rules", "Switch off the inverter first.")):
        c.bookmarkPage(title, fit="XYZ", top=800)
        c.addOutlineEntry(title, title, level=0)
        c.setFont("Helvetica", 11)
        c.drawString(72, 700, body)
        c.showPage()
    c.save()
    blocks = extract_pdf(tmp_path / "b.pdf").blocks
    assert [(type(b).__name__, getattr(b, "title", getattr(b, "text", ""))) for b in blocks] == [
        ("Heading", "Introduction"), ("Paragraph", "Welcome to the guide."),
        ("Heading", "Safety rules"), ("Paragraph", "Switch off the inverter first."),
    ]


def test_a_heading_run_into_its_first_line_is_split_off(tmp_path):
    c = canvas.Canvas(str(tmp_path / "r.pdf"))
    for title in ("Cleaning", "Repairs"):
        c.bookmarkPage(title, fit="XYZ", top=720)
        c.addOutlineEntry(title, title, level=0)
        c.setFont("Helvetica", 11)
        c.drawString(72, 700, f"{title}. Use a soft brush and water.")
        c.showPage()
    c.save()
    blocks = extract_pdf(tmp_path / "r.pdf").blocks
    assert blocks[:2] == [Heading(1, "Cleaning"), Paragraph("Use a soft brush and water.")]


def test_junk_bookmarks_are_ignored(tmp_path, settings):
    c = canvas.Canvas(str(tmp_path / "j.pdf"))
    for n, title in enumerate(("Feeding", "Habitat", "Migration"), start=1):
        c.bookmarkPage(f"p{n}", fit="XYZ", top=800)
        c.addOutlineEntry(f"Page {n}", f"p{n}", level=0)  # what some converters add: one per page, no titles
        c.setFont("Helvetica-Bold", 11)
        c.drawString(72, 740, title)
        c.setFont("Helvetica", 11)
        c.drawString(72, 720, BODY)
        c.showPage()
    c.save()
    content = extract_pdf(tmp_path / "j.pdf")
    assert content.outline is None and not any(isinstance(b, Heading) for b in content.blocks)
    index = index_of(tmp_path, settings, tmp_path / "j.pdf", small_sections="off")
    assert index.structure_source == "formatting"  # the bold titles are found instead
    assert [n.title for n in index.root.children] == ["Feeding", "Habitat", "Migration"]


def test_without_bookmarks_the_other_detectors_run(tmp_path, settings):
    pages = [[("h", "Feeding"), ("p", BODY * 4)], [("h", "Habitat"), ("p", BODY * 4)], [("h", "Migration"), ("p", BODY * 4)]]
    index = index_of(tmp_path, settings, make_pdf(tmp_path / "n.pdf", pages, bookmarks=False), small_sections="off")
    assert index.structure_source == "formatting"
    assert [(n.title, n.location.label()) for n in index.root.children] == [("Feeding", "p. 1"), ("Habitat", "p. 2"), ("Migration", "p. 3")]


def test_a_password_protected_pdf_is_refused(tmp_path):
    path = make_pdf(tmp_path / "locked.pdf", [[("p", BODY)]], encrypt="secret")
    with pytest.raises(DocumentRejected, match="password"):
        extract_pdf(path)


def test_a_scanned_pdf_is_refused(tmp_path):
    c = canvas.Canvas(str(tmp_path / "scan.pdf"))
    for _ in range(2):
        c.drawImage(ImageReader(PILImage.new("RGB", (200, 260), "white")), 50, 50, 500, 700)
        c.showPage()
    c.save()
    with pytest.raises(DocumentRejected, match="scanned"):
        extract_pdf(tmp_path / "scan.pdf")


def test_too_many_pages_is_refused_before_reading(tmp_path):
    path = make_pdf(tmp_path / "long.pdf", [[("p", "Short page.")] for _ in range(5)])
    with pytest.raises(DocumentRejected, match="5 pages. The limit here is 4"):
        extract_pdf(path, max_pages=4)


def test_too_much_text_is_refused_for_any_file_type(tmp_path, settings):
    from docx import Document

    doc = Document()
    for _ in range(20):
        doc.add_paragraph(BODY * 5)
    doc.save(tmp_path / "long.docx")
    tight = dataclasses.replace(settings, max_doc_tokens=1000)
    with pytest.raises(DocumentRejected, match="too long"):
        build_index(tmp_path / "long.docx", Store(tmp_path / "data"), tight, preset_options("free", tight))


# ---- the saved read, next to the PDF ----

import os  # noqa: E402

from toc_rag.indexing import pdf_cache  # noqa: E402


def test_the_read_is_saved_next_to_the_pdf_and_reused(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "guide.pdf", [[("h", "Cleaning"), ("p", BODY)], [("h", "Repairs"), ("p", BODY)]])
    first = pdf_cache.read_pdf(path, None)
    assert pdf_cache.saved_read_path(path) == tmp_path / "guide.pdf.read.json"
    assert pdf_cache.saved_read_path(path).is_file()

    def must_not_read(*args):
        raise AssertionError("the PDF was read again")

    monkeypatch.setattr(pdf_cache, "extract_pdf", must_not_read)
    again = pdf_cache.read_pdf(path, None)
    assert again == first  # the same blocks, labels and outline
    assert [b.page for b in again.blocks] == [b.page for b in first.blocks]  # pages kept too (not compared by ==)
    assert again.blocks[1].size == first.blocks[1].size and again.blocks[0].level == 1


def test_a_changed_file_with_the_same_name_size_and_time_is_read_again(tmp_path):
    path = tmp_path / "notes.pdf"
    make_pdf(path, [[("p", "Panels face south and need a rinse.")]])
    first = pdf_cache.read_pdf(path, None)
    stat = path.stat()
    make_pdf(path, [[("p", "Panels face north and need a rinse.")]])
    assert path.stat().st_size == stat.st_size
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # the same time too, as on a coarse file system
    second = pdf_cache.read_pdf(path, None)
    assert first.blocks[0].text.startswith("Panels face south") and second.blocks[0].text.startswith("Panels face north")


def test_a_saved_read_is_only_used_for_the_file_beside_it(tmp_path):
    """Two visitors upload different files with the same name: each gets only its own text."""
    one_dir, two_dir = tmp_path / "session-one" / "uploads", tmp_path / "session-two" / "uploads"
    one_dir.mkdir(parents=True)
    two_dir.mkdir(parents=True)
    one = make_pdf(one_dir / "guide.pdf", [[("p", "Visitor one's private notes on panels.")]])
    pdf_cache.read_pdf(one, None)
    # Even a copy of visitor one's saved read placed beside visitor two's file is refused: it is for other bytes.
    two = make_pdf(two_dir / "guide.pdf", [[("p", "Visitor two's own notes about wiring.")]])
    pdf_cache.saved_read_path(two).write_bytes(pdf_cache.saved_read_path(one).read_bytes())
    assert pdf_cache.read_pdf(two, None).blocks[0].text.startswith("Visitor two")


def test_a_damaged_saved_read_is_ignored(tmp_path):
    path = make_pdf(tmp_path / "guide.pdf", [[("p", "Panels face south and need a rinse.")]])
    pdf_cache.saved_read_path(path).write_text("{not json", encoding="utf-8")
    assert pdf_cache.read_pdf(path, None).blocks[0].text.startswith("Panels face south")
    pdf_cache.forget(path)
    assert not pdf_cache.saved_read_path(path).exists()


# ---- running headers and footers, titles over several lines ----


def test_running_footers_and_page_numbers_are_removed(tmp_path):
    c = canvas.Canvas(str(tmp_path / "f.pdf"))
    for n in range(1, 6):
        c.setFont("Helvetica", 9)
        footer = f"{n} Annual Solar Report 2026" if n % 2 == 0 else f"Annual Solar Report 2026 {n}"  # alternating sides
        c.drawString(72, 30, footer)
        c.drawString(500, 770, f"- {n} -")  # a page number at the top
        c.setFont("Helvetica", 11)
        c.drawString(72, 700, f"Body text of page {n} about panels and inverters.")
        c.drawString(72, 400, "Annual Solar Report 2026 is cited here in the body.")  # not in the margin: kept
        c.showPage()
    c.save()
    texts = [b.text for b in extract_pdf(tmp_path / "f.pdf").blocks]
    assert not any("Annual Solar Report 2026" in t and "cited" not in t for t in texts)
    assert not any(t.startswith("-") for t in texts)
    assert sum("cited here" in t for t in texts) == 5 and sum(t.startswith("Body text") for t in texts) == 5


def test_a_margin_line_on_one_page_only_is_kept(tmp_path):
    c = canvas.Canvas(str(tmp_path / "once.pdf"))
    for n in range(1, 6):
        c.setFont("Helvetica", 11)
        if n == 1:
            c.drawString(72, 770, "Prepared for the board of directors")  # a one-off line at the top
        c.drawString(72, 700, f"Body text of page {n} about panels and inverters.")
        c.showPage()
    c.save()
    assert extract_pdf(tmp_path / "once.pdf").blocks[0].text == "Prepared for the board of directors"


def test_a_bookmark_title_printed_over_two_lines_is_matched(tmp_path):
    c = canvas.Canvas(str(tmp_path / "box.pdf"))
    for label, subtitle in (("Box A", "Global supply chains"), ("Box B", "Energy prices")):
        c.bookmarkPage(label, fit="XYZ", top=760)
        c.addOutlineEntry(f"{label}: {subtitle}", label, level=0)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(72, 740, label)  # a small label, then the subtitle in a bigger font
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, 720, subtitle)
        c.setFont("Helvetica", 11)
        c.drawString(72, 690, "The box text starts here.")
        c.showPage()
    c.save()
    blocks = extract_pdf(tmp_path / "box.pdf").blocks
    assert blocks[:2] == [Heading(1, "Box A: Global supply chains"), Paragraph("The box text starts here.")]


# ---- figures and columns (pdf_layout.py) ----

import io as _io  # noqa: E402

from toc_rag.indexing.blocks import Image, Table  # noqa: E402
from tests.conftest import make_noisy_png  # noqa: E402

SENTENCE = "The output of the panels rises through the morning and falls again in the late afternoon."


def draw_chart(c, x, y, width=300, height=160):
    """A bar chart as PDF software draws one: axes, gridlines and bars as shapes, labels as loose text."""
    c.setLineWidth(0.5)
    c.line(x, y, x + width, y)
    c.line(x, y, x, y + height)
    for step in range(1, 5):
        c.line(x, y + step * height / 5, x + width, y + step * height / 5)
    for i, value in enumerate((40, 90, 65, 120, 80, 30)):
        c.rect(x + 10 + i * 48, y, 30, value, fill=1)
    c.setFont("Helvetica", 8)
    for step in range(5):
        c.drawString(x - 18, y + step * height / 5 - 3, str(step * 25))


def test_a_chart_becomes_a_picture_and_its_labels_leave_the_text(tmp_path):
    c = canvas.Canvas(str(tmp_path / "chart.pdf"))
    c.setFont("Helvetica", 11)
    c.drawString(72, 760, SENTENCE)
    draw_chart(c, 100, 500)
    c.setFont("Helvetica", 11)
    c.drawString(72, 440, "Sources: the national energy agency and our own survey of owners.")
    c.showPage()
    c.save()
    blocks = extract_pdf(tmp_path / "chart.pdf").blocks
    assert [type(b).__name__ for b in blocks] == ["Paragraph", "Image", "Paragraph"]
    picture = blocks[1]
    assert picture.ext == "png" and picture.page == 0 and picture.data.startswith(b"\x89PNG")
    assert PILImage.open(_io.BytesIO(picture.data)).size[0] > 600  # rendered sharp, not at screen size
    assert all(not b.text.strip().startswith(("0", "25", "50")) for b in blocks if isinstance(b, Paragraph))
    assert blocks[2].text.startswith("Sources:")  # the note under the chart stays text


def test_a_ruled_table_becomes_a_markdown_table(tmp_path):
    c = canvas.Canvas(str(tmp_path / "table.pdf"))
    c.setFont("Helvetica", 11)
    c.drawString(72, 760, SENTENCE)
    rows = [("Panel", "Output", "Price"), ("Mono", "400 W", "210"), ("Poly", "350 W", "160"), ("Thin film", "120 W", "90")]
    left, top, col, row = 100, 700, 120, 20
    for r in range(len(rows) + 1):
        c.line(left, top - r * row, left + 3 * col, top - r * row)
    for k in range(4):
        c.line(left + k * col, top, left + k * col, top - len(rows) * row)
    for r, cells in enumerate(rows):
        for k, cell in enumerate(cells):
            c.drawString(left + k * col + 4, top - r * row - 14, cell)
    c.showPage()
    c.save()
    blocks = extract_pdf(tmp_path / "table.pdf").blocks
    tables = [b for b in blocks if isinstance(b, Table)]
    assert len(tables) == 1 and not any(isinstance(b, Image) for b in blocks)
    assert tables[0].markdown.splitlines()[:3] == ["| Panel | Output | Price |", "| --- | --- | --- |", "| Mono | 400 W | 210 |"]
    assert not any("Thin film" in b.text for b in blocks if isinstance(b, Paragraph))


def test_embedded_pictures_are_cut_out_and_tiny_or_background_ones_skipped(tmp_path):
    c = canvas.Canvas(str(tmp_path / "pictures.pdf"))
    c.drawImage(ImageReader(PILImage.new("RGB", (600, 800), "white")), 0, 0, 612, 792)  # a page background
    c.setFont("Helvetica", 11)
    c.drawString(72, 760, SENTENCE)
    c.drawImage(ImageReader(_io.BytesIO(make_noisy_png(200, 120))), 100, 500, 300, 180)
    c.drawImage(ImageReader(PILImage.new("RGB", (16, 16), "red")), 100, 450, 12, 12)  # an icon
    c.drawString(72, 400, "A photo of the roof after the panels were fitted last spring.")
    c.showPage()
    c.save()
    blocks = extract_pdf(tmp_path / "pictures.pdf").blocks
    assert [type(b).__name__ for b in blocks] == ["Paragraph", "Image", "Paragraph"]


def test_a_framed_box_keeps_its_text_and_gives_up_only_its_chart(tmp_path):
    c = canvas.Canvas(str(tmp_path / "box.pdf"))
    c.rect(60, 120, 490, 640)  # the box frame around everything
    c.setFont("Helvetica", 11)
    for i in range(4):
        c.drawString(72, 740 - i * 14, SENTENCE)
    draw_chart(c, 120, 420)
    c.setFont("Helvetica", 11)
    for i in range(4):
        c.drawString(72, 360 - i * 14, SENTENCE)
    c.showPage()
    c.save()
    blocks = extract_pdf(tmp_path / "box.pdf").blocks
    assert [type(b).__name__ for b in blocks] == ["Paragraph", "Image", "Paragraph"]
    assert blocks[0].text.count("morning") == 4 and blocks[2].text.count("morning") == 4


def test_two_columns_are_read_left_then_right(tmp_path):
    c = canvas.Canvas(str(tmp_path / "columns.pdf"))
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, 760, "Looking after a home solar system")  # a title over both columns
    c.setFont("Helvetica", 10)
    left = simpleSplit("Left column. " + BODY * 5, "Helvetica", 10, 220)
    right = simpleSplit("Right column. " + BODY * 5, "Helvetica", 10, 220)
    for i, (a, b) in enumerate(zip(left, right)):
        c.drawString(72, 730 - i * 13, a)
        c.drawString(320, 730 - i * 13, b)
    c.showPage()
    c.save()
    texts = [b.text for b in extract_pdf(tmp_path / "columns.pdf").blocks]
    assert texts[0] == "Looking after a home solar system"
    assert texts[1].startswith("Left column.") and texts[2].startswith("Right column.")
    assert "Right column" not in texts[1]


def test_figures_reach_the_index_cited_by_their_own_page(tmp_path, settings):
    path = tmp_path / "report.pdf"
    c = canvas.Canvas(str(path))  # "Output" runs over two pages, with a chart on its first; then "Costs"
    for title, chart in (("Output", True), (None, False), ("Costs", False)):
        if title:
            c.bookmarkPage(title, fit="XYZ", top=790)
            c.addOutlineEntry(title, title, level=0)
            c.setFont("Helvetica-Bold", 16)
            c.drawString(72, 760, title)
        c.setFont("Helvetica", 11)
        for i, line in enumerate(simpleSplit(SENTENCE * 3, "Helvetica", 11, WIDTH)):
            c.drawString(72, 730 - i * 14, line)
        if chart:
            draw_chart(c, 100, 420)
        c.showPage()
    c.save()
    index = index_of(tmp_path, settings, path, small_sections="off")
    output = index.root.children[0]
    assert len(index.images) == 1 and output.location.label() == "pp. 1-2"
    record = index.images[0]
    assert record.node_id == output.id and record.location.label() == "p. 1"  # its own page, not the section's
    assert record.mime == "image/png" and (tmp_path / "data" / "docs" / index.doc_id / record.file).is_file()



# ---- a typed contents page in a PDF without bookmarks (typed_toc.py with page numbers) ----


def test_a_contents_page_builds_the_outline_checked_against_page_numbers(tmp_path, settings):
    pages = [
        [("p", "Home Solar Guide")],
        [("p", "Contents"), ("p", "Getting started ........ 1"), ("p", "Wiring ........ 2", 18), ("p", "Maintenance ........ 3")],
        [("p", "Getting started"), ("p", BODY * 4), ("p", "Wiring"), ("p", "See the wiring section for cable sizes.")],  # a mention
        [("p", "Wiring"), ("p", BODY * 4)],
        [("p", "Maintenance"), ("p", BODY * 4)],
    ]
    path = make_pdf(tmp_path / "guide.pdf", pages, labels=[(0, "ROMAN_LOWER", 1), (2, "ARABIC", 1)])
    index = index_of(tmp_path, settings, path, small_sections="off")
    assert index.structure_source == "typed_toc"
    assert [(n.title, n.location.label(), [c.title for c in n.children]) for n in index.root.children] == [
        ("Getting started", "pp. 1-2", ["Wiring"]), ("Maintenance", "p. 3", []),  # a section spans its subsections
    ]
    wiring = index.root.children[0].children[0]
    assert wiring.location.label() == "p. 2"  # the real section, not the short "Wiring" line on page 1
    assert "See the wiring section" in index.root.children[0].text


def test_without_page_labels_the_offset_is_learnt(tmp_path, settings):
    """Printed numbers start after a cover and a contents page: "1" is the third page."""
    pages = [
        [("p", "Home Solar Guide")],
        [("p", "Contents"), ("p", "Getting started ........ 1"), ("p", "Wiring ........ 2"), ("p", "Maintenance ........ 3")],
        [("p", "Getting started"), ("p", BODY * 4)],
        [("p", "Wiring"), ("p", BODY * 4)],
        [("p", "Maintenance"), ("p", BODY * 4), ("p", "Wiring"), ("p", "is covered on page 2.")],
    ]
    index = index_of(tmp_path, settings, make_pdf(tmp_path / "plain.pdf", pages), small_sections="off")
    assert index.structure_source == "typed_toc"
    assert [(n.title, n.location.pdf_page_start) for n in index.root.children] == [
        ("Getting started", 2), ("Wiring", 3), ("Maintenance", 4),
    ]


def test_a_contents_page_over_two_pages_with_different_margins_keeps_its_levels(tmp_path):
    c = canvas.Canvas(str(tmp_path / "margins.pdf"))
    entries = [  # (page of the contents, left edge, indent, title, page number)
        (0, 72, 0, "Getting started", 3), (0, 72, 18, "Installation", 3), (0, 72, 0, "Wiring", 4),
        (1, 40, 18, "Cables", 4), (1, 40, 0, "Maintenance", 5), (1, 40, 18, "Cleaning", 5),
    ]
    for page in (0, 1):
        c.setFont("Helvetica", 11)
        for row, (on, left, indent, title, number) in enumerate(e for e in entries if e[0] == page):
            c.drawString(left + indent, 700 - row * 26, f"{title} ........ {number}")
        c.showPage()
    for pair in (("Getting started", "Installation"), ("Wiring", "Cables"), ("Maintenance", "Cleaning")):
        for top, title in zip((700, 420), pair):  # two sections a page, on pages 3, 4 and 5
            c.setFont("Helvetica", 11)
            c.drawString(72, top, title)
            for i, line in enumerate(simpleSplit(BODY * 2, "Helvetica", 11, WIDTH)):
                c.drawString(72, top - 30 - i * 14, line)
        c.showPage()
    c.save()
    from toc_rag.indexing.typed_toc import apply_typed_toc

    content = extract_pdf(tmp_path / "margins.pdf")
    blocks, used = apply_typed_toc(content.blocks, content.page_labels)
    assert used
    assert [(b.level, b.title) for b in blocks if isinstance(b, Heading)] == [
        (1, "Getting started"), (2, "Installation"), (1, "Wiring"), (2, "Cables"), (1, "Maintenance"), (2, "Cleaning"),
    ]


def test_roman_page_numbers_in_a_footer_are_removed(tmp_path):
    c = canvas.Canvas(str(tmp_path / "roman.pdf"))
    for n, roman in enumerate(("i", "ii", "iii", "iv", "v")):
        c.setFont("Helvetica", 9)
        c.drawString(72, 30, f"Annual Solar Report {roman}")
        c.setFont("Helvetica", 11)
        c.drawString(72, 700, f"Front matter page {n + 1} about panels and inverters.")
        c.showPage()
    c.save()
    assert [b.text for b in extract_pdf(tmp_path / "roman.pdf").blocks] == [
        f"Front matter page {n} about panels and inverters." for n in range(1, 6)
    ]
