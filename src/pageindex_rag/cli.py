"""Command line: index files, list them, print a tree, ask a question."""

import argparse

from .config import get_settings
from .models import Node
from .storage import Store


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pageindex")
    commands = parser.add_subparsers(dest="command", required=True)

    index_cmd = commands.add_parser("index", help="index a .docx or .md file")
    index_cmd.add_argument("path")
    index_cmd.add_argument("--no-llm", action="store_true", help="skip summaries and image descriptions")

    commands.add_parser("list", help="list indexed documents")

    tree_cmd = commands.add_parser("tree", help="print a document's node tree")
    tree_cmd.add_argument("doc_id")

    ask_cmd = commands.add_parser("ask", help="ask a question about the library (or one document)")
    ask_cmd.add_argument("question")
    ask_cmd.add_argument("--doc", help="limit to one document id")

    args = parser.parse_args(argv)
    settings = get_settings()
    store = Store(settings.data_dir)

    if args.command == "index":
        from .indexing import index_document

        index = index_document(args.path, store, settings, use_llm=not args.no_llm)
        print(f"Indexed {index.doc_id}: {sum(1 for _ in index.root.walk())} nodes, {len(index.images)} images")
    elif args.command == "list":
        for entry in store.list_documents():
            print(f"{entry.doc_id}  ({entry.kind})  {entry.title}")
    elif args.command == "tree":
        _print_tree(store.load_index(args.doc_id).root)
    elif args.command == "ask":
        from .agent import AnswerResult, Step, stream_ask

        doc_ids = [args.doc] if args.doc else [entry.doc_id for entry in store.list_documents()]
        if not doc_ids:
            parser.error("no documents indexed yet")
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


def _print_tree(node: Node, depth: int = 0) -> None:
    marker = " *" if node.origin == "generated" else ""
    print(f"{'  ' * depth}[{node.id}] {node.title}{marker}  ({len(node.text)} chars)")
    for child in node.children:
        _print_tree(child, depth + 1)


if __name__ == "__main__":
    main()
