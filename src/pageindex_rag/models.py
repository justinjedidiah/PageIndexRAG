"""Data model: the navigable node tree, image records and the library."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, Field

FileKind = Literal["pdf", "docx", "markdown"]


class Location(BaseModel):
    """Where a node lives in the source file. Used only for citations."""

    kind: FileKind
    heading_path: list[str] = Field(default_factory=list)
    pdf_page_start: int | None = None  # 0-based index into the PDF
    pdf_page_end: int | None = None
    printed_page_start: str | None = None  # page label as printed on the page
    printed_page_end: str | None = None

    def label(self) -> str:
        if self.kind == "pdf" and self.printed_page_start:
            if self.printed_page_end and self.printed_page_end != self.printed_page_start:
                return f"pp. {self.printed_page_start}-{self.printed_page_end}"
            return f"p. {self.printed_page_start}"
        return " > ".join(self.heading_path) or "(document start)"


class Node(BaseModel):
    id: str
    title: str
    level: int
    origin: Literal["original", "generated"] = "original"
    summary: str = ""
    text: str = ""  # content directly under this node, before its first child
    location: Location
    children: list[Node] = Field(default_factory=list)

    def walk(self) -> Iterator[Node]:
        yield self
        for child in self.children:
            yield from child.walk()


class ImageRecord(BaseModel):
    id: str
    file: str  # path relative to the document's data folder
    mime: str
    node_id: str | None = None
    location: Location | None = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class TocEntry(BaseModel):
    """One entry of the source document's own ToC, kept as reference data."""

    title: str
    level: int
    page: str | None = None


class DocumentIndex(BaseModel):
    doc_id: str
    title: str
    source_path: str  # the original file name only, so saved indexes are safe to share
    kind: FileKind
    summary: str = ""
    source_toc: list[TocEntry] | None = None
    root: Node
    images: list[ImageRecord] = Field(default_factory=list)

    def node_map(self) -> dict[str, Node]:
        return {node.id: node for node in self.root.walk()}

    def parent_map(self) -> dict[str, str]:
        return {child.id: node.id for node in self.root.walk() for child in node.children}


class LibraryEntry(BaseModel):
    doc_id: str
    title: str
    kind: FileKind
    summary: str = ""
