"""Shared pytest fixtures."""
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session", autouse=True)
def _telemetry_off_for_tests() -> None:
    # Don't ship spans to Jaeger from unit tests.
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")


@pytest.fixture()
def db_session() -> Iterator:
    """Yields a transactional session that rolls back on exit."""
    from core.db import SessionLocal
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
