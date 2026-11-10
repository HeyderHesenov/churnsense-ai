"""Serve the built front end: its page routes and its hashed assets.

Explicit routes, not a catch-all mount at ``/``: a mount there answers every
path itself, so an unknown path would get the page instead of a JSON 404 and a
wrong method would get 200 instead of 405. Each page of the single-page app has
its own route returning ``index.html``; the bundle's files are served from
``/assets`` by a name checked against a strict pattern, never a joined path.

A browser that navigates to an address no route has (a stale link, a typo) gets
the same shell with status 404, so the app can show its own Not found page;
everything else - a program's request, another method, any API, asset or docs
path - keeps the JSON 404 (``not_found_page``).
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from churnsense.config import PROJECT_ROOT

#: The Vite build output. Module-level and read per request, so tests can
#: point it elsewhere and a rebuild needs no restart; never taken from a request.
FRONTEND_DIST: Path = PROJECT_ROOT / "frontend" / "dist"

#: The front end's pages, one route each.
PAGES: tuple[str, ...] = (
    "/",
    "/explorer",
    "/patterns",
    "/performance",
    "/explainability",
    "/customer",
    "/simulator",
    "/batch",
)

#: A bundle file name: no separator, no leading dot, nothing a path could hide in.
_ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
#: The file types a build contains, with the type each is served as.
MEDIA_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}
#: Bundle files carry a content hash in their name, so they never change.
IMMUTABLE = "public, max-age=31536000, immutable"


class FrontendNotBuiltError(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=404, detail="The front end has not been built. Run `make frontend`."
        )
        self.error = "frontend_not_built"


def is_built() -> bool:
    """Whether the built front end's shell is there to serve."""
    return (FRONTEND_DIST / "index.html").is_file()


def page() -> FileResponse:
    """The app shell; the page itself is chosen in the browser from the path."""
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise FrontendNotBuiltError
    return FileResponse(index, media_type="text/html; charset=utf-8")


def asset(name: str) -> FileResponse:
    """One file of the bundle, by name only."""
    directory = (FRONTEND_DIST / "assets").resolve()
    path = (directory / name).resolve() if _ASSET_NAME.fullmatch(name) else None
    if (
        path is None
        or path.parent != directory
        or path.suffix not in MEDIA_TYPES
        or not path.is_file()
    ):
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(
        path, media_type=MEDIA_TYPES[path.suffix], headers={"Cache-Control": IMMUTABLE}
    )


def favicon() -> FileResponse:
    path = FRONTEND_DIST / "favicon.svg"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(
        path, media_type=MEDIA_TYPES[".svg"], headers={"Cache-Control": "public, max-age=86400"}
    )


def reserved_segments(app: FastAPI) -> frozenset[str]:
    """The first path segments the app's other routes claim: ``api``, ``docs``, ``health``...

    Read from the app itself, so a new top-level route is never answered with a
    page: the OpenAPI paths hold every API route, included routers too (FastAPI
    caches the schema), and the top-level routes add the docs, assets and icon.
    A route hidden from the schema inside an included router is in neither (an
    included router has no ``path`` of its own): there is none, and one would
    only give a browser's typo under it the shell instead of the JSON 404.
    """
    top_level = (path for route in app.routes if (path := getattr(route, "path", None)))
    return frozenset(
        path.split("/")[1] for path in (*app.openapi()["paths"], *top_level) if path not in PAGES
    )


def not_found_page(request: Request, exc: StarletteHTTPException) -> FileResponse | None:
    """The app shell with status 404 for a page address the app does not have, else None.

    Only a browser navigation qualifies: a GET or HEAD that accepts ``text/html``,
    to a path outside every route's first segment, answered by the router's own
    404 (one that names its ``error``, ``frontend_not_built`` say, keeps it).
    The address is never used to read a file.
    """
    index = FRONTEND_DIST / "index.html"
    if (
        exc.status_code != 404
        or getattr(exc, "error", None) is not None
        or request.method not in {"GET", "HEAD"}
        or "text/html" not in request.headers.get("accept", "").lower()
        or request.url.path.split("/")[1] in reserved_segments(request.app)
        or not index.is_file()
    ):
        return None
    return FileResponse(index, status_code=404, media_type="text/html; charset=utf-8")


def install(app: FastAPI) -> None:
    """Add the front end's routes to ``app``; call it after every API route."""
    for path in PAGES:
        app.add_api_route(path, page, methods=["GET"], include_in_schema=False, name=f"page:{path}")
    app.add_api_route("/assets/{name}", asset, methods=["GET"], include_in_schema=False)
    app.add_api_route("/favicon.svg", favicon, methods=["GET"], include_in_schema=False)
