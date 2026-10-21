"""Fixtures for the OAuth connector tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import respx

PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> Iterator[respx.MockRouter]:
    """Recorded responses only: any request without a matching route fails the test."""
    for var in PROXY_VARS:  # a host's proxy settings must not change how httpx builds clients
        monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(var.lower(), raising=False)
    with respx.mock(assert_all_called=False) as router:
        yield router
