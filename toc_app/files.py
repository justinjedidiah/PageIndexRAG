"""Serves document images to the page: GET /api/files/{scope}/{doc_id}/images/{name}, and pictures of
an uploaded PDF's pages: GET /api/files/{scope}/{doc_id}/pages/{n}.png (rendered on first request).

`scope` is "sample" for the shared sample library, or the visitor's session token.
Only plain image files inside a document's own folder are ever served.
"""

import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable

from toc_rag.library import SAMPLE_SCOPE, Library, image_media_type
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .runtime import sessions, settings

log = logging.getLogger(__name__)

HEADERS = {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}


async def serve_image(request: Request) -> Response:
    scope = request.path_params["scope"]
    try:
        library = Library.for_session(settings, sessions, None if scope == SAMPLE_SCOPE else scope)
    except ValueError:  # not a valid session token
        return Response(status_code=404)
    path = library.image_file(request.path_params["doc_id"], f"images/{request.path_params['name']}")
    if path is None:
        return Response(status_code=404)
    return FileResponse(path, media_type=image_media_type(path), headers=HEADERS)


_PAGE_NAME = re.compile(r"^(\d{1,5})\.png$")


async def serve_page(request: Request) -> Response:
    scope = request.path_params["scope"]
    match = _PAGE_NAME.match(request.path_params["name"])
    if scope == SAMPLE_SCOPE or match is None:
        return Response(status_code=404)
    try:
        library = Library.for_session(settings, sessions, scope)
    except ValueError:  # not a valid session token
        return Response(status_code=404)
    try:
        path = await run_in_threadpool(library.page_file, request.path_params["doc_id"], int(match.group(1)))
    except Exception:  # a damaged file: no page, never a crash
        return Response(status_code=404)
    if path is None:
        return Response(status_code=404)
    return FileResponse(path, media_type="image/png", headers=HEADERS)


UPLOAD_PATH = "/_upload"

# Set by the app (toc_app.py): shows a visitor why their upload was refused here. These refusals happen
# before the app's upload handler runs, and Reflex's page only logs a failed upload to the browser console,
# so without this the visitor would see nothing happen. Arguments: the client token, the message.
upload_refused: Callable[[str, str], Awaitable[None]] | None = None


async def _refuse_upload(scope: Scope, receive: Receive, send: Send, status: int, message: str) -> None:
    token = dict(scope["headers"]).get(b"reflex-client-token", b"").decode("latin-1")
    if upload_refused is not None and token:
        try:
            await upload_refused(token, message)
        except Exception:
            log.exception("Could not tell the page an upload was refused")
    headers = {"Retry-After": "120"} if status == 429 else None
    await Response(message, status_code=status, headers=headers)(scope, receive, send)
# The file limit plus room for the multipart wrapping around it.
MAX_UPLOAD_REQUEST_BYTES = settings.max_upload_bytes + 1024 * 1024


def limit_upload_size(app: ASGIApp) -> ASGIApp:
    """Refuse an upload request bigger than the file limit before the framework spools it to disk.

    Reflex parses the whole multipart body (to temporary files, with no size cap) before the upload
    handler can check anything, so without this a script could fill the disk with one request.
    A body with no Content-Length, or one that sends more than it declared, is cut off too.
    """

    async def guarded(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].rstrip("/").endswith(UPLOAD_PATH):
            await app(scope, receive, send)
            return
        length = dict(scope["headers"]).get(b"content-length", b"")
        if not length.isdigit() or int(length) > MAX_UPLOAD_REQUEST_BYTES:
            message = f"That file is larger than the {settings.max_upload_bytes // (1024 * 1024)} MB limit."
            await _refuse_upload(scope, receive, send, 413, message)
            return
        received = 0

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_UPLOAD_REQUEST_BYTES:
                    return {"type": "http.disconnect"}  # the parser stops as if the client had left
            return message

        await app(scope, counted, send)

    return guarded


