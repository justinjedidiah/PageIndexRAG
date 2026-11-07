"""PDF documents: paragraphs from positioned lines, bookmarks as the outline, page ranges, refusals.

Test PDFs are drawn in code with reportlab, so no files are committed and no key is needed.
"""

import dataclasses

import pytest
from PIL import Image as PILImage
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

from pageindex_rag.indexing import DocumentRejected, build_index, preset_options
from pageindex_rag.indexing.blocks import Heading, Paragraph
from pageindex_rag.indexing.pdf_extract import extract_pdf
from pageindex_rag.storage import Store

BODY = "Clean the panels with plain water in the early morning, when the glass is cool. "
TOP = 780
WIDTH = 450


def make_pdf(path, pages, bookmarks=True, labels=None, encrypt=None):
    """Draw pages of ("h", title) headings (bold 16 pt) and ("p", text) paragraphs (11 pt, wrapped).

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
                c.setFont("Helvetica", 11)
                for line in simpleSplit(text, "Helvetica", 11, WIDTH):
                    c.drawString(72, y, line)
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

from pageindex_rag.indexing import pdf_cache  # noqa: E402


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
