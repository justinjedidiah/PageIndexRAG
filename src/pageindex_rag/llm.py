"""Chat model factory. Model names come from Settings or the visitor's choice, never hardcoded here.

The web app is bring-your-own-key: it always passes the visitor's provider, key and models (an
`LLMConfig`). With no key given, LangChain falls back to the provider's environment variable
(ANTHROPIC_API_KEY, OPENAI_API_KEY or GOOGLE_API_KEY), which is only meant for local dev and the
CLI. Do not set those variables on a public server.
"""

from langchain_core.language_models import BaseChatModel

from . import providers
from .config import Settings
from .providers import LLMConfig, Provider


def chat_model(model: str, api_key: str | None = None, max_tokens: int = 2048, provider: Provider = "anthropic") -> BaseChatModel:
    return providers.chat_model(LLMConfig(provider, api_key), model, max_tokens)


def llm_config(settings: Settings, api_key: str | None = None) -> LLMConfig:
    """The provider and models from settings (environment variables), with an optional key."""
    return LLMConfig(
        settings.provider,
        api_key,
        index_model=settings.index_model,
        vision_model=settings.vision_model,
        agent_model=settings.agent_model,
    )
