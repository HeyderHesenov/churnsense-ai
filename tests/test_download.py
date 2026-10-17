"""Tests for fetching the raw dataset. No network: ``urlopen`` is replaced."""

from __future__ import annotations

import http.client
import io

import pytest

from churnsense.data import download
from churnsense.data.download import download_dataset, sha256_of
from churnsense.exceptions import DataError
from tests.conftest import pinned_copy


class _Response(io.BytesIO):
    """What ``urlopen`` returns, without the network."""

    status = 200


class _DroppedMidBody(_Response):
    """Sends the first chunk, then the connection drops."""

    def read(self, size: int | None = -1) -> bytes:
        if self.tell():
            raise http.client.IncompleteRead(b"")
        return super().read(size)


@pytest.fixture
def pinned(demo_csv, tmp_path, cfg):
    """A config whose dataset is the demo file, pinned to its real hash."""
    return pinned_copy(demo_csv, tmp_path, cfg, sha256=sha256_of(demo_csv))


@pytest.fixture
def serve(monkeypatch):
    """Answer the next download with the given response."""

    def install(response: _Response) -> None:
        monkeypatch.setattr(download.urllib.request, "urlopen", lambda *_, **__: response)

    return install


def _leftovers(cfg) -> list:
    return sorted(cfg.paths.raw_dir.glob("*.part"))


def test_a_forced_download_that_fails_verification_keeps_the_verified_copy(pinned, serve):
    """The download was renamed over the dataset before it was checked, so a
    changed upstream file replaced the good copy and then failed."""
    dest = pinned.raw_data_file
    good = dest.read_bytes()
    serve(_Response(good.replace(b"DEMO-", b"DEMX-", 1)))  # same rows, other bytes

    with pytest.raises(DataError, match="SHA-256 mismatch"):
        download_dataset(pinned, force=True)
    assert dest.read_bytes() == good
    assert _leftovers(pinned) == []


def test_a_truncated_download_never_lands_at_the_dataset_path(pinned, serve):
    dest = pinned.raw_data_file
    good = dest.read_bytes()
    dest.unlink()
    serve(_Response(good[: len(good) // 2]))

    with pytest.raises(DataError, match="data rows"):
        download_dataset(pinned)
    assert not dest.exists()
    assert _leftovers(pinned) == []


def test_a_verified_download_is_written_in_place(pinned, serve):
    dest = pinned.raw_data_file
    good = dest.read_bytes()
    dest.unlink()
    serve(_Response(good))

    assert download_dataset(pinned) == dest
    assert dest.read_bytes() == good
    assert _leftovers(pinned) == []


def test_a_download_interrupted_while_it_is_checked_is_removed(pinned, serve, monkeypatch):
    """Any failure between the download and the rename keeps the verified copy
    and removes the partial file, not only a failed check: here the file cannot
    be read back."""
    dest = pinned.raw_data_file
    good = dest.read_bytes()
    serve(_Response(good.replace(b"DEMO-", b"DEMX-", 1)))  # never verified

    def unreadable(*_, **__):
        raise PermissionError("denied")

    monkeypatch.setattr(download, "verify_dataset", unreadable)
    with pytest.raises(PermissionError):
        download_dataset(pinned, force=True)
    assert dest.read_bytes() == good
    assert _leftovers(pinned) == []


def test_a_connection_dropped_mid_body_is_a_data_error(pinned, serve):
    """IncompleteRead is not an OSError: it escaped as a traceback, without the
    manual-download instructions, and left the partial file behind."""
    dest = pinned.raw_data_file
    good = dest.read_bytes()
    dest.unlink()
    serve(_DroppedMidBody(good))

    with pytest.raises(DataError, match="Place it manually"):
        download_dataset(pinned)
    assert not dest.exists()
    assert _leftovers(pinned) == []
