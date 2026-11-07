"""Serves document images to the page: GET /api/files/{scope}/{doc_id}/images/{name}, and pictures of
an uploaded PDF's pages: GET /api/files/{scope}/{doc_id}/pages/{n}.png (rendered on first request).

`scope` is "sample" for the shared sample library, or the visitor's session token.
Only plain image files inside a document's own folder are ever served.
"""

import re

from pageindex_rag.library import SAMPLE_SCOPE, Library, image_media_type
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, Response
from starlette.routing import Route

from .runtime import sessions, settings

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


api = Starlette(
    routes=[
        Route("/api/files/{scope}/{doc_id}/images/{name}", serve_image),
        Route("/api/files/{scope}/{doc_id}/pages/{name}", serve_page),
    ]
)
