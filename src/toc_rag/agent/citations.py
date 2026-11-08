"""Source markers in the model's answer: [doc_id:node_id] for a section, [img:doc_id:img-1] for an image."""

import re
from collections.abc import Collection

_BRACKET = re.compile(r"[ \t]*\[([^\[\]]+)\]")
_REF = re.compile(r"^(img:)?[\w-]+:[\w.-]+$")


def visible_prefix(partial: str, doc_ids: Collection[str] | None = None) -> str:
    """The part of a half-written reply that is safe to show: no markers, and no `[` that is still open."""
    cut = partial.rfind("[")
    if cut != -1 and "]" not in partial[cut:]:
        partial = partial[:cut]
    return extract_markers(partial, doc_ids)[0]


def extract_markers(text: str, doc_ids: Collection[str] | None = None) -> tuple[str, list[str], list[str]]:
    """Return the text without its markers, the section refs and the image refs, each in order and unique.

    A bracket is a marker only if every comma-separated part looks like a ref; anything else is left alone.
    A time like [10:30] or a ratio like [3:1] has the shape of a ref but is part of the answer: a ref whose
    document part is only digits stays in the text, unless `doc_ids` (the library's documents) has it.
    A made-up document id with letters ([bogus:9.9]) is still taken out: it is a marker that cites nothing.
    """

    def is_ref(ref: str) -> bool:
        if not _REF.match(ref):
            return False
        doc_id = ref.removeprefix("img:").partition(":")[0]
        return not doc_id.isdigit() or (doc_ids is not None and doc_id in doc_ids)

    sections: list[str] = []
    images: list[str] = []

    def take(match: re.Match) -> str:
        refs = [part.strip() for part in re.split(r"[,;]", match.group(1))]
        if not all(is_ref(ref) for ref in refs):
            return match.group(0)
        for ref in refs:
            if ref.startswith("img:"):
                images.append(ref.removeprefix("img:"))
            else:
                sections.append(ref)
        return ""

    clean = _BRACKET.sub(take, text).strip()
    return clean, list(dict.fromkeys(sections)), list(dict.fromkeys(images))
