"""Rough size of the LLM work an index needs, shown to the visitor before they spend their key on it."""

from dataclasses import dataclass

from ..models import DocumentIndex
from ..storage import Store
from .split import estimate_tokens
from .summarize import should_describe

PROMPT_OVERHEAD_TOKENS = 250  # instructions around each section
SUMMARY_TOKENS = 70  # a child's summary as it appears in its parent's prompt
OUTPUT_TOKENS = 110  # title + summary
IMAGE_INPUT_TOKENS = 1500  # images vary with size; this is a flat guess
IMAGE_OUTPUT_TOKENS = 80
MAX_SECTION_CHARS = 12000  # matches summarize_tree's truncation


@dataclass(frozen=True)
class Estimate:
    sections: int
    images: int  # images that will be described (tiny and unsupported ones are skipped)
    calls: int
    input_tokens: int
    output_tokens: int


def estimate_index(index: DocumentIndex, store: Store) -> Estimate:
    sections = list(index.root.walk())
    input_tokens = 0
    for node in sections:
        text_tokens = estimate_tokens(node.text[:MAX_SECTION_CHARS])
        input_tokens += PROMPT_OVERHEAD_TOKENS + text_tokens + SUMMARY_TOKENS * len(node.children)

    doc_dir = store.doc_dir(index.doc_id)
    images = sum(
        should_describe(record, (doc_dir / record.file).read_bytes())
        for record in index.images
        if (doc_dir / record.file).exists()
    )
    return Estimate(
        sections=len(sections),
        images=images,
        calls=len(sections) + images,
        input_tokens=input_tokens + images * IMAGE_INPUT_TOKENS,
        output_tokens=len(sections) * OUTPUT_TOKENS + images * IMAGE_OUTPUT_TOKENS,
    )
