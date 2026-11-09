"""Reading an uploaded CSV, once, for every endpoint that scores one."""

from __future__ import annotations

import pandas as pd
from fastapi import HTTPException, UploadFile

from churnsense.config import Config
from churnsense.models.predict import validate_upload


def read_upload(file: UploadFile, cfg: Config, columns: list[str]) -> tuple[bytes, pd.DataFrame]:
    """The upload's bytes and its frame, validated for the model scoring ``columns``.

    ``BodySizeLimit`` has already refused any request body much larger than
    the limit, so the file here is at most a few KB over it; reading one byte
    past the limit is enough to tell, and the CSV parser never sees it. An
    invalid file raises ``SchemaValidationError``, which the service answers
    with 422 and every problem found.
    """
    payload = file.file.read(cfg.api.max_upload_bytes + 1)
    if len(payload) > cfg.api.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {cfg.api.max_upload_bytes / 1_048_576:.0f} MB limit.",
        )
    return payload, validate_upload(payload, cfg, columns=columns)
