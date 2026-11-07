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
    # (The lone top heading "Top" was collapsed into the root, saving the agent a step.)
    assert "Top" not in llm.prompts
    assert "About Child." in llm.prompts[index.root.title]
    assert all(node.summary_source == "llm" for node in index.root.walk())

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


def test_estimate_follows_the_options(tmp_path, settings):
    from pageindex_rag.indexing import preset_options

    store, index = make_doc(tmp_path, settings)
    free, budget, full = (estimate_index(index, store, preset_options(n, settings)) for n in ("free", "budget", "full"))

    assert free.calls == 0 and free.input_tokens == 0 and free.output_tokens == 0
    assert budget.images == 0 and budget.summaries == budget.sections
    assert full.calls == full.summaries + 1
    assert budget.output_tokens < full.output_tokens


def test_budget_options_skip_images_and_ask_for_short_summaries(tmp_path, settings, monkeypatch):
    from pageindex_rag.indexing import preset_options
    from pageindex_rag.indexing.summarize import _ShortNodeSummary

    store, index = make_doc(tmp_path, settings)
    llm = FakeLLM()
    schemas = []
    original = llm.with_structured_output
    llm.with_structured_output = lambda schema: schemas.append(schema) or original(schema)
    caps = []
    monkeypatch.setattr("pageindex_rag.llm.chat_model", lambda model, api_key=None, **kw: caps.append(kw) or llm)

    enrich_index(index, store, settings, options=preset_options("budget", settings))

    assert llm.image_calls == 0
    assert schemas == [_ShortNodeSummary]
    assert caps == [{"max_tokens": 256, "provider": "anthropic"}]
    assert "short sentences" in next(iter(llm.prompts.values()))
    assert index.options.summary_length == "short"


def test_a_stop_before_indexing_makes_no_claude_calls(tmp_path, settings, monkeypatch):
    import threading

    from pageindex_rag.indexing import IndexingCancelled

    store, index = make_doc(tmp_path, settings)
    llm = FakeLLM()
    monkeypatch.setattr("pageindex_rag.llm.chat_model", lambda model, api_key=None, **kw: llm)
    cancel = threading.Event()
    cancel.set()
    try:
        enrich_index(index, store, settings, cancel=cancel)
    except IndexingCancelled:
        pass
    else:
        raise AssertionError("expected IndexingCancelled")
    assert llm.image_calls == 0 and llm.prompts == {}


def test_a_stop_during_indexing_starts_no_new_calls(tmp_path, settings, monkeypatch):
    import dataclasses
    import threading

    from pageindex_rag.indexing import IndexingCancelled

    one_worker = dataclasses.replace(settings, index_workers=1)  # one call at a time, so the count is exact
    store, index = make_doc(tmp_path, one_worker)
    llm = FakeLLM()
    monkeypatch.setattr("pageindex_rag.llm.chat_model", lambda model, api_key=None, **kw: llm)
    cancel = threading.Event()
    steps = []

    def on_progress(done, total, label):
        steps.append(label)
        if len(steps) == 2:
            cancel.set()  # e.g. the visitor pressed "End session" here

    try:
        enrich_index(index, store, one_worker, on_progress=on_progress, cancel=cancel)
    except IndexingCancelled:
        pass
    else:
        raise AssertionError("expected IndexingCancelled")
    total = estimate_index(index, store).calls
    assert len(steps) == 2 < total  # nothing ran after the stop
    assert llm.image_calls + len(llm.prompts) == 2


def test_enrich_records_the_provider_and_models_but_never_the_key(tmp_path, settings, monkeypatch):
    from pageindex_rag.indexing import preset_options
    from pageindex_rag.providers import LLMConfig

    store, index = make_doc(tmp_path, settings)
    monkeypatch.setattr("pageindex_rag.llm.chat_model", lambda model, api_key=None, **kw: FakeLLM())
    llm = LLMConfig("openai", "sk-secret-123", index_model="small-model", agent_model="big-model")

    enrich_index(index, store, settings, options=preset_options("budget", settings), llm=llm)

    assert (index.indexed_with.provider, index.indexed_with.index_model, index.indexed_with.vision_model) == (
        "openai", "small-model", "")  # Budget describes no images
    assert "sk-secret-123" not in index.model_dump_json()
