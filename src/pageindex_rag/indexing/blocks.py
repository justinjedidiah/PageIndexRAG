"""Flat content blocks that every extractor produces, in document order."""

import re
from dataclasses import dataclass


@dataclass
class Heading:
    level: int
    title: str


@dataclass
class Paragraph:
    text: str


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
