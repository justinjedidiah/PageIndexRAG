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
    covers: list[str] = Field(default_factory=list)  # titles of small sections merged or folded into this node

    def label(self) -> str:
        if self.kind == "pdf" and self.printed_page_start:
            if self.printed_page_end and self.printed_page_end != self.printed_page_start:
                return f"pp. {self.printed_page_start}-{self.printed_page_end}"
            return f"p. {self.printed_page_start}"
        path = " > ".join(self.heading_path)
        if self.covers:
            covered = ", ".join(self.covers)
            return f"{path} > {covered}" if path else covered
        return path or "(document start)"


class Node(BaseModel):
    id: str
    title: str
    level: int
    origin: Literal["original", "generated"] = "original"
    summary: str = ""
    summary_source: Literal["", "llm", "preview"] = ""  # "preview" = the first sentences of the text, no LLM
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


class IndexOptions(BaseModel):
    """What indexing does to a document. Tree changes are free; summaries and image descriptions use the LLM.

    Presets that bundle these live in `indexing/options.py`.
    """

    split_large: bool = True  # split sections too big to read in one go
    max_leaf_tokens: int = Field(default=2000, ge=1)
    small_sections: Literal["off", "merge", "fold"] = "merge"  # merge tiny siblings and 1-child chains; fold small subtrees
    min_leaf_tokens: int = Field(default=150, ge=0)
    summaries: Literal["llm", "preview"] = "llm"
    summary_length: Literal["short", "detailed"] = "detailed"  # LLM summaries only
    preview_sentences: int = Field(default=3, ge=1)
    describe_images: bool = True

    @property
    def uses_llm(self) -> bool:
        return self.summaries == "llm" or self.describe_images


class IndexedWith(BaseModel):
    """Which provider and models wrote a document's summaries and image descriptions. Never the key."""

    provider: str
    index_model: str = ""  # empty when summaries were free previews
    vision_model: str = ""  # empty when images were not described


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
    options: IndexOptions | None = None  # what was applied; None for indexes made before options existed
    indexed_with: IndexedWith | None = None  # None when no model was used (Free) or for older indexes
    # Where the tree's sections came from: the document's headings, else its typed contents page, else lines
    # formatted like headings, else nothing found (the text is cut by size). None for older indexes.
    structure_source: Literal["bookmarks", "headings", "typed_toc", "formatting", "none"] | None = None

    def node_map(self) -> dict[str, Node]:
        return {node.id: node for node in self.root.walk()}

    def parent_map(self) -> dict[str, str]:
        return {child.id: node.id for node in self.root.walk() for child in node.children}


class LibraryEntry(BaseModel):
    doc_id: str
    title: str
    kind: FileKind
    summary: str = ""
