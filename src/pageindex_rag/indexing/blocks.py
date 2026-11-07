"""Flat content blocks that every extractor produces, in document order."""

import re
from dataclasses import dataclass, field


@dataclass
class Heading:
    level: int
    title: str


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


@dataclass
class Table:
    markdown: str


@dataclass
class Image:
    data: bytes
    ext: str
    alt: str = ""


@dataclass
class TocLine:
    """One entry of the document's own table of contents. Kept as reference data, never as text."""

    level: int
    title: str
    page: str | None = None


Block = Heading | Paragraph | Table | Image | TocLine

IMAGE_REF = re.compile(r"\[image:(img-\d+)\]")


def image_placeholder(image_id: str) -> str:
    """Marker left in node text where an image appeared."""
    return f"[image:{image_id}]"
