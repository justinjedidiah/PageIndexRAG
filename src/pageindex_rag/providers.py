"""The model providers a visitor can bring a key for: Anthropic, OpenAI and Google Gemini.

Everything provider-specific lives here: how to build a chat model and how to list the models a
key can use. The rest of the code works through LangChain's common interface (tool calls,
structured output, image input) and an `LLMConfig`.

Model lists change every few months, so the app names no models at all: after a key is checked,
`list_models` asks the provider which models that key can use, and the visitor chooses from that
list. The dev CLI takes its models from the environment.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field, replace
from typing import Literal, get_args

from langchain_core.language_models import BaseChatModel

Provider = Literal["anthropic", "openai", "google"]
PROVIDER_IDS: tuple[Provider, ...] = get_args(Provider)

# Words in a model id that mark it as something other than a chat model (speech, images, embeddings...).
_NOT_CHAT = (
    "audio", "realtime", "tts", "transcribe", "whisper", "image", "dall-e", "embedding", "embed",
    "moderation", "search", "computer-use", "live", "aqa", "veo", "imagen", "lyria", "robotics",
)


@dataclass(frozen=True)
class ProviderInfo:
    id: Provider
    label: str  # "OpenAI"
    key_placeholder: str
    key_url: str  # where a visitor gets a key
    key_env: str  # the environment variable LangChain reads when no key is passed (dev and CLI only)
    # Tight output caps are only used where the model does not spend output tokens on hidden reasoning
    # before answering; elsewhere a small cap could leave no room for the answer itself.
    small_caps: bool = False


PROVIDERS: dict[Provider, ProviderInfo] = {
    "anthropic": ProviderInfo(
        "anthropic", "Anthropic", "sk-ant-...", "https://console.anthropic.com/settings/keys", "ANTHROPIC_API_KEY",
        small_caps=True,
    ),
    "openai": ProviderInfo(
        "openai", "OpenAI", "sk-...", "https://platform.openai.com/api-keys", "OPENAI_API_KEY",
    ),
    "google": ProviderInfo(
        "google", "Google Gemini", "AIza...", "https://aistudio.google.com/apikey", "GOOGLE_API_KEY",
    ),
}
LARGE_OUTPUT_CAP = 4096  # output cap where small_caps is False


class NoModelChosen(ValueError):
    """A model call was asked for before a model was chosen."""


@dataclass(frozen=True)
class LLMConfig:
    """Which provider, key and models one visitor (or the CLI) uses. The key is never written anywhere.

    `api_key=None` means LangChain reads the provider's environment variable: for local dev and the CLI only.
    """

    provider: Provider = "anthropic"
    api_key: str | None = field(default=None, repr=False)  # repr=False: a printed config never shows the key
    index_model: str = ""  # summaries and image descriptions; empty until chosen
    vision_model: str = ""  # empty means the index model
    agent_model: str = ""  # the chat agent; empty until chosen

    def __post_init__(self) -> None:
        if self.provider not in PROVIDERS:
            raise ValueError(f"Unknown provider {self.provider!r}. Choose one of: {', '.join(PROVIDER_IDS)}.")
        object.__setattr__(self, "vision_model", self.vision_model or self.index_model)

    @property
    def info(self) -> ProviderInfo:
        return PROVIDERS[self.provider]

    def output_cap(self, small: int) -> int:
        """`small` where the provider allows a tight cap, else a generous one (see ProviderInfo.small_caps)."""
        return small if self.info.small_caps else LARGE_OUTPUT_CAP

    def with_key(self, api_key: str | None) -> LLMConfig:
        return replace(self, api_key=api_key)


def chat_model(config: LLMConfig, model: str, max_tokens: int = 2048) -> BaseChatModel:
    """A LangChain chat model for this provider. `PAGEINDEX_LLM_FACTORY` swaps in an offline model for tests."""
    if not model:
        raise NoModelChosen("No model chosen. Pick one in the app, or set PAGEINDEX_INDEX_MODEL / PAGEINDEX_AGENT_MODEL.")
    factory = os.getenv("PAGEINDEX_LLM_FACTORY")  # "package.module:function"
    if factory:
        module_name, _, function = factory.partition(":")
        return getattr(importlib.import_module(module_name), function)(model)
    key = {"api_key": config.api_key} if config.api_key else {}
    if config.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, max_tokens=max_tokens, **key)
    if config.provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, max_tokens=max_tokens, **key)
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=model, max_output_tokens=max_tokens, **key)


def list_models(provider: Provider, api_key: str) -> list[str]:
    """The chat models this key can use, sorted. Raises the provider's own error if the key is rejected.

    Listing models costs nothing, so this is also how a key is checked.
    """
    factory = os.getenv("PAGEINDEX_LLM_FACTORY")
    if factory:  # offline runs: the fake model module may provide its own list
        module = importlib.import_module(factory.partition(":")[0])
        if hasattr(module, "list_models"):
            return module.list_models(provider, api_key)
        return [f"{provider}-offline-model"]
    if provider == "anthropic":
        import anthropic

        ids = [model.id for model in anthropic.Anthropic(api_key=api_key).models.list(limit=1000)]
    elif provider == "openai":
        import openai

        ids = [model.id for model in openai.OpenAI(api_key=api_key).models.list()]
        ids = [i for i in ids if i.startswith(("gpt-", "o1", "o3", "o4", "o5", "chatgpt-"))]
    else:
        from google import genai

        ids = [
            model.name.removeprefix("models/")
            for model in genai.Client(api_key=api_key).models.list()
            if model.name and "generateContent" in (model.supported_actions or [])
        ]
        ids = [i for i in ids if i.startswith("gemini-")]
    return sorted({i for i in ids if not any(word in i for word in _NOT_CHAT)})

