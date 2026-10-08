"""Fetch the raw Telco churn dataset and pin it by content hash.

Two things here are not boilerplate.

**TLS.** A python.org CPython build on macOS ships without CA roots unless the
bundled ``Install Certificates.command`` has been run, so ``urllib`` raises
``CERTIFICATE_VERIFY_FAILED`` on an otherwise healthy network. This module
therefore builds its SSL context from ``certifi`` explicitly rather than
relying on the interpreter's default trust store.

**Immutability.** The raw file is written once, atomically, and verified
against a SHA-256 recorded in ``configs/config.yaml``. If upstream ever
changes the file, the pipeline stops instead of silently producing different
numbers from the ones documented in the README.
"""

from __future__ import annotations

import argparse
import hashlib
import ssl
import urllib.error
import urllib.request
from pathlib import Path

import certifi

from churnsense.config import Config, load_config
from churnsense.exceptions import DataError
from churnsense.logging_setup import get_logger

logger = get_logger(__name__)

_CHUNK = 1 << 16
_TIMEOUT_SECONDS = 60


def sha256_of(path: Path) -> str:
    """SHA-256 of a file, read in chunks so file size does not matter."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _manual_instructions(cfg: Config) -> str:
    return (
        "Could not download the dataset automatically.\n\n"
        "Place it manually instead:\n"
        f"  1. Download: {cfg.dataset.url}\n"
        f"  2. Save it as: {cfg.raw_data_file}\n"
        f"  3. Re-run this command to verify it ({cfg.dataset.expected_rows} data rows expected).\n\n"
        "The same file is also published on Kaggle as 'Telco Customer Churn' "
        "(WA_Fn-UseC_-Telco-Customer-Churn.csv) and is identical in content.\n\n"
        "If the failure was a TLS certificate error, running\n"
        '  "/Applications/Python 3.10/Install Certificates.command"\n'
        "installs the CA roots your Python build is missing and fixes it permanently."
    )


def _fetch(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest`` atomically, verifying TLS via certifi."""
    if not url.startswith("https://"):
        raise DataError(f"refusing to download over a non-HTTPS URL: {url}")

    context = ssl.create_default_context(cafile=certifi.where())
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS, context=context) as response:
            if response.status != 200:
                raise DataError(f"unexpected HTTP status {response.status} for {url}")
            with tmp.open("wb") as fh:
                while chunk := response.read(_CHUNK):
                    fh.write(chunk)
    except (urllib.error.URLError, OSError, ssl.SSLError) as exc:
        tmp.unlink(missing_ok=True)
        raise DataError(f"download failed ({exc.__class__.__name__}: {exc})") from exc
    tmp.replace(dest)  # atomic: a reader never sees a partial file


def _verify(path: Path, cfg: Config) -> str:
    """Check the row count and return the file's SHA-256.

    The expected hash, when configured, is enforced here. The row count is
    checked by counting newlines rather than by parsing with pandas, so a
    corrupt download is caught before it reaches the loader.
    """
    with path.open("rb") as fh:
        data_rows = sum(1 for _ in fh) - 1  # minus the header
    if data_rows != cfg.dataset.expected_rows:
        raise DataError(
            f"{path.name} has {data_rows} data rows, expected {cfg.dataset.expected_rows}. "
            "The file looks truncated or is a different dataset."
        )

    digest = sha256_of(path)
    expected = cfg.dataset.sha256
    if expected and digest != expected:
        raise DataError(
            f"SHA-256 mismatch for {path.name}.\n"
            f"  expected: {expected}\n  actual:   {digest}\n"
            "Upstream content changed. Review the change, then update "
            "`dataset.sha256` in configs/config.yaml if the new file is correct."
        )
    return digest


def download_dataset(cfg: Config | None = None, *, force: bool = False) -> Path:
    """Ensure the raw dataset is present and verified; return its path.

    Idempotent. An existing file is verified rather than re-downloaded, which
    keeps the raw directory immutable across repeated ``make data`` runs.
    """
    cfg = cfg or load_config()
    dest = cfg.raw_data_file
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and not force:
        digest = _verify(dest, cfg)
        logger.info(
            "dataset already present rows=%d sha256=%s", cfg.dataset.expected_rows, digest[:16]
        )
    else:
        logger.info("downloading dataset url=%s", cfg.dataset.url)
        try:
            _fetch(cfg.dataset.url, dest)
        except DataError as exc:
            raise DataError(f"{exc}\n\n{_manual_instructions(cfg)}") from exc
        digest = _verify(dest, cfg)
        logger.info("downloaded bytes=%d sha256=%s", dest.stat().st_size, digest)

    if not cfg.dataset.sha256:
        logger.warning(
            "dataset.sha256 is not pinned in configs/config.yaml. Pin it to make runs "
            "reproducible:\n  dataset:\n    sha256: %s",
            digest,
        )
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download and verify the raw churn dataset.")
    parser.add_argument("--force", action="store_true", help="re-download even if the file exists")
    args = parser.parse_args(argv)
    try:
        path = download_dataset(force=args.force)
    except DataError as exc:
        logger.error("%s", exc)
        return 1
    print(f"OK  {path}  sha256={sha256_of(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
