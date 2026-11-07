"""TOC RAG: chat with documents by navigating their table of contents."""

import asyncio
import logging

import reflex as rx

from .files import api, limit_upload_size, rate_limit, security_headers
from .home import APP_ROUTE, home
from .runtime import sessions
from .state import State
from .views import index

log = logging.getLogger(__name__)
SWEEP_EVERY_SECONDS = 300

app = rx.App(
    # Applied in order: the size limit, rate limits and headers wrap everything, /_upload and (in production) the page included.
    api_transformer=[api, limit_upload_size, rate_limit, security_headers],
    stylesheets=["/app.css"],
    head_components=[
        # Tab icon: the header's open book. The SVG switches to a light tile in dark mode; the .ico is for
        # browsers that only look for /favicon.ico.
        rx.el.link(rel="icon", href="/favicon.svg", type="image/svg+xml"),
        rx.el.link(rel="icon", href="/favicon.ico", sizes="48x48"),
    ],
)
# "/" is the static home page; the app itself (key, library, chat) is at "/app".
app.add_page(
    home,
    route="/",
    title="TOC RAG: vectorless RAG over your documents",
    description="An AI agent answers questions about your PDF and Word documents by navigating their table of contents, with cited sources.",
)
app.add_page(index, route=APP_ROUTE, title="TOC RAG", on_load=State.on_load)


async def sweep_expired_sessions() -> None:
    """Delete session folders past their lifetime. This is what guarantees cleanup."""
    while True:
        try:
            removed = await asyncio.to_thread(sessions.sweep)
            if removed:
                log.info("Removed %d expired session(s)", removed)
        except Exception:
            log.exception("Session sweep failed")
        await asyncio.sleep(SWEEP_EVERY_SECONDS)


app.register_lifespan_task(sweep_expired_sessions)
