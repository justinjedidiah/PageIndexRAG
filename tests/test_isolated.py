"""Reading a document in a child process with a time limit. These start real processes (a second or two
each on Windows, where every child is a fresh interpreter)."""

import time
from dataclasses import replace

import pytest

from toc_rag.indexing import DocumentRejected
from toc_rag.indexing.isolated import ReadTimedOut, build_index_isolated, run_isolated
from toc_rag.indexing.pipeline import build_index
from toc_rag.storage import Store

from .conftest import make_png


# Top-level, so the child process can import them.
def _sleep(seconds: float) -> str:
    time.sleep(seconds)
    return "done"


def _refuse() -> None:
    raise DocumentRejected("This PDF is password-protected.")


def test_a_read_gives_the_same_index_and_images_as_in_process(tmp_path, settings):
    (tmp_path / "crew.png").write_bytes(make_png())
    path = tmp_path / "Ship Manual.md"
    path.write_text("# Ship\n\nAbout.\n\n## Crew\n\n![crew photo](crew.png)\n\nMore.\n", encoding="utf-8")
    store = Store(settings.data_dir)

    isolated = build_index_isolated(path, store, settings)

    assert isolated == build_index(path, store, settings)
    assert (store.doc_dir(isolated.doc_id) / isolated.images[0].file).read_bytes() == make_png()


def test_a_slow_read_is_stopped_with_a_message_for_the_visitor():
    started = time.monotonic()
    with pytest.raises(ReadTimedOut, match="took too long to read"):
        run_isolated(_sleep, (60,), timeout=3)
    assert time.monotonic() - started < 20  # stopped, not waited out
    assert issubclass(ReadTimedOut, DocumentRejected)  # so the app shows it like any other refusal


def test_errors_in_the_child_come_back_as_themselves():
    with pytest.raises(DocumentRejected, match="password-protected"):
        run_isolated(_refuse, (), timeout=30)
    assert run_isolated(_sleep, (0,), timeout=30) == "done"


def test_the_timeout_setting_is_used(tmp_path, settings, monkeypatch):
    path = tmp_path / "a.md"
    path.write_text("# A\n\ntext\n", encoding="utf-8")
    seen = {}

    def fake_run(fn, args, timeout):
        seen["timeout"] = timeout
        return "index"

    monkeypatch.setattr("toc_rag.indexing.isolated.run_isolated", fake_run)
    assert build_index_isolated(path, Store(tmp_path), replace(settings, read_timeout_seconds=7)) == "index"
    assert seen["timeout"] == 7
