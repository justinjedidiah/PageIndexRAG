from types import SimpleNamespace

import anthropic
import httpx
import openai
import pytest
from google.genai import errors as google_errors
from langchain_core.messages import AIMessage

from pageindex_rag import providers
from pageindex_rag.agent.graph import _refused
from pageindex_rag.errors import friendly_error
from pageindex_rag.providers import PROVIDERS, LLMConfig, NoModelChosen, chat_model, list_models

SECRET = "sk-secret-do-not-show-123"


def test_there_are_no_default_models():
    for provider in PROVIDERS:
        config = LLMConfig(provider, "k")
        assert (config.index_model, config.vision_model, config.agent_model) == ("", "", "")
    chosen = LLMConfig("openai", "k", index_model="small", agent_model="big")
    assert (chosen.index_model, chosen.vision_model, chosen.agent_model) == ("small", "small", "big")
    with pytest.raises(ValueError):
        LLMConfig("mistral", "k")


def test_the_key_never_shows_in_a_printed_config():
    assert SECRET not in repr(LLMConfig("openai", SECRET))


def test_output_caps_stay_small_only_where_they_are_safe():
    assert LLMConfig("anthropic").output_cap(256) == 256
    # OpenAI and Gemini models can spend output tokens on hidden reasoning first; a tight cap could leave no answer.
    assert LLMConfig("openai").output_cap(256) == providers.LARGE_OUTPUT_CAP
    assert LLMConfig("google").output_cap(256) == providers.LARGE_OUTPUT_CAP


def test_chat_model_builds_each_providers_model(monkeypatch):
    monkeypatch.delenv("PAGEINDEX_LLM_FACTORY", raising=False)
    built = {p: chat_model(LLMConfig(p, "test-key"), "some-model", 100) for p in PROVIDERS}
    assert type(built["anthropic"]).__name__ == "ChatAnthropic"
    assert type(built["openai"]).__name__ == "ChatOpenAI"
    assert type(built["google"]).__name__ == "ChatGoogleGenerativeAI"


def test_list_models_keeps_only_chat_models(monkeypatch):
    monkeypatch.delenv("PAGEINDEX_LLM_FACTORY", raising=False)
    openai_ids = ["gpt-6-luna", "gpt-6.1-sol", "gpt-6-audio", "gpt-image-2", "text-embedding-3", "whisper-1", "o5-mini"]
    monkeypatch.setattr(openai, "OpenAI", lambda api_key: SimpleNamespace(
        models=SimpleNamespace(list=lambda: [SimpleNamespace(id=i) for i in openai_ids])))
    assert list_models("openai", "k") == ["gpt-6-luna", "gpt-6.1-sol", "o5-mini"]

    from google import genai

    google_models = [
        SimpleNamespace(name="models/gemini-3.6-flash", supported_actions=["generateContent"]),
        SimpleNamespace(name="models/gemini-3.1-flash-image", supported_actions=["generateContent"]),
        SimpleNamespace(name="models/text-embedding-005", supported_actions=["embedContent"]),
        SimpleNamespace(name="models/gemini-3.5-flash-lite", supported_actions=["generateContent", "countTokens"]),
    ]
    monkeypatch.setattr(genai, "Client", lambda api_key: SimpleNamespace(models=SimpleNamespace(list=lambda: google_models)))
    assert list_models("google", "k") == ["gemini-3.5-flash-lite", "gemini-3.6-flash"]

    monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: SimpleNamespace(
        models=SimpleNamespace(list=lambda limit: [SimpleNamespace(id="claude-sonnet-5-5"), SimpleNamespace(id="claude-haiku-5-5")])))
    assert list_models("anthropic", "k") == ["claude-haiku-5-5", "claude-sonnet-5-5"]


def test_a_model_must_be_chosen_before_use(monkeypatch):
    monkeypatch.delenv("PAGEINDEX_LLM_FACTORY", raising=False)
    with pytest.raises(NoModelChosen):
        chat_model(LLMConfig("openai", "k"), "")


