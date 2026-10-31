from pageindex_rag.indexing import index_document
from pageindex_rag.library import Library
from pageindex_rag.sessions import SessionManager
from pageindex_rag.storage import Store

from .conftest import make_png


def write_md(tmp_path, name: str, body: str):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_library_merges_samples_and_session_documents(tmp_path, settings):
    samples = Store(tmp_path / "samples")
    index_document(write_md(tmp_path, "zeta.md", "# Z\n\ntext\n"), samples, settings, use_llm=False)
    sessions = SessionManager(tmp_path / "sessions", ttl_seconds=100)
    sessions.ensure("token-12345")
    own = sessions.store("token-12345")
    mine = index_document(write_md(tmp_path, "alpha.md", "# A\n\ntext\n"), own, settings, use_llm=False)

    library = Library(samples, own)

    docs = library.documents()
    assert [(d.title, d.sample) for d in docs] == [("zeta", True), ("alpha", False)]  # samples first
    assert library.load(mine.doc_id).title == "alpha"
    assert library.is_sample(docs[0].doc_id) and not library.is_sample(mine.doc_id)
    assert {i.title for i in library.indexes()} == {"zeta", "alpha"}
    assert library.load("missing-doc") is None


def test_library_without_a_live_session_shows_only_samples(tmp_path, settings):
    samples = Store(tmp_path / "samples")
    index_document(write_md(tmp_path, "zeta.md", "# Z\n\ntext\n"), samples, settings, use_llm=False)
    sessions = SessionManager(tmp_path / "sessions", ttl_seconds=100)

    library = Library.for_session(settings.__class__(**{**settings.__dict__, "samples_dir": tmp_path / "samples"}), sessions, "token-12345")

    assert [d.title for d in library.documents()] == ["zeta"]
    assert Library.for_session(settings, sessions, None).session is None


def test_delete_document_removes_files_and_listing(tmp_path, settings):
    store = Store(tmp_path / "s")
    keep = index_document(write_md(tmp_path, "keep.md", "# K\n\ntext\n"), store, settings, use_llm=False)
    drop = index_document(write_md(tmp_path, "drop.md", "# D\n\ntext\n"), store, settings, use_llm=False)

    store.delete_document(drop.doc_id)

    assert [e.doc_id for e in store.list_documents()] == [keep.doc_id]
    assert not store.doc_dir(drop.doc_id).exists()
    assert store.has_index(keep.doc_id) and not store.has_index(drop.doc_id)


def test_image_file_only_serves_images_inside_the_document_folder(tmp_path, settings):
    (tmp_path / "pic.png").write_bytes(make_png())
    store = Store(tmp_path / "s")
    doc = index_document(write_md(tmp_path, "d.md", "# D\n\n![pic](pic.png)\n"), store, settings, use_llm=False)
    (store.doc_dir(doc.doc_id) / "secret.txt").write_text("secret")
    (store.doc_dir(doc.doc_id) / "images" / "notes.txt").write_text("not an image")
    library = Library(None, store)

    found = library.image_file(doc.doc_id, "images/img-1.png")
    assert found is not None and found.read_bytes() == make_png()

    for relative in ("../secret.txt", "images/../index.json", "secret.txt", "images/notes.txt", "images/missing.png", "images/../../x.png"):
        assert library.image_file(doc.doc_id, relative) is None
    for doc_id in ("../s", "..", "No-Such-Doc", doc.doc_id.upper(), "x" * 200):
        assert library.image_file(doc_id, "images/img-1.png") is None
