"""JSON file storage: one folder per document plus a library.json listing."""

import shutil
from pathlib import Path

from pydantic import TypeAdapter

from .models import DocumentIndex, LibraryEntry

_library_adapter = TypeAdapter(list[LibraryEntry])


class Store:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)

    @property
    def library_path(self) -> Path:
        return self.data_dir / "library.json"

    def doc_dir(self, doc_id: str) -> Path:
        return self.data_dir / "docs" / doc_id

    def save_image(self, doc_id: str, image_id: str, data: bytes, ext: str) -> str:
        """Write an image file and return its path relative to the document folder."""
        relative = f"images/{image_id}.{ext}"
        path = self.doc_dir(doc_id) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return relative

    def save_index(self, index: DocumentIndex) -> None:
        path = self.doc_dir(index.doc_id) / "index.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(index.model_dump_json(indent=2), encoding="utf-8")

        entries = {entry.doc_id: entry for entry in self.list_documents()}
        entries[index.doc_id] = LibraryEntry(
            doc_id=index.doc_id, title=index.title, kind=index.kind, summary=index.summary
        )
        self.library_path.write_bytes(_library_adapter.dump_json(list(entries.values()), indent=2))

    def delete_document(self, doc_id: str) -> None:
        """Remove a document's folder (index and images) and its library entry."""
        shutil.rmtree(self.doc_dir(doc_id), ignore_errors=True)
        entries = [entry for entry in self.list_documents() if entry.doc_id != doc_id]
        if self.library_path.exists() or entries:
            self.library_path.write_bytes(_library_adapter.dump_json(entries, indent=2))

    def has_index(self, doc_id: str) -> bool:
        return (self.doc_dir(doc_id) / "index.json").is_file()

    def load_index(self, doc_id: str) -> DocumentIndex:
        path = self.doc_dir(doc_id) / "index.json"
        return DocumentIndex.model_validate_json(path.read_text(encoding="utf-8"))

    def list_documents(self) -> list[LibraryEntry]:
        if not self.library_path.exists():
            return []
        return _library_adapter.validate_json(self.library_path.read_bytes())
