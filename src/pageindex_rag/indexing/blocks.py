"""Flat content blocks that every extractor produces, in document order.

PDF blocks also carry the page they start on. The tree keeps pages as markers in node text
(`[page:N]`) while it is built and refined, so a split or merge never loses track of them; they are
turned into each node's page range and removed before anything reads the text (see pages.py).
"""

import re
from dataclasses import dataclass, field


class DocumentRejected(ValueError):
    """A document this app will not index (password, scanned, too long). The message is safe to show visitors."""


@dataclass
class Heading:
    level: int
    title: str
    page: int | None = field(default=None, compare=False)  # PDF only: 0-based page it starts on


@dataclass
class Paragraph:
    text: str
    # How far the paragraph starts from the left margin, in points (its indent plus leading tabs or spaces).
    # Only used to read the levels of a typed table of contents; never compared for anything else.
    indent: float = field(default=0, compare=False)
    # How it looks, for spotting lines formatted like headings in a document without heading styles.
    bold: bool = field(default=False, compare=False)  # all of its text is bold
    size: float | None = field(default=None, compare=False)  # font size in points (None: unknown)
    listed: bool = field(default=False, compare=False)  # an item of a Word numbered or bulleted list
    page: int | None = field(default=None, compare=False)  # PDF only: 0-based page it starts on


@dataclass
class Table:
    markdown: str
    page: int | None = field(default=None, compare=False)  # PDF only: 0-based page it starts on


@dataclass
class Image:
    data: bytes
    ext: str
    alt: str = ""
    page: int | None = field(default=None, compare=False)  # PDF only: 0-based page it starts on


@dataclass
class TocLine:
    """One entry of the document's own table of contents. Kept as reference data, never as text."""

    level: int
    title: str
    page: str | None = None


Block = Heading | Paragraph | Table | Image | TocLine

IMAGE_REF = re.compile(r"\[image:(img-\d+)\]")
PAGE_REF = re.compile(r"\[page:(\d+)\]")


def page_marker(page: int) -> str:
    """Marker left in node text where a new PDF page starts (removed once pages are worked out)."""
    return f"[page:{page}]"


def is_anchor_only(text: str) -> bool:
    """True for text that is nothing but image placeholders and page markers."""
    return not PAGE_REF.sub("", IMAGE_REF.sub("", text)).strip()


def image_placeholder(image_id: str) -> str:
    """Marker left in node text where an image appeared."""
    return f"[image:{image_id}]"
