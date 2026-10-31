"""Tree navigation, with a record of what the agent has visited."""

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel

from ..models import DocumentIndex, ImageRecord, Node

AT_TOP = "Already at the top of the document."
NO_DOCUMENT = "No document is open. Use open_document first (list_documents shows the ids)."


class Step(BaseModel):
    """One navigation action, in a form the UI can show as part of the path taken."""

    tool: Literal["list_documents", "open_document", "list_children", "read_node", "go_up"]
    doc_id: str | None = None
    node_id: str | None = None
    label: str
    note: str = ""  # what the model said just before this action, when it said anything


class Navigator:
    """Navigation inside one document. `prefix` qualifies the ids shown for citation."""

    def __init__(self, index: DocumentIndex, prefix: str = ""):
        self.index = index
        self.prefix = prefix
        self.nodes = index.node_map()
        self.parents = index.parent_map()
        self.images_by_node: dict[str, list[ImageRecord]] = defaultdict(list)
        for record in index.images:
            if record.node_id:
                self.images_by_node[record.node_id].append(record)
        self.visited: set[str] = set()  # nodes the agent has looked at in any way
        self.read: set[str] = set()  # nodes whose text it has actually read

    def list_children(self, node_id: str) -> str:
        node = self.nodes.get(node_id)
        if node is None:
            return f"Unknown node id {node_id!r}."
        self.visited.add(node_id)
        if not node.children:
            return f"[{node.id}] {node.title} has no subsections. Use read_node to read it."

        lines = [f"Subsections of [{node.id}] {node.title}:"]
        for child in node.children:
            shape = "leaf" if not child.children else f"{len(child.children)} subsections"
            visited = ", visited" if child.id in self.visited else ""
            lines.append(f"- [{child.id}] {child.title} ({shape}{visited}): {child.summary or 'no summary'}")
        return "\n".join(lines)

    def read_node(self, node_id: str) -> str:
        node = self.nodes.get(node_id)
        if node is None:
            return f"Unknown node id {node_id!r}."
        self.visited.add(node_id)
        self.read.add(node_id)
        text = node.text or "(No text of its own. Its content is in its subsections.)"
        out = f"[{self.prefix}{node.id}] {node.title} ({node.location.label()})\n\n{text}"
        images = self.images_by_node.get(node_id)
        if images:
            listed = "\n".join(f"- {self.prefix}{r.id}: {r.description or 'no description'}" for r in images)
            out += f"\n\nImages in this section:\n{listed}"
        return out

    def go_up(self, node_id: str) -> str:
        parent_id = self.parents.get(node_id)
        if parent_id is None:
            return AT_TOP
        return self.list_children(parent_id)


class LibraryNavigator:
    """Navigation over several documents. Section tools act on the document that is open."""

    def __init__(self, indexes: list[DocumentIndex]):
        if not indexes:
            raise ValueError("A library needs at least one document.")
        self.indexes = {index.doc_id: index for index in indexes}
        self.docs = {index.doc_id: Navigator(index, prefix=f"{index.doc_id}:") for index in indexes}
        self.current: str | None = indexes[0].doc_id if len(indexes) == 1 else None
        self.trace: list[Step] = []

    def documents_listing(self) -> str:
        lines = []
        for index in self.indexes.values():
            marker = ", open" if index.doc_id == self.current else ""
            lines.append(f"- [{index.doc_id}] {index.title} ({index.kind}{marker}): {index.summary or 'no summary'}")
        return "\n".join(lines)

    def list_documents(self) -> str:
        self.trace.append(Step(tool="list_documents", label="Listed documents"))
        return self.documents_listing()

    def open_document(self, doc_id: str) -> str:
        index = self.indexes.get(doc_id)
        if index is None:
            return f"Unknown document id {doc_id!r}. Use list_documents to see the ids."
        self.current = doc_id
        self.trace.append(Step(tool="open_document", doc_id=doc_id, label=f'Opened "{index.title}"'))
        return f'Opened "{index.title}".\n\n' + self.docs[doc_id].list_children("root")

    def list_children(self, node_id: str) -> str:
        nav = self._open()
        if nav is None:
            return NO_DOCUMENT
        node_id = self._local(nav, node_id)
        result = nav.list_children(node_id)
        self._record("list_children", nav, node_id, "Looked inside")
        return result

    def read_node(self, node_id: str) -> str:
        nav = self._open()
        if nav is None:
            return NO_DOCUMENT
        node_id = self._local(nav, node_id)
        result = nav.read_node(node_id)
        self._record("read_node", nav, node_id, "Read")
        return result

    def go_up(self, node_id: str) -> str:
        nav = self._open()
        if nav is None:
            return NO_DOCUMENT
        node_id = self._local(nav, node_id)
        result = nav.go_up(node_id)
        if result == AT_TOP:
            return f"{AT_TOP} Use list_documents and open_document to switch documents."
        self._record("go_up", nav, node_id, "Went back up from")
        return result

    def find_node(self, ref: str) -> tuple[DocumentIndex, Node] | None:
        """Resolve a qualified `doc_id:node_id` (or a bare node id in the open document)."""
        doc_id, local = self._split(ref)
        nav = self.docs.get(doc_id) if doc_id else None
        node = nav.nodes.get(local) if nav else None
        return (nav.index, node) if nav and node else None

    def find_image(self, ref: str) -> tuple[DocumentIndex, ImageRecord] | None:
        doc_id, local = self._split(ref)
        index = self.indexes.get(doc_id) if doc_id else None
        record = next((r for r in index.images if r.id == local), None) if index else None
        return (index, record) if index and record else None

    def has_read(self, doc_id: str, node_id: str) -> bool:
        nav = self.docs.get(doc_id)
        return nav is not None and node_id in nav.read

    def _open(self) -> Navigator | None:
        return self.docs.get(self.current) if self.current else None

    def _split(self, ref: str) -> tuple[str | None, str]:
        doc_id, sep, local = ref.partition(":")
        return (doc_id, local) if sep else (self.current, ref)

    def _local(self, nav: Navigator, node_id: str) -> str:
        return node_id.removeprefix(nav.prefix)

    def _record(self, tool: str, nav: Navigator, node_id: str, verb: str) -> None:
        node = nav.nodes.get(node_id)
        if node is not None:
            self.trace.append(
                Step(tool=tool, doc_id=nav.index.doc_id, node_id=node_id, label=f'{verb} "{node.title}"')
            )
