"""Build the node tree from a flat list of blocks."""

from ..models import FileKind, Location, Node
from .blocks import Block, Heading, Image, Paragraph, TocLine, image_placeholder, page_marker


def build_tree(blocks: list[Block], title: str, kind: FileKind) -> tuple[Node, list[tuple[str, Image]]]:
    """Nest blocks under their headings.

    Content before the first heading goes to the root. Images are replaced by
    placeholders in the text and returned separately with their ids. Where a PDF block starts on a
    new page, a page marker goes in first (a section's own marker when its heading opens a page).
    """
    root = Node(id="root", title=title, level=0, location=Location(kind=kind))
    stack = [root]
    parts: dict[str, list[str]] = {root.id: []}
    images: list[tuple[str, Image]] = []
    last_page: int | None = None

    for block in blocks:
        if isinstance(block, TocLine):
            continue  # the document's own table of contents: reference data, not text
        if isinstance(block, Heading):
            while stack[-1].level >= block.level:
                stack.pop()
            parent = stack[-1]
            prefix = "" if parent.id == "root" else f"{parent.id}."
            node = Node(
                id=f"{prefix}{len(parent.children) + 1}",
                title=block.title,
                level=block.level,
                location=Location(kind=kind, heading_path=[*parent.location.heading_path, block.title]),
            )
            parent.children.append(node)
            stack.append(node)
            parts[node.id] = []
        if block.page is not None and block.page != last_page:
            parts[stack[-1].id].append(page_marker(block.page))
            last_page = block.page
        if isinstance(block, Heading):
            continue
        if isinstance(block, Image):
            image_id = f"img-{len(images) + 1}"
            images.append((image_id, block))
            parts[stack[-1].id].append(image_placeholder(image_id))
        elif isinstance(block, Paragraph):
            parts[stack[-1].id].append(block.text)
        else:
            parts[stack[-1].id].append(block.markdown)

    for node in root.walk():
        node.text = "\n\n".join(parts[node.id])
    return root, images
