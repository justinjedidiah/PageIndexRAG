"""Markdown: ATX headings (# ...) give the structure directly."""

import re
from pathlib import Path

from .blocks import Block, Heading, Image, Paragraph

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)")


def extract_markdown(path: Path) -> list[Block]:
    blocks: list[Block] = []
    paragraph: list[str] = []
    in_fence = False

    def flush() -> None:
        text = "\n".join(paragraph).strip()
        if text:
            blocks.append(Paragraph(text))
        paragraph.clear()

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            paragraph.append(line)
            continue
        if in_fence:
            paragraph.append(line)
            continue

        heading = _HEADING.match(line)
        if heading:
            flush()
            blocks.append(Heading(len(heading.group(1)), heading.group(2)))
            continue
        if not line.strip():
            flush()
            continue

        images = [_load_image(path.parent / src, alt) for alt, src in _IMAGE.findall(line)]
        images = [image for image in images if image]
        if images:
            flush()
            blocks.extend(images)
            line = _IMAGE.sub("", line)
        if line.strip():
            paragraph.append(line)

    flush()
    return blocks


def _load_image(path: Path, alt: str) -> Image | None:
    if not path.is_file():
        return None
    return Image(data=path.read_bytes(), ext=path.suffix.lstrip(".").lower(), alt=alt)
