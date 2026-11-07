"""What one visitor sees: the shared sample documents plus their own session documents."""

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .indexing.pipeline import doc_id_for
from .models import DocumentIndex, FileKind
from .pdf_pages import render_page
from .sessions import SessionManager
from .storage import Store

SAMPLE_SCOPE = "sample"
_DOC_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,100}$")
_IMAGE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}\.(png|jpg|jpeg|gif|webp)$")
IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


@dataclass(frozen=True)
class DocInfo:
    doc_id: str
    title: str
    kind: FileKind
    summary: str
    sample: bool


class Library:
    def __init__(self, samples: Store | None, session: Store | None, uploads: Path | None = None):
        self.samples = samples
        self.session = session
        self.uploads = uploads  # the session's uploaded files: where a PDF's pages are rendered from

    @classmethod
    def for_session(cls, settings: Settings, sessions: SessionManager, token: str | None) -> "Library":
        live = sessions.get(token) if token else None
        if not live:
            return cls(Store(settings.samples_dir), None)
        return cls(Store(settings.samples_dir), sessions.store(token), sessions.uploads_dir(token))

    def documents(self) -> list[DocInfo]:
        docs = {}
        for store, sample in ((self.session, False), (self.samples, True)):  # samples win on a clash
            for entry in store.list_documents() if store else []:
                docs[entry.doc_id] = DocInfo(entry.doc_id, entry.title, entry.kind, entry.summary, sample)
        return sorted(docs.values(), key=lambda d: (not d.sample, d.title.lower()))

    def load(self, doc_id: str) -> DocumentIndex | None:
        for store in (self.samples, self.session):
            if store and store.has_index(doc_id):
                return store.load_index(doc_id)
        return None

    def indexes(self) -> list[DocumentIndex]:
        return [index for doc in self.documents() if (index := self.load(doc.doc_id))]

    def is_sample(self, doc_id: str) -> bool:
        return bool(self.samples and self.samples.has_index(doc_id))

    def image_file(self, doc_id: str, relative: str) -> Path | None:
        """Path of an image in this library, or None. Only plain image names inside the document folder."""
        if not _DOC_ID.match(doc_id) or not _IMAGE_NAME.match(Path(relative).name) or Path(relative).parent.name != "images":
            return None
        for store in (self.samples, self.session):
            if store and store.has_index(doc_id):
                base = store.doc_dir(doc_id).resolve()
                path = (base / "images" / Path(relative).name).resolve()
                return path if path.is_file() and path.is_relative_to(base) else None
        return None


    def page_file(self, doc_id: str, page: int) -> Path | None:
        """A picture of one page of an uploaded PDF, rendered the first time it is asked for; else None.

        Only for the session's own PDFs (the samples are Word files, and their folder is shared and
        read-only). The upload must still be the file the document was indexed from: a later upload
        with the same name but other content replaces the file, and must not show its pages here.
        """
        if not _DOC_ID.match(doc_id) or page < 0 or not self.session or not self.uploads:
            return None
        if self.is_sample(doc_id) or not self.session.has_index(doc_id):
            return None
        path = self.session.doc_dir(doc_id).resolve() / "pages" / f"{page}.png"
        if path.is_file():
            return path  # rendered before, after these same checks
        index = self.session.load_index(doc_id)
        if index.kind != "pdf" or not index.page_labels or page >= len(index.page_labels):
            return None
        source = (self.uploads / index.source_path).resolve()
        if not source.is_file() or not source.is_relative_to(self.uploads.resolve()):
            return None
        if doc_id_for(source.stem, source.read_bytes()) != doc_id:
            return None  # replaced by another file with the same name
        data = render_page(source, page)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")  # whole file or nothing
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(temporary, path)
        return path


def image_media_type(path: Path) -> str:
    return IMAGE_TYPES.get(path.suffix.lstrip(".").lower(), "application/octet-stream")