RATE_WINDOW_SECONDS = 600
_PAGE_PATH = re.compile(r"^/api/files/[^/]+/[^/]+/pages/")


class RateLimiter:
    """At most `limit` requests per address in a sliding window. In memory: a restart forgets it, which is fine."""

    def __init__(self, limit: int, window: float = RATE_WINDOW_SECONDS, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._hits: dict[str, deque[float]] = {}

    def allow(self, address: str) -> bool:
        now = self.clock()
        hits = self._hits.setdefault(address, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        if len(self._hits) > 10_000:  # forget addresses that have gone quiet, so this never grows without end
            self._hits = {a: h for a, h in self._hits.items() if h and now - h[-1] < self.window}
        return True


def client_address(scope: Scope) -> str:
    """The visitor's address. Behind Railway's proxy the connection comes from the proxy, which names the
    visitor in X-Real-IP / X-Forwarded-For. A forged header only gets someone a fresh allowance here; the
    server-wide caps (parallel reads, live sessions) do not depend on it."""
    headers = dict(scope.get("headers") or [])
    real = headers.get(b"x-real-ip", b"").decode("latin-1").strip()
    forwarded = headers.get(b"x-forwarded-for", b"").decode("latin-1").split(",")[0].strip()
    return real or forwarded or (scope.get("client") or ("unknown",))[0]


def rate_limit(app: ASGIApp) -> ASGIApp:
    """Per-address limits on the two requests that cost the server real work: uploads and page pictures."""
    upload_limiter = RateLimiter(settings.uploads_per_ip)
    limits = [
        (lambda path: path.rstrip("/").endswith(UPLOAD_PATH), upload_limiter),
        (lambda path: bool(_PAGE_PATH.match(path)), RateLimiter(settings.page_renders_per_ip)),
    ]

    async def limited(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            for matches, limiter in limits:
                if matches(scope["path"]) and not limiter.allow(client_address(scope)):
                    if limiter is upload_limiter:
                        message = "Too many uploads in a short time. Wait a few minutes and try again."
                        await _refuse_upload(scope, receive, send, 429, message)
                    else:
                        response = Response("Too many requests. Wait a few minutes.", status_code=429, headers={"Retry-After": "120"})
                        await response(scope, receive, send)
                    return
        await app(scope, receive, send)

    return limited


def _content_security_policy(api_url: str) -> str:
    """What the page may load and talk to.

    Scripts can't be locked down: Reflex runs inline scripts and uses eval (that is how rx.call_script
    works). The rest still matters: pictures only from this site, so text in a document can't make an
    answer load an outside image that carries data away; connections only to this backend; the page can
    never be shown inside another site's frame, so nobody can lay a fake key form over it.
    """
    connect = ["'self'"]
    if api_url.startswith(("https://", "http://")):
        host = api_url.split("://", 1)[1].rstrip("/")
        connect += [api_url.rstrip("/"), f"{'wss' if api_url.startswith('https') else 'ws'}://{host}"]
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' data: https://fonts.gstatic.com",
            "img-src 'self' data: blob:",
            f"connect-src {' '.join(connect)}",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
        ]
    )


def security_headers(app: ASGIApp) -> ASGIApp:
    """Add the browser's safety headers to every response the backend sends (in production, the page too)."""
    import reflex as rx

    api_url = rx.config.get_config().api_url
    headers = [
        (b"content-security-policy", _content_security_policy(api_url).encode()),
        (b"x-frame-options", b"DENY"),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"no-referrer"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    ]
    if api_url.startswith("https://"):  # only behind real HTTPS: a browser remembers this for a year
        headers.append((b"strict-transport-security", b"max-age=31536000"))

    async def with_headers(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                extra = [(name, value) for name, value in headers if name not in present]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await app(scope, receive, send_with_headers)

    return with_headers


api = Starlette(
    routes=[
        Route("/api/files/{scope}/{doc_id}/images/{name}", serve_image),
        Route("/api/files/{scope}/{doc_id}/pages/{name}", serve_page),
    ]
)
