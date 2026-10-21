"""Shared bits for the OAuth connector tests: recorded fixtures, fake env, connected runtimes."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

from tandem.config import AppConfig, ConnectorConfig
from tandem.runtime import Runtime
from tandem.tools import ToolContext, ToolOutcome
from tests.conftest import eventually, mock_cfg

FIXTURES = Path(__file__).parent.parent / "fixtures" / "connectors"
PUBLIC = "https://tandem.example.com"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CAL = "https://www.googleapis.com/calendar/v3"
SLACK = "https://slack.com/api"
GRAPH = "https://graph.microsoft.com/v1.0"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"

# Obviously fake values; long enough that the Redactor treats the *_SECRET ones as secrets.
ENV = {
    "GOOGLE_CLIENT_ID": "fake-google-client-id.apps.example",
    "GOOGLE_CLIENT_SECRET": "fake-google-client-secret",
    "SLACK_CLIENT_ID": "fake-slack-client-id",
    "SLACK_CLIENT_SECRET": "fake-slack-client-secret",
    "SLACK_SIGNING_SECRET": "fake-slack-signing-secret",
    "MS_CLIENT_ID": "fake-ms-client-id",
    "MS_CLIENT_SECRET": "fake-ms-client-secret",
}


def fx(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def fake_jwt(claims: dict[str, Any]) -> str:
    def enc(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc(claims)}.fake-signature"


def cfg_with(*conns: ConnectorConfig) -> AppConfig:
    return mock_cfg(connectors=[ConnectorConfig(type="webhook", name="inbox"), *conns])


def connect(rt: Runtime, name: str, provider: str, account: str = "sam@example.com", **extra: Any) -> None:
    rt.vault.save(
        name,
        provider,
        {
            "access_token": f"fake-access-{name}-1",
            "refresh_token": f"fake-refresh-{name}",
            "expires_in": 3600,
            **extra,
        },
        account=account,
    )


async def approve_call(rt: Runtime, tool: str, args: dict[str, Any]) -> ToolOutcome:
    """Invoke a gated tool, check it waits on a card, approve the card, return the outcome."""
    task = asyncio.create_task(rt.registry.invoke(tool, args, ToolContext(origin="chat", rt=rt)))
    pending = await eventually(lambda: rt.store.list_approvals(state="pending"))
    assert pending[0]["tool"] == tool
    await rt.approvals.decide(pending[0]["id"], "approve")
    return await task
