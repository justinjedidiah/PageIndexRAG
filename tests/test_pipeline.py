from pageindex_rag.indexing import index_document
from pageindex_rag.storage import Store

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
