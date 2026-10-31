import threading

import anthropic
import httpx
from langchain_core.runnables import RunnableLambda

from pageindex_rag.errors import friendly_error
from pageindex_rag.indexing import build_index, enrich_index, estimate_index
from pageindex_rag.indexing.summarize import _ImageDescription, _NodeSummary
from pageindex_rag.storage import Store

from .conftest import make_noisy_png, make_png


class FakeLLM:
    """Stands in for ChatAnthropic: structured output answers come from the prompt."""

    def __init__(self):
        self.prompts: dict[str, str] = {}
        self.lock = threading.Lock()
        self.threads: set[int] = set()
        self.image_calls = 0

    def with_structured_output(self, schema):
        return RunnableLambda(lambda prompt: self._respond(schema, prompt))

    def _respond(self, schema, prompt):
        with self.lock:
            self.threads.add(threading.get_ident())
        if schema is _ImageDescription:
            with self.lock:
                self.image_calls += 1
            return _ImageDescription(description="A noisy square.", tags=["noise"])
        title = prompt.split("Section title: ", 1)[1].split("\n", 1)[0]
        with self.lock:
            self.prompts[title] = prompt
        return _NodeSummary(title=f"Better {title}", summary=f"About {title}.")


def make_doc(tmp_path, settings):
    (tmp_path / "big.png").write_bytes(make_noisy_png())
    (tmp_path / "tiny.png").write_bytes(make_png())
    body = "\n\n".join(f"Paragraph {i}. " + "word " * 30 for i in range(6))
    path = tmp_path / "doc.md"
    path.write_text(f"# Top\n\n## Child\n\n![big](big.png)\n\n![tiny](tiny.png)\n\n## Long\n\n{body}\n", encoding="utf-8")
    store = Store(tmp_path / "store")
    return store, build_index(path, store, settings)


def test_estimate_counts_sections_and_only_describable_images(tmp_path, settings):
    store, index = make_doc(tmp_path, settings)

    estimate = estimate_index(index, store)

    assert estimate.sections == sum(1 for _ in index.root.walk())
    assert estimate.images == 1  # the tiny image is skipped
    assert estimate.calls == estimate.sections + 1
    assert estimate.input_tokens > 0 and estimate.output_tokens > 0


def test_enrich_summarizes_bottom_up_and_reports_progress(tmp_path, settings, monkeypatch):
    store, index = make_doc(tmp_path, settings)
    llm = FakeLLM()
    keys = []
    monkeypatch.setattr("pageindex_rag.llm.chat_model", lambda model, api_key=None, **kw: keys.append(api_key) or llm)
    progress = []

    enrich_index(index, store, settings, api_key="visitor-key", on_progress=lambda *args: progress.append(args))

    assert set(keys) == {"visitor-key"}  # the visitor's key reaches every model
    assert all(node.summary for node in index.root.walk())
    assert index.summary == index.root.summary
    assert llm.image_calls == 1
    assert next(r for r in index.images if r.id == "img-1").description == "A noisy square."
    assert next(r for r in index.images if r.id == "img-2").description == "tiny"  # skipped: keeps alt text

    # A parent's prompt includes its children's summaries, so children were done first.
    assert "About Child." in llm.prompts["Top"]
    assert "About Top." in llm.prompts[index.root.title]

    # Generated nodes got real titles; original ones kept theirs.
    long_parts = next(n for n in index.root.walk() if n.title == "Long").children
    assert long_parts and all(part.title.startswith("Better ") for part in long_parts)
    assert next(n for n in index.root.walk() if n.id == "1.1").title == "Child"

    totals = {total for _, total, _ in progress}
    assert totals == {estimate_index(index, store).calls}
    assert [done for done, _, _ in progress] == list(range(1, len(progress) + 1))
    assert progress[-1][0] == progress[-1][1]


def test_enrich_propagates_llm_failures(tmp_path, settings, monkeypatch):
    store, index = make_doc(tmp_path, settings)

    class Broken(FakeLLM):
        def _respond(self, schema, prompt):
            raise RuntimeError("boom")

    monkeypatch.setattr("pageindex_rag.llm.chat_model", lambda model, api_key=None, **kw: Broken())
    try:
        enrich_index(index, store, settings)
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected the failure to surface")


def test_friendly_error_never_echoes_the_raw_message():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def error(cls, status: int):
        return cls("raw text with sk-ant-secret", response=httpx.Response(status, request=request), body=None)

    cases = [
        (error(anthropic.AuthenticationError, 401), "rejected that API key"),
        (error(anthropic.PermissionDeniedError, 403), "not allowed"),
        (error(anthropic.RateLimitError, 429), "rate limit"),
        (error(anthropic.InternalServerError, 500), "status 500"),
        (anthropic.APIConnectionError(request=request), "Could not reach"),
        (RuntimeError("sk-ant-secret"), "Something went wrong"),
    ]
    for exc, expected in cases:
        message = friendly_error(exc)
        assert expected in message and "sk-ant" not in message
