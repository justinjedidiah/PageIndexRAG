"""Turn model-provider errors into messages that are safe and useful to show a visitor.

Works for Anthropic, OpenAI and Google Gemini: it recognises LangChain's standard error types, then
any error carrying an HTTP status (the SDKs' own errors), then connection failures. It also looks at
the errors an exception was raised from, since LangChain often wraps the SDK's error.
"""

from collections.abc import Iterator

from langchain_core import exceptions as lc

_PROVIDER_NAMES = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google",
    "langchain_google_genai": "Google",
    "langchain_openai": "OpenAI",
    "langchain_anthropic": "Anthropic",
}


def friendly_error(exc: Exception, provider: str | None = None) -> str:
    """Never include the raw exception text: it could echo request details, or even the key.

    `provider` is the name to show ("OpenAI"); without it, it is worked out from where the error came from.
    """
    name = provider or _provider_name(exc)
    status = _status(exc)
    if _any(exc, lc.ModelAuthenticationError) or status == 401:
        return f"{name} rejected that API key. Check it and try again."
    if _any(exc, lc.ModelPermissionDeniedError) or status == 403:
        return "That API key is not allowed to use this model."
    if _any(exc, lc.ModelNotFoundError) or status == 404:
        return "That model is not available for this API key. Choose another model."
    if _any(exc, lc.ModelRateLimitError) or status == 429:
        return f"{name} says your key hit a rate limit or has no credit left. Wait a moment or check your account."
    if _any(exc, lc.ModelConnectionError, lc.ModelTimeoutError) or _looks_like_connection_error(exc):
        return f"Could not reach {name}. Check the connection and try again."
    if _any(exc, lc.ModelInvalidRequestError) or status == 400 or _looks_like_context_overflow(exc):
        return _bad_request(exc, name)
    if status:
        return f"{name} returned an error (status {status}). Try again in a moment."
    return "Something went wrong. Try again."


# A 400 ("bad request") has many causes. The provider's own text usually says which; it is read here on
# the server and matched against known causes, but never shown: the visitor only ever sees these sentences.
_UNSUPPORTED = ("not supported", "unsupported", "does not support", "doesn't support", "not available", "not enabled")
_TOO_LONG = (
    "context length", "context window", "maximum context", "too many tokens", "prompt is too long",
    "input is too long", "exceeds the maximum", "token limit", "too long",
)


def _bad_request(exc: Exception, name: str) -> str:
    reason = _raw_text(exc)
    unsupported = any(phrase in reason for phrase in _UNSUPPORTED)
    if unsupported and ("tool" in reason or "function" in reason):
        return "This model doesn't support tool calling, which the chat needs. Choose another model for chat."
    if unsupported and any(word in reason for word in ("response_format", "json_schema", "structured", "json mode")):
        return "This model doesn't support structured output, which indexing needs. Choose another model for indexing."
    if (unsupported or "invalid" in reason) and "image" in reason:
        return "This model can't read images. Choose another model for indexing, or turn image descriptions off."
    if _looks_like_context_overflow(exc) or any(phrase in reason for phrase in _TOO_LONG):
        return "That was too long for this model. Choose a model with a larger context."
    return f"{name} couldn't process the request with this model (status 400). Choosing another model may help."


def _raw_text(exc: Exception) -> str:
    """All the error text in the chain, lower-cased, for matching only. Never shown or logged."""
    return " ".join(str(e) for e in _chain(exc)).lower()


def _looks_like_context_overflow(exc: Exception) -> bool:
    return any("ContextOverflow" in type(e).__name__ for e in _chain(exc))


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """The exception and the ones it was raised from, without looping."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _any(exc: Exception, *types: type) -> bool:
    return any(isinstance(e, types) for e in _chain(exc))


def _status(exc: Exception) -> int | None:
    """The HTTP status from the SDK error: `status_code` (Anthropic, OpenAI) or `code` (Google)."""
    for e in _chain(exc):
        for attribute in ("status_code", "code"):
            value = getattr(e, attribute, None)
            if isinstance(value, int) and 400 <= value < 600:
                return value
    return None


def _looks_like_connection_error(exc: Exception) -> bool:
    return any("Connection" in type(e).__name__ or "Timeout" in type(e).__name__ for e in _chain(exc))


def _provider_name(exc: Exception) -> str:
    for e in _chain(exc):
        root = type(e).__module__.split(".")[0]
        if root in _PROVIDER_NAMES:
            return _PROVIDER_NAMES[root]
    return "The model provider"
