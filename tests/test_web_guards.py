"""The web app's request guards: upload size, per-address rate limits, safety headers."""

from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from toc_app import files
from toc_app.files import MAX_UPLOAD_REQUEST_BYTES, RateLimiter, client_address, limit_upload_size, security_headers
from toc_rag.sessions import SessionManager


async def echo(scope, receive, send):
    body = await Request(scope, receive).body()
    await PlainTextResponse(f"ok {len(body)}")(scope, receive, send)


def test_upload_size_is_refused_before_the_body_is_read():
    client = TestClient(limit_upload_size(echo))
    assert client.post("/_upload", content=b"x" * 1000).text == "ok 1000"
    assert client.post("/_upload", content=b"x" * (MAX_UPLOAD_REQUEST_BYTES + 1)).status_code == 413
    assert client.post("/_upload", content=iter([b"x" * 10])).status_code == 413  # no Content-Length
    assert client.post("/elsewhere", content=b"x" * (MAX_UPLOAD_REQUEST_BYTES + 1)).status_code == 200


def test_rate_limiter_slides():
    now = [0.0]
    limiter = RateLimiter(2, window=60, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a")
    assert limiter.allow("b")  # per address
    now[0] = 61
    assert limiter.allow("a")


def test_rate_limit_covers_uploads_and_page_pictures():
    client = TestClient(files.rate_limit(echo))
    headers = {"x-real-ip": "203.0.113.7"}
    statuses = [client.post("/_upload", content=b"x", headers=headers).status_code for _ in range(files.settings.uploads_per_ip + 1)]
    assert statuses[-1] == 429 and set(statuses[:-1]) == {200}
    assert client.post("/_upload", content=b"x", headers={"x-real-ip": "203.0.113.8"}).status_code == 200
    page = "/api/files/abc/doc/pages/3.png"
    statuses = [client.get(page, headers=headers).status_code for _ in range(files.settings.page_renders_per_ip + 1)]
    assert statuses[-1] == 429
    assert client.get("/api/files/abc/doc/images/img-1.png", headers=headers).status_code == 200  # not limited


def test_client_address_prefers_the_proxy_headers():
    scope = {"client": ("10.0.0.1", 5000), "headers": [(b"x-forwarded-for", b"198.51.100.4, 10.0.0.2")]}
    assert client_address(scope) == "198.51.100.4"
    assert client_address({"client": ("10.0.0.1", 5000), "headers": []}) == "10.0.0.1"


def test_security_headers_are_added_without_replacing_existing_ones():
    async def framed(scope, receive, send):
        await PlainTextResponse("hi", headers={"x-frame-options": "SAMEORIGIN"})(scope, receive, send)

    response = TestClient(security_headers(framed)).get("/")
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "img-src 'self' data: blob:" in csp
    assert response.headers["x-frame-options"] == "SAMEORIGIN"  # the app's own choice wins
    assert response.headers["referrer-policy"] == "no-referrer"


def test_csp_allows_the_public_backend_socket():
    csp = files._content_security_policy("https://toc.example.app")
    assert "connect-src 'self' https://toc.example.app wss://toc.example.app" in csp


def test_session_count_ignores_expired_ones(tmp_path):
    now = [1000.0]
    manager = SessionManager(tmp_path, ttl_seconds=60, clock=lambda: now[0])
    manager.ensure("visitor-one")
    now[0] = 1050
    manager.ensure("visitor-two")
    assert manager.count() == 2
    now[0] = 1065  # the first has expired, the second has not
    assert manager.count() == 1
