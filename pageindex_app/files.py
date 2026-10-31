"""Serves document images to the page: GET /api/files/{scope}/{doc_id}/images/{name}.

`scope` is "sample" for the shared sample library, or the visitor's session token.
Only plain image files inside a document's own folder are ever served.
"""

from pageindex_rag.library import SAMPLE_SCOPE, Library, image_media_type
from starlette.applications import Starlette
from starlette.requests import Request
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


api = Starlette(routes=[Route("/api/files/{scope}/{doc_id}/images/{name}", serve_image)])
