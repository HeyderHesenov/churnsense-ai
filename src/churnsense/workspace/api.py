"""``POST /api/v2/imports/preview`` - served only where it was asked for, and never in public.

**Off unless switched on, and never in a public demo.** ``install`` adds the
route only when both hold:

* the environment sets ``CHURNSENSE_V2_DATA_HUB_PREVIEW=1`` (exactly ``1``), and
* the configuration's ``deployment.mode`` is ``local``.

Anything else - the variable unset, any other value, or ``public_demo`` with
the variable set - leaves the route out of the application altogether: it is
not routed, not in the OpenAPI schema and not in ``/docs``. The variable set
in a public demo is logged as an error and changes nothing else; the service
starts as it always has. The route checks the mode again on every request, so
mounting the router by hand cannot serve it in a public demo either.

Every request still passes the service's own layers first
(``churnsense.api.middleware``): loopback peers only, the allowed hosts and
origins, the upload body limit and the batch budget.

**Nothing is written.** Starlette spools an uploaded file over 1 MB to a
temporary file on disk, so the form is parsed here by a parser whose spool
never rolls over before the body limit: the upload stays in memory, for this
request only, and is gone when the response is sent.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Final

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from churnsense.api import middleware
from churnsense.api.schemas import ErrorResponse
from churnsense.api.state import batch_slot
from churnsense.config import Config, load_config
from churnsense.exceptions import SchemaValidationError
from churnsense.workspace import schemas as out
from churnsense.workspace.preview import Limits, build_preview

logger = logging.getLogger(__name__)

#: The environment variable that switches the preview on, in a local deployment only.
FLAG: Final = "CHURNSENSE_V2_DATA_HUB_PREVIEW"
PREFIX: Final = "/api/v2"
PREVIEW_PATH: Final = f"{PREFIX}/imports/preview"
#: The form's fields: the file, and two optional JSON objects.
FORM_FIELDS: Final = frozenset({"file", "mapping", "status_mapping"})
#: The largest mapping or status mapping a form field may hold.
FIELD_BYTES: Final = 64 * 1024


def preview_enabled(environ: Mapping[str, str] | None = None, cfg: Config | None = None) -> bool:
    """Whether this process serves the preview: the flag is exactly ``1`` and the mode is local."""
    value = (os.environ if environ is None else environ).get(FLAG)
    if value is None or value == "":
        return False
    if value != "1":
        logger.error("%s must be exactly 1 to enable the V2 Data Hub preview; it stays off", FLAG)
        return False
    mode = (cfg or load_config()).deployment.mode
    if mode != "local":
        logger.error(
            "%s is set but deployment.mode is %s: the V2 Data Hub preview is served only in "
            "a local deployment, so it stays off",
            FLAG,
            mode,
        )
        return False
    return True


def _local_only() -> None:
    """Refuse the route wherever the deployment is not local, however it was mounted."""
    if load_config().deployment.mode != "local":
        raise HTTPException(status_code=404, detail="Not Found")


class _InMemoryParser(MultiPartParser):
    """Starlette's multipart parser with a spool that never reaches the disk.

    ``BodySizeLimit`` refuses any body over the upload limit before it is read,
    so a spool as large as that limit is never rolled over to a file.
    """

    def __init__(self, request: Request, spool_bytes: int) -> None:
        super().__init__(
            request.headers,
            request.stream(),
            max_files=1,
            max_fields=len(FORM_FIELDS) - 1,
            max_part_size=FIELD_BYTES,
        )
        self.spool_max_size = spool_bytes


async def _form(request: Request, cfg: Config) -> tuple[bytes, str | None, str | None]:
    """The upload's bytes and its two optional fields, read in memory and checked."""
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise SchemaValidationError("send the file as multipart/form-data in a field named 'file'")
    spool = cfg.api.max_upload_bytes + middleware.MULTIPART_OVERHEAD_BYTES
    form: FormData | None = None
    try:
        try:
            form = await _InMemoryParser(request, spool).parse()
        except (MultiPartException, ValueError):
            # A malformed body (python-multipart's errors are ValueErrors) or a breach of
            # the parser's limits. Their own text is not repeated.
            raise SchemaValidationError(
                "the form could not be read: send one file in 'file' and at most the "
                f"fields 'mapping' and 'status_mapping', each under {FIELD_BYTES // 1024} KB"
            ) from None
        if unknown := sorted(set(form.keys()) - FORM_FIELDS):
            raise SchemaValidationError(
                f"unexpected form field(s): {len(unknown)}; the form takes 'file', 'mapping' "
                "and 'status_mapping' only"
            )
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise SchemaValidationError("send the CSV as a file in a form field named 'file'")
        payload = await file.read(cfg.api.max_upload_bytes + 1)
        if len(payload) > cfg.api.max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {cfg.api.max_upload_bytes / 1_048_576:.0f} MB limit.",
            )
        fields = {}
        for name in ("mapping", "status_mapping"):
            value = form.get(name)
            if value is not None and not isinstance(value, str):
                raise SchemaValidationError(f"'{name}' must be a text field holding a JSON object")
            fields[name] = value
        return payload, fields["mapping"], fields["status_mapping"]
    finally:
        if form is not None:
            await form.close()


