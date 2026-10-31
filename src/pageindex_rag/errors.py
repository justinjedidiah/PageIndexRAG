"""Turn API errors into messages that are safe and useful to show a visitor."""

import anthropic


def friendly_error(exc: Exception) -> str:
    """Never include the raw exception text: it could echo request details."""
    if isinstance(exc, anthropic.AuthenticationError):
        return "Anthropic rejected that API key. Check it and try again."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "That API key is not allowed to use this model."
    if isinstance(exc, anthropic.RateLimitError):
        return "Anthropic says your key hit a rate limit or has no credit left. Wait a moment or check your account."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Could not reach Anthropic. Check the connection and try again."
    if isinstance(exc, anthropic.APIStatusError):
        return f"Anthropic returned an error (status {exc.status_code}). Try again in a moment."
    return "Something went wrong. Try again."
