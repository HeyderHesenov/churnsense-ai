"""Logging configuration: one function, called by each process entry point.

Library modules only ever call ``logging.getLogger(__name__)``. Configuring
the root logger is the job of whatever owns the process - a ``make`` command,
the API's startup hook, the dashboard - never a side effect of an import,
which would reach into the logging of any program that imported the package.
"""

from __future__ import annotations

import logging
import os
import sys

_configured = False


def configure_logging(level: str | None = None, fmt: str | None = None) -> None:
    """Attach a single stderr handler to the root logger.

    Idempotent: safe to call from every entry point (CLI, API, Streamlit),
    including Streamlit's re-run loop, without stacking duplicate handlers.

    Precedence: explicit argument, then ``CHURNSENSE_LOG_LEVEL``, then the
    value in ``configs/config.yaml``. The config is imported lazily so that a
    broken config file still produces readable log output.
    """
    global _configured
    if _configured:
        if level:
            logging.getLogger().setLevel(level.upper())
        return

    if level is None or fmt is None:
        try:
            from churnsense.config import load_config

            cfg = load_config()
            level = level or os.getenv("CHURNSENSE_LOG_LEVEL") or cfg.log_level
            fmt = fmt or cfg.log_format
        except Exception:  # noqa: BLE001 - logging must work even if config does not
            level = level or os.getenv("CHURNSENSE_LOG_LEVEL") or "INFO"
            fmt = fmt or "%(asctime)s %(levelname)-8s %(name)-28s %(message)s"

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Third-party libraries are chatty at INFO and drown out our own output.
    for noisy in ("matplotlib", "numba", "shap", "PIL", "urllib3", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
