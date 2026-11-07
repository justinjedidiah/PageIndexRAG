from toc_rag.indexing import index_document
from toc_rag.storage import Store

from .conftest import make_png


def test_index_markdown_without_llm(tmp_path, settings):
    (tmp_path / "crew.png").write_bytes(make_png())
    long_section = "\n\n".join(f"Fact {i}. " + "detail " * 30 for i in range(5))
    path = tmp_path / "Ship Manual.md"
    path.write_text(
        f"# Ship\n\nAbout the ship.\n\n## Crew\n\n![crew photo](crew.png)\n\n## Engines\n\n{long_section}\n",
        encoding="utf-8",
    )
    store = Store(settings.data_dir)

    index = index_document(path, store, settings, use_llm=False)

    assert index.doc_id.startswith("ship-manual-")
    assert [e.title for e in index.source_toc] == ["Ship", "Crew", "Engines"]
    engines = index.node_map()["1.2"]
    assert engines.title == "Engines"
    assert engines.children and all(c.origin == "generated" for c in engines.children)

    image = index.images[0]
    assert image.node_id == "1.1"
    assert image.description == "crew photo"
    assert (store.doc_dir(index.doc_id) / image.file).read_bytes() == make_png()

    assert store.load_index(index.doc_id) == index
    assert [e.doc_id for e in store.list_documents()] == [index.doc_id]


def test_reindexing_keeps_one_library_entry(tmp_path, settings):
    path = tmp_path / "a.md"
    path.write_text("# A\n\ntext\n", encoding="utf-8")
    store = Store(settings.data_dir)
    index_document(path, store, settings, use_llm=False)
    index_document(path, store, settings, use_llm=False)
    assert len(store.list_documents()) == 1


def test_free_preset_makes_no_llm_calls_and_fills_previews(tmp_path, settings, monkeypatch):
    from toc_rag.indexing import preset_options

    def no_llm(*args, **kwargs):
        raise AssertionError("the Free preset must not create a model")

    monkeypatch.setattr("toc_rag.llm.chat_model", no_llm)
    path = tmp_path / "notes.md"
    path.write_text("# Notes\n\nFirst point. Second point. Third point. Fourth point.\n", encoding="utf-8")

    index = index_document(path, Store(settings.data_dir), settings, options=preset_options("free", settings))

    assert index.options == preset_options("free", settings)
    assert index.summary and all(node.summary_source == "preview" for node in index.root.walk())


def test_options_are_recorded_and_no_llm_overrides_them(tmp_path, settings):
    path = tmp_path / "a.md"
    path.write_text("# A\n\ntext\n", encoding="utf-8")
    index = index_document(path, Store(settings.data_dir), settings, use_llm=False)
    assert index.options.summaries == "preview" and not index.options.describe_images
    assert index.options.max_leaf_tokens == settings.max_leaf_tokens


def test_committed_sample_indexes_still_load():
    from pathlib import Path

    from toc_rag.models import DocumentIndex

    paths = sorted(Path(__file__).resolve().parents[1].glob("samples/index/docs/*/index.json"))
    assert paths
    for path in paths:
        DocumentIndex.model_validate_json(path.read_text(encoding="utf-8"))


def test_doc_id_comes_from_the_name_and_content(tmp_path):
    from toc_rag.indexing.pipeline import doc_id_for, make_doc_id

    path = tmp_path / "Ship Manual.md"
    path.write_bytes(b"# Ship\n")
    assert make_doc_id(path) == doc_id_for("Ship Manual", b"# Ship\n")
    assert doc_id_for("Ship Manual", b"# Ship\n") != doc_id_for("Ship Manual", b"# Boat\n")
