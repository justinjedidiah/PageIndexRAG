"""LLM passes at index time: image descriptions and bottom-up node summaries."""

import base64
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from ..models import ImageRecord, Node
from .blocks import IMAGE_REF

# Image types the Claude API accepts. Others (e.g. EMF/WMF from Word) are skipped.
SUPPORTED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MIN_IMAGE_SIDE = 32  # pixels; smaller images are icons, bullets and rules. Bytes would be a poor test:
# a flat-colour chart or diagram is a tiny file but worth describing.
MAX_VISION_SIDE = 1568  # larger images are shrunk first (the API's recommended maximum); it keeps them under its 5 MB limit
MAX_VISION_BYTES = 5 * 1024 * 1024

OnStep = Callable[[str], None]
SummaryLength = Literal["short", "detailed"]
# Output caps per call. The prompt sets the length; these only stop a runaway reply.
SUMMARY_MAX_TOKENS: dict[str, int] = {"short": 256, "detailed": 512}


class _NodeSummary(BaseModel):
    title: str = Field(description="Short descriptive title for the section.")
    summary: str = Field(
        description="2-4 sentences: what the section covers and which questions it can answer. "
        "Mention any tables or images."
    )


class _ShortNodeSummary(BaseModel):
    title: str = Field(description="Short descriptive title for the section.")
    summary: str = Field(description="One or two short sentences: which questions the section answers.")


_SUMMARY_SCHEMAS: dict[str, type[BaseModel]] = {"short": _ShortNodeSummary, "detailed": _NodeSummary}


class _ImageDescription(BaseModel):
    description: str = Field(description="One or two sentences describing what the image shows.")
    tags: list[str] = Field(description="A few short keywords for finding this image.")


def should_describe(record: ImageRecord, data: bytes) -> bool:
    """True for a readable image the API accepts and that is big enough to be more than decoration."""
    if record.mime not in SUPPORTED_IMAGE_MIMES:
        return False
    try:
        with Image.open(BytesIO(data)) as image:  # reads the header only
            width, height = image.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return False
    return min(width, height) >= MIN_IMAGE_SIDE


def prepare_for_vision(data: bytes) -> tuple[bytes, str] | None:
    """The image as bytes and mime type to send to the API, shrunk if large. None if it still will not fit."""
    with Image.open(BytesIO(data)) as image:
        image.thumbnail((MAX_VISION_SIDE, MAX_VISION_SIDE))  # only ever shrinks
        buffer = BytesIO()
        if image.mode in ("RGBA", "LA", "P"):
            image.convert("RGBA").save(buffer, "PNG")
            mime = "image/png"
        else:
            image.convert("RGB").save(buffer, "JPEG", quality=85)
            mime = "image/jpeg"
    return (buffer.getvalue(), mime) if buffer.tell() <= MAX_VISION_BYTES else None


def describe_images(
    images: list[ImageRecord], doc_dir: Path, llm: BaseChatModel, workers: int = 6, on_step: OnStep | None = None
) -> None:
    """Fill in description and tags for each image worth describing."""
    model = llm.with_structured_output(_ImageDescription)

    def describe(record: ImageRecord) -> None:
        data = (doc_dir / record.file).read_bytes()
        prepared = prepare_for_vision(data) if should_describe(record, data) else None
        if prepared is None:
            return
        data, mime = prepared
        hint = f" The document's alt text for it is: {record.description}" if record.description else ""
        message = HumanMessage(
            content=[
                {"type": "text", "text": f"Describe this image from a document so it can be found by search.{hint}"},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}},
            ]
        )
        result = model.invoke([message])
        record.description = result.description
        record.tags = result.tags
        if on_step:
            on_step(f"Described image {record.id}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(describe, images))  # list() re-raises the first failure


def summarize_tree(
    root: Node,
    llm: BaseChatModel,
    images: list[ImageRecord],
    workers: int = 6,
    on_step: OnStep | None = None,
    max_chars: int = 12000,
    length: SummaryLength = "detailed",
) -> None:
    """Summarize every node, deepest level first, so parents are summarized from their children.

    Nodes on one level do not depend on each other, so each level runs in parallel.
    """
    model = llm.with_structured_output(_SUMMARY_SCHEMAS[length])
    descriptions = {record.id: record.description for record in images}

    levels: dict[int, list[Node]] = defaultdict(list)
    pending = [(root, 0)]
    while pending:
        node, depth = pending.pop()
        levels[depth].append(node)
        pending.extend((child, depth + 1) for child in node.children)

    def summarize(node: Node) -> None:
        result = model.invoke(_prompt(node, descriptions, max_chars, length))
        node.summary = result.summary
        node.summary_source = "llm"
        if _retitle(node):
            node.title = result.title
        if on_step:
            on_step(f'Summarized "{node.title}"')

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for depth in sorted(levels, reverse=True):
            list(pool.map(summarize, levels[depth]))


def _retitle(node: Node) -> bool:
    """Split parts get an LLM title. Merged and folded sections keep theirs: it is made of original headings."""
    return node.origin == "generated" and not node.location.covers


def _prompt(node: Node, descriptions: dict[str, str], max_chars: int, length: SummaryLength = "detailed") -> str:
    text = IMAGE_REF.sub(lambda m: f"[image: {descriptions.get(m.group(1)) or 'no description'}]", node.text)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[...truncated]"
    children = "\n".join(f"- {child.title}: {child.summary}" for child in node.children) or "(none)"
    return (
        "You are indexing a document so that an agent can find information by reading section summaries "
        "without opening the sections.\n\n"
        f"Section title: {node.title}\n\n"
        f"Section text:\n{text or '(no text of its own)'}\n\n"
        f"Subsections:\n{children}\n\n"
        + ("Write the summary in one or two short sentences. " if length == "short" else "Write the summary. ")
        + (
            "This section is one part of a longer section, cut by size: write a short descriptive title for it."
            if _retitle(node)
            else "For the title, keep the existing one."
        )
    )