def _preview_in_slot(
    payload: bytes, cfg: Config, mapping: str | None, statuses: str | None
) -> out.ImportPreviewResponse:
    """The preview, computed while holding one of the service's batch slots."""
    with batch_slot():
        return build_preview(
            payload,
            Limits(max_columns=cfg.api.max_upload_columns, max_rows=cfg.api.max_batch_rows),
            mapping,
            statuses,
        )


router = APIRouter(tags=["v2 data hub preview"], dependencies=[Depends(_local_only)])

_FORM_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {
                            "type": "string",
                            "format": "binary",
                            "description": "CSV of account snapshots, UTF-8, one header row.",
                        },
                        "mapping": {
                            "type": "string",
                            "description": 'JSON object {"source column": "canonical field"}. '
                            "When sent, it is complete and the only mapping used.",
                        },
                        "status_mapping": {
                            "type": "string",
                            "description": 'JSON object {"source status value": "canonical '
                            'status"}.',
                        },
                    },
                }
            }
        },
    }
}


@router.post(
    "/imports/preview",
    response_model=out.ImportPreviewResponse,
    responses={status: {"model": ErrorResponse} for status in (400, 403, 404, 413, 422, 429, 500)},
    openapi_extra=_FORM_SCHEMA,
)
async def import_preview(request: Request) -> out.ImportPreviewResponse:
    """Map and validate a CSV of account snapshots, and report what it would import.

    Stateless: nothing is stored, logged or committed. A file or a mapping that
    cannot be read at all is a 422 with every reason; a readable file gets a
    preview, its invalid rows rejected with their reasons.
    """
    cfg = load_config()
    payload, mapping, statuses = await _form(request, cfg)
    # The slot is taken after the body has arrived, as V1's batch scoring takes it.
    return await run_in_threadpool(_preview_in_slot, payload, cfg, mapping, statuses)


def install(
    app: FastAPI, environ: Mapping[str, str] | None = None, cfg: Config | None = None
) -> bool:
    """Add the preview to ``app`` if ``preview_enabled``; whether it was added.

    ``environ`` and ``cfg`` default to the process's environment and configuration.
    """
    if not preview_enabled(environ, cfg):
        return False
    app.include_router(router, prefix=PREFIX)
    middleware.LOCAL_UPLOAD_PATHS = middleware.LOCAL_UPLOAD_PATHS | {PREVIEW_PATH}
    logger.warning(
        "the V2 Data Hub preview is enabled at %s: local only, synthetic or test data only",
        PREVIEW_PATH,
    )
    return True
