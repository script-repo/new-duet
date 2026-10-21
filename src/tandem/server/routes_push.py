"""Web Push and notification settings under /api (SPEC §15.4)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from tandem.engine.notify import QUIET_HOURS
from tandem.push import PushError
from tandem.runtime import Runtime


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=256)
    auth: str = Field(min_length=1, max_length=64)


class SubscriptionBody(BaseModel):
    """The browser's `PushSubscription.toJSON()`."""

    endpoint: str = Field(min_length=1, max_length=1024)
    keys: SubscriptionKeys
    expirationTime: float | None = None  # the browser's field name


class UnsubscribeBody(BaseModel):
    endpoint: str | None = Field(default=None, max_length=1024)
    id: str | None = Field(default=None, max_length=32)


class NotifySettingsBody(BaseModel):
    """Partial update: only the fields sent change. `quiet_hours: null` turns quiet hours off."""

    min_urgency: Literal["low", "normal", "high", "critical"] | None = None
    quiet_hours: str | None = Field(default=None, pattern=QUIET_HOURS.pattern)
    max_per_hour: int | None = Field(default=None, ge=0, le=1000)
    push_enabled: bool | None = None


def push_router(runtime: Runtime, auth: Any) -> APIRouter:
    router = APIRouter(prefix="/api", dependencies=[Depends(auth)])
    push = runtime.push

    @router.get("/push/vapid")
    async def vapid() -> dict[str, Any]:
        return {"public_key": push.public_key, "enabled": runtime.cfg.notify.webpush_on()}

    @router.get("/push/subscriptions")
    async def subscriptions() -> list[dict[str, Any]]:
        return push.list()

    @router.post("/push/subscriptions")
    async def subscribe(body: SubscriptionBody, request: Request) -> dict[str, Any]:
        try:
            return push.subscribe(body.model_dump(), request.headers.get("user-agent", ""))
        except PushError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.delete("/push/subscriptions")
    async def unsubscribe(body: UnsubscribeBody) -> dict[str, Any]:
        if not (body.endpoint or body.id):
            raise HTTPException(422, "endpoint or id required")
        return {"ok": push.unsubscribe(body.endpoint, body.id)}

    @router.post("/push/test")
    async def test() -> dict[str, Any]:
        if not runtime.cfg.notify.webpush_on():
            raise HTTPException(409, "Web Push is turned off")
        n = push.count()
        if not n:
            raise HTTPException(409, "no device has turned on notifications")
        delivered = await push.send(
            {
                "title": "Tandem",
                "body": "Notifications work on this device.",
                "urgency": "normal",
                "url": "/#/settings",
                "tag": "tandem-test",
                "actions": [{"action": "open", "title": "Open"}],
            },
            urgency="normal",
            ttl=300,
            topic="tandem-test",
        )
        return {"delivered": delivered, "subscriptions": n}

    @router.get("/settings/notify")
    async def get_settings() -> dict[str, Any]:
        return runtime.notify_settings()

    @router.put("/settings/notify")
    async def put_settings(body: NotifySettingsBody) -> dict[str, Any]:
        changes = body.model_dump(exclude_unset=True)
        return runtime.update_notify_settings(
            {k: v for k, v in changes.items() if v is not None or k == "quiet_hours"}
        )

    return router
