"""Source markers in the model's answer: [doc_id:node_id] for a section, [img:doc_id:img-1] for an image."""

import re

_BRACKET = re.compile(r"[ \t]*\[([^\[\]]+)\]")
_REF = re.compile(r"^(img:)?[\w-]+:[\w.-]+$")


def visible_prefix(partial: str) -> str:
    """The part of a half-written reply that is safe to show: no markers, and no `[` that is still open."""
    cut = partial.rfind("[")
    if cut != -1 and "]" not in partial[cut:]:
        partial = partial[:cut]
    return extract_markers(partial)[0]


def extract_markers(text: str) -> tuple[str, list[str], list[str]]:
    """Return the text without its markers, the section refs and the image refs, each in order and unique.

    A bracket is a marker only if every comma-separated part looks like a ref; anything else is left alone.
    """
    sections: list[str] = []
    images: list[str] = []

    def take(match: re.Match) -> str:
        refs = [part.strip() for part in re.split(r"[,;]", match.group(1))]
        if not all(_REF.match(ref) for ref in refs):
            return match.group(0)
        for ref in refs:
            if ref.startswith("img:"):
                images.append(ref.removeprefix("img:"))
            else:
                sections.append(ref)
        return ""

    clean = _BRACKET.sub(take, text).strip()
    return clean, list(dict.fromkeys(sections)), list(dict.fromkeys(images))
