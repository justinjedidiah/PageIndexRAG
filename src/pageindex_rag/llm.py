"""Chat model factory. Model names come from Settings, never hardcoded.

The web app is bring-your-own-key: it always passes the visitor's key. With no key
given, langchain falls back to ANTHROPIC_API_KEY, which is only meant for local
dev and the CLI. Do not set that variable on a public server.
"""

import importlib
import os

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel

from .config import Settings


def chat_model(model: str, api_key: str | None = None, max_tokens: int = 2048) -> BaseChatModel:
    factory = os.getenv("PAGEINDEX_LLM_FACTORY")  # "package.module:function", for tests and offline runs
    if factory:
        module_name, _, function = factory.partition(":")
        return getattr(importlib.import_module(module_name), function)(model)
    kwargs = {"api_key": api_key} if api_key else {}
    return ChatAnthropic(model=model, max_tokens=max_tokens, **kwargs)


def validate_key(api_key: str, settings: Settings) -> None:
    """Make one tiny request with the key. Raises if Anthropic rejects it."""
    chat_model(settings.index_model, api_key=api_key, max_tokens=1).invoke("Hi")
