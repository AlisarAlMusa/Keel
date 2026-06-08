"""Shared pytest configuration and fixtures.

Async mode is configured in ``pyproject.toml`` (``asyncio_mode = "auto"``).
Integration tests that need a real database are gated on ``TEST_DATABASE_URL``
and skip cleanly when it is absent, so the unit/lint CI job stays green without
infrastructure.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def test_database_url() -> str | None:
    """The integration DB DSN, or None when unavailable (tests should skip)."""
    return os.environ.get("TEST_DATABASE_URL")
