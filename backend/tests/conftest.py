"""Test setup: force the mock provider, isolate the DB, and block all network.

The mock provider never constructs an httpx client, so replacing httpx.Client
with a guard both proves and enforces that the suite never hits the network.
"""
from __future__ import annotations

import pathlib
import tempfile

import httpx

from app import db
from app.config import settings


def _no_network(*args, **kwargs):
    raise RuntimeError("Network access is not allowed in tests (mock provider only).")


def pytest_configure():
    settings.llm.provider = "mock"
    httpx.Client = _no_network  # type: ignore[assignment]
    httpx.AsyncClient = _no_network  # type: ignore[assignment]

    tmp = pathlib.Path(tempfile.gettempdir()) / "userstate_pytest.db"
    for suffix in ("", "-wal", "-shm"):
        p = pathlib.Path(str(tmp) + suffix)
        if p.exists():
            p.unlink()
    settings.db_path = tmp
    db.init_db()
