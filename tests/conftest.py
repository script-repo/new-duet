from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tandem.config import AppConfig, ConnectorConfig, ModelProfile, Settings
from tandem.runtime import Runtime
from tandem.util import FakeClock

TZ = "America/Toronto"
# CI's postgres job points this at a real server; each runtime starts from empty tables.
TEST_DB = os.environ.get("TANDEM_TEST_DATABASE_URL")


def local(hh: int, mm: int = 0, day: int = 21) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=ZoneInfo(TZ))


def mock_cfg(**overrides: Any) -> AppConfig:
    cfg = AppConfig(
        models={
            "strong": ModelProfile(name="strong", provider="mock", model="mock-strong"),
            "cheap": ModelProfile(name="cheap", provider="mock", model="mock-cheap"),
        },
        connectors=[ConnectorConfig(type="fake", name="demo"), ConnectorConfig(type="webhook", name="inbox")],
        demo_autoplay=False,
    )
    cfg.schedule.timezone = TZ
    cfg.sandbox.mode = "local"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


@pytest.fixture
def make_rt(tmp_path: Path) -> Callable[..., Runtime]:
    def make(
        start: datetime | None = None,
        cfg: AppConfig | None = None,
        env: dict[str, str] | None = None,
        **kw: Any,
    ) -> Runtime:
        if TEST_DB:
            from sqlalchemy import create_engine

            from tandem.store import metadata

            engine = create_engine(TEST_DB)
            metadata.drop_all(engine)
            engine.dispose()
        settings = Settings(
            data_dir=tmp_path / "data",
            database_url=TEST_DB or f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}",
            mock=False,
            autostart=False,
            **kw,
        )
        return Runtime(
            settings, cfg or mock_cfg(), FakeClock(start or local(9, 0)), env=env if env is not None else {}
        )

    return make


@pytest.fixture
async def rt(make_rt: Callable[..., Runtime]) -> AsyncIterator[Runtime]:
    runtime = make_rt()
    await runtime.start()
    yield runtime
    await runtime.stop()


async def eventually(pred: Callable[[], Any], timeout: float = 10.0, interval: float = 0.02) -> Any:
    """Poll until `pred()` is truthy (background tasks run on the same loop)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        val = pred()
        if val:
            return val
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(interval)
