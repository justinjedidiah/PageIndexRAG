"""Command line: index files, list them, print a tree, ask a question."""

import argparse
from dataclasses import replace

from .config import Settings, get_settings
from .indexing.options import DEFAULT_PRESET, PRESET_NAMES, preset_options
from .models import IndexOptions, Node
from .storage import Store


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pageindex")
    commands = parser.add_subparsers(dest="command", required=True)

    index_cmd = commands.add_parser("index", help="index a .docx or .md file")
    index_cmd.add_argument("path")
    index_cmd.add_argument("--preset", choices=PRESET_NAMES, default=DEFAULT_PRESET, help="what to spend on the LLM")
    index_cmd.add_argument("--no-llm", action="store_true", help="same as --preset free: no LLM calls at all")
    index_cmd.add_argument("--no-split", action="store_true", help="never split sections that are too big")
    index_cmd.add_argument("--max-leaf", type=int, help="largest section, in tokens, before it is split")
    index_cmd.add_argument("--small-sections", choices=["off", "merge", "fold"], help="how to handle tiny sections")
    index_cmd.add_argument("--min-leaf", type=int, help="sections under this many tokens count as tiny")
    index_cmd.add_argument("--summaries", choices=["llm", "preview"], help="LLM summaries or free previews")
    index_cmd.add_argument("--summary-length", choices=["short", "detailed"])
    index_cmd.add_argument("--no-images", action="store_true", help="skip image descriptions")

    commands.add_parser("list", help="list indexed documents")

    tree_cmd = commands.add_parser("tree", help="print a document's node tree")
    tree_cmd.add_argument("doc_id")

    ask_cmd = commands.add_parser("ask", help="ask a question about the library (or one document)")
    ask_cmd.add_argument("question")
    ask_cmd.add_argument("--doc", help="limit to one document id")
    ask_cmd.add_argument("--listing-depth", type=int, choices=[1, 2, 3], help="tree levels one listing shows")

    args = parser.parse_args(argv)
    settings = get_settings()
    store = Store(settings.data_dir)

    if args.command == "index":
        from .indexing import index_document

        options = _index_options(args, settings)
        if options.uses_llm and not settings.index_model:
            parser.error("set PAGEINDEX_INDEX_MODEL in .env to a model of your provider, or use --preset free")
        index = index_document(args.path, store, settings, options=options)
        options = index.options
        print(f"Indexed {index.doc_id}: {sum(1 for _ in index.root.walk())} nodes, {len(index.images)} images")
        print(
            f"  split={'on' if options.split_large else 'off'} (max {options.max_leaf_tokens} tokens), "
            f"small sections={options.small_sections} (min {options.min_leaf_tokens}), "
            f"summaries={options.summaries}"
            + (f" ({options.summary_length})" if options.summaries == "llm" else "")
            + f", images={'described' if options.describe_images else 'not described'}"
        )
    elif args.command == "list":
        for entry in store.list_documents():
            print(f"{entry.doc_id}  ({entry.kind})  {entry.title}")
    elif args.command == "tree":
        index = store.load_index(args.doc_id)
        print(f"structure: {STRUCTURE_NOTES.get(index.structure_source, 'not recorded')}")
        _print_tree(index.root)
    elif args.command == "ask":
        from .agent import AnswerResult, Step, stream_ask

        doc_ids = [args.doc] if args.doc else [entry.doc_id for entry in store.list_documents()]
        if not doc_ids:
            parser.error("no documents indexed yet")
        if not settings.agent_model:
            parser.error("set PAGEINDEX_AGENT_MODEL in .env to a model of your provider (one that supports tool calling)")
        if args.listing_depth:
            settings = replace(settings, agent_listing_depth=args.listing_depth)
        for event in stream_ask([store.load_index(doc_id) for doc_id in doc_ids], args.question, settings):
            if isinstance(event, Step):
                print(f"  > {event.label}")
            if not isinstance(event, AnswerResult):
                continue
            print(event.text)
            for citation in event.citations:
                print(f"  [{citation.doc_id}:{citation.node_id}] {citation.title} - {citation.location}")
            for image in event.images:
                print(f"  image: {image.doc_id}:{image.image_id} ({image.description})")


STRUCTURE_NOTES = {
    "headings": "from the document's headings",
    "typed_toc": "from the document's typed contents page (it has no headings)",
    "formatting": "from lines formatted like headings (bold, bigger or numbered; it has no heading styles)",
    "none": "no headings found, so the text is split into parts by size",
}


def _index_options(args: argparse.Namespace, settings: Settings) -> IndexOptions:
    """The chosen preset, with any single option given on the command line applied on top."""
    overrides = {
        "split_large": False if args.no_split else None,
        "max_leaf_tokens": args.max_leaf,
        "small_sections": args.small_sections,
        "min_leaf_tokens": args.min_leaf,
        "summaries": args.summaries,
        "summary_length": args.summary_length,
        "describe_images": False if args.no_images else None,
    }
    preset = "free" if args.no_llm else args.preset
    options = preset_options(preset, settings, **{k: v for k, v in overrides.items() if v is not None})
    if args.no_llm:
        options = options.model_copy(update={"summaries": "preview", "describe_images": False})
    return options


def _print_tree(node: Node, depth: int = 0) -> None:
    marker = " *" if node.origin == "generated" else ""  # * = made by indexing, not an original heading
    covers = f"  covers: {', '.join(node.location.covers)}" if node.location.covers else ""
    print(f"{'  ' * depth}[{node.id}] {node.title}{marker}  ({len(node.text)} chars){covers}")
    if node.summary_source == "preview" and node.summary:
        print(f"{'  ' * depth}    preview: {node.summary[:100]}")
    for child in node.children:
        _print_tree(child, depth + 1)


if __name__ == "__main__":
    main()
