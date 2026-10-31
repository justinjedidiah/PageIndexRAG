import io
import zipfile

import pytest
from docx import Document

from pageindex_rag.uploads import UploadError, safe_filename, validate_upload

MAX = 1024 * 1024


def real_docx() -> bytes:
    buffer = io.BytesIO()
    document = Document()
    document.add_heading("Hello", level=1)
    document.save(buffer)
    return buffer.getvalue()


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_accepts_a_real_docx_and_pdf():
    assert validate_upload("Report.DOCX", real_docx(), MAX) == "docx"
    assert validate_upload("a.pdf", b"%PDF-1.7\n...", MAX) == "pdf"


def test_rejects_zip_archives_by_name_even_if_they_contain_a_docx():
    with pytest.raises(UploadError, match="ZIP"):
        validate_upload("stuff.zip", real_docx(), MAX)


@pytest.mark.parametrize("name", ["notes.txt", "page.html", "old.doc", "readme.md", "noextension", "x.docx.exe"])
def test_rejects_other_file_types(name):
    with pytest.raises(UploadError, match="Only .pdf and .docx"):
        validate_upload(name, real_docx(), MAX)


def test_rejects_names_that_lie_about_their_content():
    with pytest.raises(UploadError, match="not a valid PDF"):
        validate_upload("a.pdf", b"<html>not a pdf</html>", MAX)
    with pytest.raises(UploadError, match="not a valid Word"):
        validate_upload("a.docx", b"plain text", MAX)
    # A zip that is not a Word document.
    with pytest.raises(UploadError, match="not a valid Word"):
        validate_upload("a.docx", zip_bytes({"hello.txt": b"hi"}), MAX)
    # An Excel-like package: has Content_Types but no word/document.xml.
    with pytest.raises(UploadError, match="not a valid Word"):
        validate_upload("a.docx", zip_bytes({"[Content_Types].xml": b"<x/>", "xl/workbook.xml": b"<x/>"}), MAX)


def test_rejects_empty_and_oversized_files():
    with pytest.raises(UploadError, match="empty"):
        validate_upload("a.pdf", b"", MAX)
    with pytest.raises(UploadError, match="larger than the 1 MB"):
        validate_upload("a.pdf", b"%PDF-" + b"0" * MAX, MAX)


def test_rejects_a_zip_bomb():
    bomb = zip_bytes({"[Content_Types].xml": b"<x/>", "word/document.xml": b"<x/>", "word/media/huge.bin": b"\0" * 5_000_000})
    assert len(bomb) < 100_000  # tiny on the wire
    with pytest.raises(UploadError, match="too large once unpacked"):
        validate_upload("a.docx", bomb, MAX, max_uncompressed=1_000_000)
    with pytest.raises(UploadError, match="compression bomb"):
        validate_upload("a.docx", bomb, MAX)  # default size cap allows 5 MB, but the ratio is absurd


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.docx", "report.docx"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\My File (1).docx", "My File _1_.docx"),
        ("<script>.docx", "_script_.docx"),
        ("...", "upload"),
        ("a" * 300 + ".docx", ("a" * 300 + ".docx")[:100]),
    ],
)
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected
