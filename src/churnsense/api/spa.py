"""Serve the built front end: its page routes and its hashed assets.

Explicit routes, not a catch-all mount at ``/``: a mount there answers every
path itself, so an unknown path would get the page instead of a JSON 404 and a
wrong method would get 200 instead of 405. Each page of the single-page app has
its own route returning ``index.html``; the bundle's files are served from
``/assets`` by a name checked against a strict pattern, never a joined path.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

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


def install(app: FastAPI) -> None:
    """Add the front end's routes to ``app``; call it after every API route."""
    for path in PAGES:
        app.add_api_route(path, page, methods=["GET"], include_in_schema=False, name=f"page:{path}")
    app.add_api_route("/assets/{name}", asset, methods=["GET"], include_in_schema=False)
    app.add_api_route("/favicon.svg", favicon, methods=["GET"], include_in_schema=False)
