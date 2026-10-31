"""Checks on uploaded files. Strictly PDF and Word (.docx); nothing else is accepted.

A .docx is itself a zip file, so "no zip files" means: reject .zip and anything else by name,
then check that the contents really are what the name claims, and refuse archives that
unpack to something huge (a zip bomb).
"""

import io
import re
import zipfile
from pathlib import PurePath

from .models import FileKind

ALLOWED = {".pdf": "pdf", ".docx": "docx"}
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000
MAX_COMPRESSION_RATIO = 200


class UploadError(ValueError):
    """The message is safe to show to the visitor."""


def safe_filename(name: str) -> str:
    """Keep only the base name, with odd characters replaced, so it is safe to store and show."""
    base = PurePath(name.replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")
    return base[:100] or "upload"


def validate_upload(
    filename: str, data: bytes, max_bytes: int, max_uncompressed: int = MAX_UNCOMPRESSED_BYTES
) -> FileKind:
    """Return the file kind, or raise UploadError saying why the file was refused."""
    suffix = PurePath(filename.replace("\\", "/")).suffix.lower()
    if suffix == ".zip":
        raise UploadError("ZIP archives are not accepted. Upload a .pdf or .docx file.")
    if suffix not in ALLOWED:
        raise UploadError("Only .pdf and .docx files are accepted.")
    if not data:
        raise UploadError("That file is empty.")
    if len(data) > max_bytes:
        raise UploadError(f"That file is larger than the {max_bytes // (1024 * 1024)} MB limit.")

    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise UploadError("That file is not a valid PDF.")
        return "pdf"

    _check_docx(data, max_uncompressed)
    return "docx"


def _check_docx(data: bytes, max_uncompressed: int) -> None:
    not_docx = UploadError("That file is not a valid Word (.docx) document.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise not_docx from None
    with archive:
        entries = archive.infolist()
        names = {entry.filename for entry in entries}
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise not_docx
        if len(entries) > MAX_ZIP_ENTRIES:
            raise UploadError("That document contains too many parts.")
        if sum(entry.file_size for entry in entries) > max_uncompressed:
            raise UploadError("That document is too large once unpacked.")
        for entry in entries:
            if entry.compress_size and entry.file_size / entry.compress_size > MAX_COMPRESSION_RATIO:
                raise UploadError("That document looks like a compression bomb and was refused.")
