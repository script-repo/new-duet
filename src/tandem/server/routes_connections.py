"""Connections API (SPEC §15.3): list connectors, start an OAuth sign-in, finish it, disconnect.

The callback has no bearer auth: the single-use `state` (carrying the PKCE verifier) is the CSRF guard.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from tandem import oauth
from tandem.connectors.base import CONNECTOR_TYPES
from tandem.connectors.oauth_base import OAuthConnector

log = logging.getLogger("tandem.server.connections")


def _ui(**params: str) -> RedirectResponse:
    return RedirectResponse(f"/#/connections?{urlencode(params)}", status_code=303)


def connection_view(c: Any) -> dict[str, Any]:
    """One connector as the Connections screen shows it; never any token."""
    st = c.status()
    if isinstance(c, OAuthConnector):
        return st
    return {
        **st,
        "provider": None,
        "display_name": c.name,
        "oauth": False,
        "configured": True,
        "client_configured": True,
        "connected": st.get("status") == "ok",
        "account": None,
        "scopes": [],
        "expires_at": None,
        "push": {"mode": "push" if c.type == "webhook" else "poll", "detail": ""},
    }


def available_providers(rt: Any) -> list[dict[str, Any]]:
    """OAuth connector types, whether their client env vars are set, and whether one is configured."""
    out = []
    in_use = {c.type for c in rt.connectors.values()}
    for cls in CONNECTOR_TYPES.values():
        if not (isinstance(cls, type) and issubclass(cls, OAuthConnector)):
            continue
        p = oauth.PROVIDERS[cls.provider]
        out.append(
            {
                "type": cls.type,
                "provider": cls.provider,
                "display_name": cls.display_name,
                "default_name": cls.default_name,
                "env": [p.client_id_env, p.client_secret_env],
                "client_configured": bool(
                    rt.secrets.get(p.client_id_env) and rt.secrets.get(p.client_secret_env)
                ),
                "in_use": cls.type in in_use,
                "docs": "docs/CONNECTORS.md",
            }
        )
    return out


def connections_router(rt: Any, auth: Callable[..., Any]) -> APIRouter:
    router = APIRouter()
    api = Depends(auth)
    states = oauth.StateStore(rt.store, rt.cipher)

    def oauth_connector(name: str) -> OAuthConnector:
        c = rt.connectors.get(name)
        if c is None:
            raise HTTPException(404, "no such connector")
        if not isinstance(c, OAuthConnector):
            raise HTTPException(400, f"{name} does not use OAuth")
        return c

    @router.get("/api/connections", dependencies=[api])
    async def list_connections() -> dict[str, Any]:
        return {
            "connections": [connection_view(c) for c in rt.connectors.values()],
            "available": available_providers(rt),
            "redirect_uri": f"{rt.settings.base_url}/api/oauth/callback",
            "public_url": rt.settings.base_url,
            "push_capable": rt.settings.base_url.startswith("https://"),
        }

    @router.post("/api/connections/{name}/authorize", dependencies=[api])
    async def authorize(name: str) -> dict[str, str]:
        c = oauth_connector(name)
        try:
            app = c.app()
        except oauth.OAuthError as exc:
            raise HTTPException(400, str(exc)) from exc
        verifier, challenge = oauth.pkce_pair()
        state = states.create(name, verifier)
        return {"url": oauth.authorize_url(app, c.scope_list(), state, challenge)}

    @router.get("/api/oauth/callback")
    async def callback(
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
        error_description: str | None = None,
    ) -> RedirectResponse:
        if error:
            # Still burn the state so it can't be replayed.
            if state:
                try:
                    states.consume(state)
                except oauth.OAuthError:
                    pass
            return _ui(error=(error_description or error)[:200])
        try:
            name, verifier = states.consume(state or "")
        except oauth.OAuthError as exc:
            return _ui(error=f"invalid_state: {exc}")
        c = rt.connectors.get(name)
        if not isinstance(c, OAuthConnector) or not code:
            return _ui(error="missing code", name=name)
        try:
            app = c.app()
            tok = await oauth.exchange_code(app, code, verifier)
        except Exception as exc:
            msg = rt.redactor.text(str(exc))[:200]
            rt.store.add_activity("connections", f"Couldn't connect {c.display_name}", msg, level="warn")
            return _ui(error=msg, name=name)
        account = app.provider.account(tok)
        rt.vault.save(name, c.provider, tok, account=account, scopes=oauth._scope_list(tok) or c.scope_list())
        c.last_error = None
        rt.background(_after_connect(rt, c))
        return _ui(connected=name)

    @router.delete("/api/connections/{name}", dependencies=[api])
    async def disconnect(name: str) -> dict[str, Any]:
        c = oauth_connector(name)
        was = c.connected
        revoked = await c.disconnect()
        if was:
            rt.store.add_activity("connections", f"Disconnected {c.display_name}", None)
        return {"ok": True, "revoked": revoked}

    return router


async def _after_connect(rt: Any, c: OAuthConnector) -> None:
    """Learn the account, subscribe to push, and log it (errors go to the connector's status)."""
    try:
        await c.on_connected()
    except Exception as exc:
        c.last_error = rt.redactor.text(str(exc))[:300]
    account = c.account()
    rt.store.add_activity(
        "connections",
        f"Connected {c.display_name}" + (f" ({account})" if account else ""),
        c.status()["push"]["detail"],
    )
