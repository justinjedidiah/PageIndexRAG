"""Pictures of whole PDF pages, for "View page": a citation shown as the page itself, not only its text."""

import io
from pathlib import Path

import pypdfium2 as pdfium

from .indexing.pdf_layout import PDFIUM_LOCK

PAGE_WIDTH = 1200  # pixels: sharp in the source panel and still readable when enlarged
MAX_SCALE = 3.0


def render_page(pdf_path: Path, page: int, width: int = PAGE_WIDTH) -> bytes:
    """One page of a PDF as PNG bytes. Raises IndexError for a page the file does not have."""
    with PDFIUM_LOCK:  # pdfium is not safe to use from two threads at once
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            if not 0 <= page < len(pdf):
                raise IndexError(page)
            pdf_page = pdf[page]
            try:
                page_width, _ = pdf_page.get_size()
                picture = pdf_page.render(scale=min(MAX_SCALE, width / max(page_width, 1))).to_pil()
            finally:
                pdf_page.close()
        finally:
            pdf.close()
    buffer = io.BytesIO()
    picture.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
