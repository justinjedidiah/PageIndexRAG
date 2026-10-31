"""Plain data shown by the page. Dataclasses, so Reflex can read their fields in the UI."""

import dataclasses


@dataclasses.dataclass
class DocView:
    doc_id: str
    title: str
    kind: str
    summary: str
    sample: bool


@dataclasses.dataclass
class CitationView:
    ref: str  # "doc_id:node_id"
    doc_title: str
    title: str
    location: str


@dataclasses.dataclass
class ImageView:
    url: str
    description: str


@dataclasses.dataclass
class StepView:
    label: str
    note: str = ""  # what the model said before taking this step


@dataclasses.dataclass
class ChatMessage:
    role: str  # "user" or "assistant"
    text: str = ""
    steps: list[StepView] = dataclasses.field(default_factory=list)
    citations: list[CitationView] = dataclasses.field(default_factory=list)
    images: list[ImageView] = dataclasses.field(default_factory=list)
    found: bool = True
    pending: bool = False


@dataclasses.dataclass
class OutlineRowView:
    ref: str
    title: str
    summary: str
    indent: str  # CSS length, e.g. "1.5em"
