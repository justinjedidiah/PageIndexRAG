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


Block = Heading | Paragraph | Table | Image

IMAGE_REF = re.compile(r"\[image:(img-\d+)\]")


def image_placeholder(image_id: str) -> str:
    """Marker left in node text where an image appeared."""
    return f"[image:{image_id}]"
