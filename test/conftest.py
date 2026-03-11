"""Pytest fixtures for Novin tests."""

from __future__ import annotations

import os

# Set env before any backend imports (config loads at import time)
os.environ.setdefault("INGEST_ASYNC_DEFAULT", "false")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///./test_novin.db")
os.environ.setdefault("INGEST_API_KEY", "test-ingest-key")

import pytest
from fastapi.testclient import TestClient

from backend.main import app

# Credential for ingest tests (must match INGEST_API_KEY in env)
INGEST_HEADERS = {"x-api-key": "test-ingest-key", "Content-Type": "application/json"}


@pytest.fixture
def client() -> TestClient:
    """FastAPI test client. Uses context manager so lifespan/startup runs."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def ingest_headers() -> dict:
    """Headers with API key for ingest requests."""
    return dict(INGEST_HEADERS)
