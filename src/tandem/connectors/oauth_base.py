"""What every first-party OAuth connector shares (SPEC §15.3): tokens from the vault, an authorized HTTP
helper that refreshes once on 401, cursors in the kv store, push channel bookkeeping and status.

Connectors are built with `(name, options, clock, secrets)` like any other; the Runtime then calls
`bind(rt)` to hand them the vault, the cipher, the store and the public base URL before `start()`.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets as pysecrets
from datetime import datetime, timedelta
from typing import Any, ClassVar

import httpx
from sqlalchemy import delete

from tandem import oauth
from tandem.config import ConnectorConfig
from tandem.connectors.base import Connector, InboundEvent, PushRequest, PushResult
from tandem.connectors.simple import WebhookAuthError
from tandem.store import kv
from tandem.tools import Tool, ToolContext
from tandem.util import iso, parse_iso

log = logging.getLogger("tandem.connectors.oauth")

TIMEOUT = 30.0
DEFAULT_POLL = timedelta(minutes=5)


class ApiError(RuntimeError):
    """A provider API call failed; the message is readable and has no tokens in it."""

    def __init__(self, message: str, status: int = 0, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class NotConnected(RuntimeError):
    pass


class OAuthConnector(Connector):
    """Base for `gmail`, `google_calendar`, `slack`, `m365`."""

    provider: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    default_name: ClassVar[str] = ""  # instance name when auto-offered
    scopes: ClassVar[list[str]] = []
    supports_push: ClassVar[bool] = True

    def __init__(self, name: str, options: dict[str, Any], clock: Any, secrets: Any = None) -> None:
        super().__init__(name, options, clock, secrets)
        self.rt: Any = None
        self.vault: Any = None
        self.cipher: Any = None
        self.store: Any = None
        self.base_url = ""
        self.push_error: str | None = None
        self._poll_lock = asyncio.Lock()

    # ---- wiring -------------------------------------------------------------------------------------------
    def bind(self, rt: Any) -> None:
        """Called by the Runtime before `start()`: the services that hold tokens and cursors."""
        self.rt = rt
        self.vault = rt.vault
        self.cipher = rt.cipher
        self.store = rt.store
        self.base_url = rt.settings.base_url
        enc = self.kv_get("push_secret")
        if enc:
            try:
                rt.redactor.extra.add(str(self.cipher.decrypt(enc)))
            except Exception:
                pass

    @property
    def poll_every(self) -> timedelta | None:
        return super().poll_every or DEFAULT_POLL

    def scope_list(self) -> list[str]:
        extra = self.options.get("scopes") or []
        return list(dict.fromkeys([*self.scopes, *extra]))

    def app(self) -> oauth.OAuthApp:
        return oauth.resolve_app(self.provider, self.options, self.secrets, self.base_url)

    @property
    def configured(self) -> bool:
        """The client id and secret env vars are set (Connect can work)."""
        try:
            self.app()
            return True
        except oauth.OAuthError:
            return False

    @property
    def connected(self) -> bool:
        if self.vault is None:
            return False
        pub = self.vault.public(self.name)
        return bool(pub) and pub.get("status") != "revoked"

    def account(self) -> str | None:
        pub = self.vault.public(self.name) if self.vault else None
        return (pub or {}).get("account")

    # ---- kv -----------------------------------------------------------------------------------------------
    def _key(self, key: str) -> str:
        return f"connector:{self.name}:{key}"

    def kv_get(self, key: str, default: Any = None) -> Any:
        return self.store.kv_get(self._key(key), default)

    def kv_set(self, key: str, value: Any) -> None:
        self.store.kv_set(self._key(key), value)

    def kv_clear(self) -> None:
        self.store._exec(
            delete(kv).where(kv.c.key.like(f"connector:{self.name}:%"), kv.c.key != self._key("push_secret"))
        )

    def push_secret(self) -> str:
        """A per-connector token for push verification: `push_token_env`, else random and stored encrypted.

        It survives a disconnect, so a Pub/Sub subscription URL that carries it keeps working.
        """
        pinned = self.secrets.get(self.options.get("push_token_env")) if self.secrets else None
        if pinned:
            return str(pinned)
        enc = self.kv_get("push_secret")
        if enc:
            return str(self.cipher.decrypt(enc))
        secret = pysecrets.token_urlsafe(32)
        self.kv_set("push_secret", self.cipher.encrypt(secret))
        if self.rt is not None:
            self.rt.redactor.extra.add(secret)
        return secret

    def check_secret(self, given: str | None) -> None:
        pinned = self.secrets.get(self.options.get("push_token_env")) if self.secrets else None
        enc = self.kv_get("push_secret")
        expected = str(pinned or (self.cipher.decrypt(enc) if enc else ""))
        if not (expected and given and hmac.compare_digest(given, expected)):
            raise WebhookAuthError("bad or missing push token")

    # ---- tokens and HTTP ------------------------------------------------------------------------------------
    async def access_token(self) -> str:
        if not self.connected:
            raise NotConnected(f"connect {self.display_name} first (Connections screen)")
        return await self.vault.access_token(self.name, oauth.refresher(self.app()))

    async def force_refresh(self) -> None:
        tok = self.vault.token(self.name) or {}
        fresh = await oauth.refresher(self.app())(tok)
        self.vault.save(self.name, self.provider, fresh)

    def _redact(self, text: str) -> str:
        return self.rt.redactor.text(text) if self.rt is not None else text

    def _error_message(self, r: httpx.Response) -> tuple[str, str | None]:
        try:
            body = r.json()
        except ValueError:
            return r.text[:200], None
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):  # Google and Graph: {"error": {"code", "message"}}
            return str(err.get("message") or err.get("code") or ""), str(err.get("code") or "")
        if isinstance(err, str):
            return str(body.get("error_description") or err), err
        return str(body)[:200], None

    async def _api(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """An authorized call: bearer from the vault, one forced refresh on 401, readable errors."""
        for attempt in (0, 1):
            token = await self.access_token()
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    data=data,
                    headers={"Authorization": f"Bearer {token}", **(headers or {})},
                )
            if r.status_code == 401 and attempt == 0:
                try:
                    await self.force_refresh()
                except Exception as exc:
                    self.vault.set_status(self.name, "refresh_failed")
                    raise ApiError(
                        self._redact(f"{self.display_name} rejected the token and refresh failed: {exc}"), 401
                    ) from exc
                continue
            if r.status_code >= 400:
                msg, code = self._error_message(r)
                raise ApiError(
                    self._redact(f"{self.display_name} API error {r.status_code}: {msg}"), r.status_code, code
                )
            if r.status_code == 204 or not r.content:
                return {}
            try:
                return self.check(r.json())
            except ValueError:
                return r.text
        raise ApiError(f"{self.display_name}: unauthorized", 401)  # pragma: no cover

    def check(self, body: Any) -> Any:
        """Hook for APIs that report errors in a 200 body (Slack)."""
        return body

    # ---- lifecycle ------------------------------------------------------------------------------------------
    def push_mode(self) -> tuple[str, str]:
        """('push' | 'poll', why)."""
        if not self.supports_push:
            return "poll", "this connector polls"
        if self.options.get("push") is False:
            return "poll", "push is off in tandem.yaml"
        if not self.base_url.startswith("https://"):
            return "poll", f"polling every {_fmt(self.poll_every)}: push needs an https TANDEM_PUBLIC_URL"
        why = self.push_unavailable()
        if why:
            return "poll", why
        return "push", self.push_detail()

    def push_unavailable(self) -> str | None:
        """A reason push can't be used even with a public URL (e.g. no Pub/Sub topic)."""
        return None

    def push_detail(self) -> str:
        exp = self.kv_get("push_expires_at")
        return f"subscribed until {exp}" if exp else "not subscribed yet"

    def webhook_url(self) -> str:
        return f"{self.base_url}/api/webhooks/{self.name}"

    async def start(self) -> None:
        if self.connected:
            await self.ensure_push()

    async def on_connected(self) -> None:
        """After the OAuth callback: learn the account and subscribe to push."""
        try:
            account = await self.fetch_account()
            if account:
                pub = self.vault.public(self.name) or {}
                if pub.get("account") != account:
                    self.vault.save(self.name, self.provider, {}, account=account)
        except Exception as exc:
            log.info("%s: couldn't read the account: %s", self.name, exc)
        await self.ensure_push(force=True)

    async def fetch_account(self) -> str | None:
        return None

    async def ensure_push(self, force: bool = False) -> None:
        """Subscribe, or renew a subscription that expires within a day. Errors go to status."""
        if self.push_mode()[0] != "push" or not self.connected:
            return
        exp = parse_iso(self.kv_get("push_expires_at"))
        if not force and exp and exp - timedelta(days=1) > self.clock.now():
            return
        try:
            expires = await self.subscribe(renew=bool(exp) and not force)
            self.kv_set("push_expires_at", iso(expires) if expires else None)
            self.push_error = None
        except Exception as exc:
            self.push_error = self._redact(str(exc))[:300]
            log.warning("%s: push subscription failed: %s", self.name, self.push_error)

    async def subscribe(self, renew: bool) -> datetime | None:
        """Create or renew the provider's push channel; returns its expiry."""
        return None

    async def unsubscribe(self) -> None:
        return None

    async def disconnect(self) -> bool:
        """Stop push, revoke at the provider (best effort), delete the token and cursors."""
        revoked = False
        if self.connected:
            try:
                await self.unsubscribe()
            except Exception as exc:
                log.info("%s: stopping push failed: %s", self.name, exc)
            tok = self.vault.token(self.name) or {}
            try:
                revoked = await oauth.revoke(self.app(), tok)
            except Exception as exc:
                log.info("%s: revoke failed: %s", self.name, exc)
        self.vault.delete(self.name)
        self.kv_clear()
        self.push_error = None
        self.last_error = None
        return revoked

    # ---- polling and push ---------------------------------------------------------------------------------
    async def poll(self, since: datetime | None) -> list[InboundEvent]:
        if not self.connected:
            return []
        async with self._poll_lock:
            try:
                events = await self.fetch_new()
                self.last_error = None
            except Exception as exc:
                self.last_error = self._redact(str(exc))[:300]
                raise
            finally:
                self.last_poll = self.clock.now()
        await self.ensure_push()
        return events

    async def fetch_new(self, hint: dict[str, Any] | None = None) -> list[InboundEvent]:
        """Incremental fetch from the stored cursors."""
        return []

    async def verify_push(self, req: PushRequest) -> PushResult | None:
        """Check a push; return a `PushResult` to answer a handshake, or None to poll. Raise to reject."""
        raise WebhookAuthError(f"{self.name} does not accept push")

    def push_hint(self, req: PushRequest) -> dict[str, Any] | None:
        return None

    async def handle_push(self, req: PushRequest) -> PushResult:
        """Verify, answer handshakes, then poll in the background: the payload is only a doorbell."""
        res = await self.verify_push(req)
        if res is not None:
            return res
        queued = self.rt is not None and self.connected
        if queued:
            self.rt.background(self._push_poll(self.push_hint(req)))
        return PushResult(status=self.push_status, body={"accepted": 0, "queued": queued})

    push_status: ClassVar[int] = 200

    async def _push_poll(self, hint: dict[str, Any] | None) -> None:
        try:
            async with self._poll_lock:
                events = await self.fetch_new(hint)
                self.last_poll = self.clock.now()
            if events:
                await self.rt.ingest(self.name, events)
        except Exception as exc:
            self.last_error = self._redact(str(exc))[:300]
            log.warning("%s: push-triggered poll failed: %s", self.name, self.last_error)

    # ---- tools and status ---------------------------------------------------------------------------------
    def tool(self, *args: Any, **kwargs: Any) -> Tool:
        """A `Tool` whose function first checks the connection (tools are listed even when not connected)."""
        t = Tool(*args, **kwargs)
        func = t.func

        async def guarded(a: dict[str, Any], ctx: ToolContext) -> Any:
            if not self.connected:
                raise NotConnected(f"connect {self.display_name} first (Connections screen)")
            return await func(a, ctx)

        t.func = guarded
        return t

    def status(self) -> dict[str, Any]:
        base = super().status()
        mode, detail = self.push_mode()
        if mode == "push" and self.push_error:
            detail = f"push failed ({self.push_error}); polling every {_fmt(self.poll_every)}"
        pub = (self.vault.public(self.name) if self.vault else None) or {}
        connected = self.connected
        err = self.last_error
        if pub.get("status") == "refresh_failed":
            err = err or f"{self.display_name} needs to be reconnected (token refresh failed)"
        return {
            **base,
            "status": "error" if err else ("ok" if connected else "disconnected"),
            "error": err,
            "provider": self.provider,
            "display_name": self.display_name,
            "oauth": True,
            "configured": not self.options.get("auto_offered", False),
            "client_configured": self.configured,
            "connected": connected,
            "account": pub.get("account"),
            "scopes": pub.get("scopes") or self.scope_list(),
            "expires_at": pub.get("expires_at"),
            "push": {
                "mode": mode if connected else "poll",
                "detail": detail if connected else "not connected",
            },
        }


def _fmt(td: timedelta | None) -> str:
    if not td:
        return "check-in"
    mins = int(td.total_seconds() // 60)
    return f"{mins} min" if mins < 120 else f"{mins // 60} h"


# ---- auto-offer -------------------------------------------------------------------------------------------


def oauth_offers(configs: list[ConnectorConfig], secrets: Any) -> list[ConnectorConfig]:
    """Default instances for OAuth connector types whose client id env var is set and that aren't configured.

    Names: `gmail`, `calendar` (Google Calendar), `slack`, `outlook` (Microsoft 365); a name already used by
    another connector falls back to the type name.
    """
    from tandem.connectors.base import CONNECTOR_TYPES

    types = {c.type for c in configs}
    names = {c.name or c.type for c in configs}
    out = []
    for cls in CONNECTOR_TYPES.values():
        if not (isinstance(cls, type) and issubclass(cls, OAuthConnector)) or cls.type in types:
            continue
        env = oauth.PROVIDERS[cls.provider].client_id_env
        if not (secrets and secrets.get(env)):
            continue
        name = cls.default_name if cls.default_name not in names else cls.type
        if name in names:
            continue
        names.add(name)
        out.append(ConnectorConfig(type=cls.type, name=name, auto_offered=True))
    return out
