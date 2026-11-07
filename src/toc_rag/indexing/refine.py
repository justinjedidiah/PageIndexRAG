"""Careful changes to the document's own tree, each made only when a section breaks a limit.

Every tree level costs the agent one tool call, so changes aim for fewer levels and fewer tiny
stops, never more depth. A tree that breaks no limit comes back unchanged. Original headings are
never dropped: merged and folded sections keep their titles in the text and in `location.covers`.

- merge: a run of tiny sibling leaves becomes one section ("A · B · C"), and a section whose only
  content is one subsection absorbs it ("A › B").
- fold: merge, plus a whole subtree small enough to read in one go becomes a single section.
- split: a section too big to read in one go is cut into generated parts (see split.py).
"""

from ..models import IndexOptions, Node
from .split import estimate_tokens, split_large_nodes

MERGE_SEPARATOR = " · "
CHAIN_SEPARATOR = " › "
MAX_TITLE_CHARS = 120


def refine_tree(root: Node, options: IndexOptions) -> None:
    """Apply the chosen changes in place. Small sections first, so a merged section is never re-split."""
    if options.small_sections == "fold":
        for child in root.children:
            _fold(child, options.max_leaf_tokens)
    if options.small_sections != "off":
        _collapse_chains(root, options.min_leaf_tokens)
        _merge_tiny_leaves(root, options.min_leaf_tokens, options.max_leaf_tokens)
        _collapse_chains(root, options.min_leaf_tokens)  # a merge can leave a section with one child
    if options.split_large:
        split_large_nodes(root, options.max_leaf_tokens)


def _fold(node: Node, max_tokens: int) -> None:
    """Fold the largest subtrees that fit in one read, top-down. The root itself is never folded."""
    if not node.children:
        return
    descendants = list(node.walk())[1:]
    if estimate_tokens(node.text) + sum(_section_tokens(d) for d in descendants) <= max_tokens:
        node.text = _join([node.text, *(_section_text(d) for d in descendants)])
        node.location.covers = [*node.location.covers, *(d.title for d in descendants)]
        node.children = []
        return
    for child in node.children:
        _fold(child, max_tokens)


def _collapse_chains(node: Node, min_tokens: int) -> None:
    """A section with exactly one subsection and almost no text of its own absorbs that subsection.

    The root keeps its id and title (the document's), and only absorbs a child that has subsections
    of its own, so a document always keeps an outline. Other sections get the title "A › B". When the
    child is itself a merge of small sections, the section keeps its own title and takes over the
    merged titles in `covers`, as if it had been folded.
    """
    for child in node.children:
        _collapse_chains(child, min_tokens)
    while len(node.children) == 1 and estimate_tokens(node.text) < min_tokens:
        child = node.children[0]
        if node.id == "root" and not child.children:
            break
        node.text = _join([node.text, child.text])
        node.children = child.children
        if node.id == "root":
            continue
        if child.location.covers and child.origin == "generated":  # a merge of node's own subsections
            node.location.covers = [*node.location.covers, *child.location.covers]
        else:
            node.title = _shorten(f"{node.title}{CHAIN_SEPARATOR}{child.title}")
            node.location = child.location.model_copy(deep=True)


def _merge_tiny_leaves(node: Node, min_tokens: int, max_tokens: int) -> None:
    """Join runs of two or more adjacent tiny leaves, as long as the joined section still fits in one read."""
    for child in node.children:
        _merge_tiny_leaves(child, min_tokens, max_tokens)

    merged: list[Node] = []
    run: list[Node] = []
    run_tokens = 0

    def flush() -> None:
        nonlocal run_tokens
        if run:
            merged.append(_merged_node(run) if len(run) >= 2 else run[0])
        run.clear()
        run_tokens = 0

    for child in node.children:
        tokens = _section_tokens(child)
        if child.children or estimate_tokens(child.text) >= min_tokens:
            flush()
            merged.append(child)
            continue
        if run and run_tokens + tokens > max_tokens:
            flush()
        run.append(child)
        run_tokens += tokens
    flush()
    node.children = merged


def _merged_node(run: list[Node]) -> Node:
    first = run[0]
    location = first.location.model_copy(deep=True)
    location.heading_path = first.location.heading_path[:-1]  # the parent's path; the titles go in covers
    location.covers = [title for node in run for title in (node.location.covers or [node.title])]
    return Node(
        id=first.id,
        title=_shorten(MERGE_SEPARATOR.join(node.title for node in run)),
        level=first.level,
        origin="generated",
        text=_join([_section_text(node) for node in run]),
        location=location,
    )


def _section_text(node: Node) -> str:
    """A section as it reads once inlined: its title on its own line, then its text."""
    return f"{node.title}\n{node.text}" if node.text else node.title


def _section_tokens(node: Node) -> int:
    return estimate_tokens(_section_text(node))


def _join(parts: list[str]) -> str:
    return "\n\n".join(part for part in parts if part)


def _shorten(title: str) -> str:
    return title if len(title) <= MAX_TITLE_CHARS else title[: MAX_TITLE_CHARS - 1].rstrip() + "…"
