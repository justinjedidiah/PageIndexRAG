"""Rough size of the LLM work an index needs, shown to the visitor before they spend their key on it."""

from dataclasses import dataclass

from ..models import DocumentIndex, IndexOptions
from ..storage import Store
from .split import estimate_tokens
from .summarize import should_describe

PROMPT_OVERHEAD_TOKENS = 250  # instructions around each section
SUMMARY_TOKENS = 70  # a child's summary as it appears in its parent's prompt
OUTPUT_TOKENS = {"short": 60, "detailed": 110}  # title + summary, per summary length
IMAGE_INPUT_TOKENS = 1500  # images vary with size; this is a flat guess
IMAGE_OUTPUT_TOKENS = 80
MAX_SECTION_CHARS = 12000  # matches summarize_tree's truncation


@dataclass(frozen=True)
class Estimate:
    sections: int  # all sections in the tree
    summaries: int  # sections that get an LLM summary (none with free previews)
    images: int  # images that will be described (tiny and unsupported ones are skipped)
    calls: int
    input_tokens: int
    output_tokens: int


def count_describable_images(index: DocumentIndex, store: Store) -> int:
    """Images an image-describing run would send to Claude. Opens every image, so callers may cache it."""
    doc_dir = store.doc_dir(index.doc_id)
    return sum(
        should_describe(record, (doc_dir / record.file).read_bytes())
        for record in index.images
        if (doc_dir / record.file).exists()
    )


def estimate_index(
    index: DocumentIndex,
    store: Store,
    options: IndexOptions | None = None,
    describable_images: int | None = None,
) -> Estimate:
    """The LLM work these options need on this tree. Options default to the index's own, then the Full preset.

    `describable_images` (from `count_describable_images`) skips opening the image files again.
    """
    options = options or index.options or IndexOptions()
    sections = list(index.root.walk())
    summaries = len(sections) if options.summaries == "llm" else 0
    input_tokens = 0
    if summaries:
        for node in sections:
            text_tokens = estimate_tokens(node.text[:MAX_SECTION_CHARS])
            input_tokens += PROMPT_OVERHEAD_TOKENS + text_tokens + SUMMARY_TOKENS * len(node.children)

    images = 0
    if options.describe_images:
        images = describable_images if describable_images is not None else count_describable_images(index, store)
    return Estimate(
        sections=len(sections),
        summaries=summaries,
        images=images,
        calls=summaries + images,
        input_tokens=input_tokens + images * IMAGE_INPUT_TOKENS,
        output_tokens=summaries * OUTPUT_TOKENS[options.summary_length] + images * IMAGE_OUTPUT_TOKENS,
    )