def _http(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "https://api.example.com"))


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (openai.AuthenticationError(SECRET, response=_http(401), body=None), "OpenAI rejected that API key"),
        (openai.RateLimitError(SECRET, response=_http(429), body=None), "OpenAI says your key hit a rate limit"),
        (openai.NotFoundError(SECRET, response=_http(404), body=None), "model is not available"),
        (openai.APIConnectionError(message=SECRET, request=httpx.Request("POST", "https://x")), "Could not reach OpenAI"),
        (google_errors.ClientError(401, {"error": {"message": SECRET}}), "Google rejected that API key"),
        (google_errors.ClientError(429, {"error": {"message": SECRET}}), "Google says your key hit a rate limit"),
        (google_errors.ServerError(503, {"error": {"message": SECRET}}), "Google returned an error (status 503)"),
        (anthropic.AuthenticationError(SECRET, response=_http(401), body=None), "Anthropic rejected that API key"),
    ],
)
def test_friendly_errors_for_every_provider_never_echo_the_raw_text(exc, expected):
    message = friendly_error(exc)
    assert expected in message
    assert SECRET not in message


def test_friendly_error_sees_through_a_wrapped_error():
    from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError

    try:
        try:
            raise google_errors.ClientError(403, {"error": {"message": SECRET}})
        except google_errors.ClientError as inner:
            raise ChatGoogleGenerativeAIError(SECRET) from inner
    except ChatGoogleGenerativeAIError as outer:
        message = friendly_error(outer)
    assert "not allowed" in message and SECRET not in message


def test_refusals_are_recognised_for_every_provider():
    assert _refused(AIMessage("", response_metadata={"stop_reason": "refusal"}))  # Anthropic
    assert _refused(AIMessage("", response_metadata={"finish_reason": "content_filter"}))  # OpenAI
    assert _refused(AIMessage("", additional_kwargs={"refusal": "I can't help with that."}))  # OpenAI
    assert _refused(AIMessage("", response_metadata={"finish_reason": "SAFETY"}))  # Gemini
    assert not _refused(AIMessage("fine", response_metadata={"finish_reason": "stop"}))
    assert not _refused(AIMessage("fine", response_metadata={"stop_reason": "end_turn"}))


def test_settings_take_models_only_from_the_environment(monkeypatch):
    from pageindex_rag.config import get_settings

    monkeypatch.setenv("PAGEINDEX_PROVIDER", "openai")
    for name in ("PAGEINDEX_INDEX_MODEL", "PAGEINDEX_VISION_MODEL", "PAGEINDEX_AGENT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    settings = get_settings()
    assert (settings.provider, settings.index_model, settings.agent_model) == ("openai", "", "")
    monkeypatch.setenv("PAGEINDEX_INDEX_MODEL", "small")
    monkeypatch.setenv("PAGEINDEX_AGENT_MODEL", "big")
    settings = get_settings()
    assert (settings.index_model, settings.vision_model, settings.agent_model) == ("small", "small", "big")
    monkeypatch.setenv("PAGEINDEX_PROVIDER", "mistral")
    with pytest.raises(ValueError):
        get_settings()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("'tools' is not supported with this model.", "doesn't support tool calling"),
        ("Function calling is not enabled for models/gemini-x", "doesn't support tool calling"),
        ("Invalid parameter: 'response_format' of type 'json_schema' is not supported with this model.",
         "doesn't support structured output"),
        ("Invalid content type. image_url is only supported by certain models.", "can't read images"),
        ("This model's maximum context length is 8192 tokens.", "too long for this model"),
        ("Invalid value for 'temperature'.", "couldn't process the request with this model (status 400)"),
    ],
)
def test_bad_requests_say_why_when_the_provider_does(raw, expected):
    for exc in (
        openai.BadRequestError(f"{raw} {SECRET}", response=_http(400), body=None),
        google_errors.ClientError(400, {"error": {"message": f"{raw} {SECRET}"}}),
    ):
        message = friendly_error(exc)
        assert expected in message
        assert SECRET not in message and raw not in message  # matched on the server, never shown


def test_a_context_overflow_from_langchain_is_recognised():
    from langchain_google_genai.chat_models import GoogleContextOverflowError

    assert "too long for this model" in friendly_error(GoogleContextOverflowError(400, {"error": {"message": SECRET}}))
