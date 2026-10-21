"""The server key and the token vault (SPEC §15.1).

OAuth tokens are encrypted at rest with a server key (Fernet: AES-128-CBC + HMAC-SHA256), refreshed
before they expire, and handed only to connector code that sets an `Authorization` header. Every live
token is registered with the `Redactor`, so one echoed back by an API never reaches a model or the UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import JSON, Column, String, Table, Text, delete, insert, select, update

from tandem.store import Store, metadata
from tandem.util import iso, parse_iso

log = logging.getLogger("tandem.vault")

credentials = Table(
    "credentials",
    metadata,
    Column("name", String(64), primary_key=True),
    Column("provider", String(32), nullable=False),
    Column("account", String(255)),
    Column("scopes", JSON),
    Column("token", Text, nullable=False),  # Fernet ciphertext of the token response
    Column("expires_at", String(32)),
    Column("status", String(16), nullable=False, default="ok"),
    Column("created_at", String(32), nullable=False),
    Column("updated_at", String(32), nullable=False),
)

REFRESH_MARGIN = timedelta(seconds=120)


class VaultError(RuntimeError):
    pass


def load_server_key(state_dir: Path, env: dict[str, str] | None = None) -> bytes:
    """`TANDEM_SECRET_KEY`, else `<data>/secret.key` (created once, mode 0600)."""
    env = env if env is not None else dict(os.environ)
    if env.get("TANDEM_SECRET_KEY"):
        key = env["TANDEM_SECRET_KEY"].strip().encode()
        Fernet(key)  # validates: 32 url-safe base64 bytes
        return key
    path = state_dir / "secret.key"
    if path.is_file():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


class Cipher:
    def __init__(self, key: bytes) -> None:
        self._f = Fernet(key)

    def encrypt(self, value: Any) -> str:
        return self._f.encrypt(json.dumps(value).encode()).decode()

    def decrypt(self, token: str) -> Any:
        try:
            return json.loads(self._f.decrypt(token.encode()))
        except InvalidToken as exc:
            raise VaultError("cannot decrypt: the server key changed (reconnect the app)") from exc


# A refresher takes the stored token dict and returns the provider's new token response.
Refresher = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class TokenVault:
    def __init__(self, store: Store, cipher: Cipher, redactor: Any = None) -> None:
        self.store = store
        self.cipher = cipher
        self.redactor = redactor
        self._locks: dict[str, asyncio.Lock] = {}
        for row in self.store._all(select(credentials)):
            try:
                self._register(self.cipher.decrypt(row["token"]))
            except VaultError:
                log.warning("credential %s can't be decrypted with this server key", row["name"])

    def _register(self, token: dict[str, Any]) -> None:
        if self.redactor is None:
            return
        for k in ("access_token", "refresh_token", "id_token"):
            if token.get(k):
                self.redactor.extra.add(str(token[k]))

    def _expires_at(self, token: dict[str, Any]) -> str | None:
        if token.get("expires_at"):
            return str(token["expires_at"])
        if token.get("expires_in"):
            return iso(self.store.clock.now() + timedelta(seconds=int(token["expires_in"])))
        return None

    def save(
        self,
        name: str,
        provider: str,
        token: dict[str, Any],
        account: str | None = None,
        scopes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Store a token response (merging a refresh that omits `refresh_token`)."""
        old = self.token(name)
        merged = {**(old or {}), **{k: v for k, v in token.items() if v not in (None, "")}}
        merged["expires_at"] = self._expires_at(token) or (old or {}).get("expires_at")
        merged.pop("expires_in", None)
        self._register(merged)
        now = self.store.now()
        scopes = scopes if scopes is not None else _scopes(token)
        fields = {
            "provider": provider,
            "token": self.cipher.encrypt(merged),
            "expires_at": merged.get("expires_at"),
            "status": "ok",
            "updated_at": now,
        }
        if account:
            fields["account"] = account
        if scopes:
            fields["scopes"] = scopes
        if old is None:
            self.store._exec(insert(credentials).values(name=name, created_at=now, **fields))
        else:
            self.store._exec(update(credentials).where(credentials.c.name == name).values(**fields))
        return self.public(name) or {}

    def token(self, name: str) -> dict[str, Any] | None:
        row = self.store._one(select(credentials).where(credentials.c.name == name))
        return self.cipher.decrypt(row["token"]) if row else None

    def has(self, name: str) -> bool:
        return self.store._one(select(credentials.c.name).where(credentials.c.name == name)) is not None

    def set_status(self, name: str, status: str) -> None:
        self.store._exec(
            update(credentials)
            .where(credentials.c.name == name)
            .values(status=status, updated_at=self.store.now())
        )

    async def access_token(self, name: str, refresh: Refresher | None = None) -> str:
        """A valid access token, refreshed first when it expires within two minutes."""
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            tok = self.token(name)
            if tok is None:
                raise VaultError(f"{name} is not connected")
            exp = parse_iso(tok.get("expires_at"))
            if (
                refresh
                and tok.get("refresh_token")
                and exp
                and exp - REFRESH_MARGIN <= self.store.clock.now()
            ):
                try:
                    fresh = await refresh(tok)
                except Exception as exc:
                    self.set_status(name, "refresh_failed")
                    raise VaultError(f"couldn't refresh the {name} token: {exc}") from exc
                row = self.public(name) or {}
                self.save(name, row.get("provider", ""), fresh)
                tok = self.token(name) or {}
            if not tok.get("access_token"):
                raise VaultError(f"{name} has no access token")
            return str(tok["access_token"])

    def delete(self, name: str) -> bool:
        return bool(self.store._exec(delete(credentials).where(credentials.c.name == name)).rowcount)

    def public(self, name: str) -> dict[str, Any] | None:
        """Everything about a connection except its tokens."""
        row = self.store._one(select(credentials).where(credentials.c.name == name))
        if row is None:
            return None
        row.pop("token", None)
        return row


def _scopes(token: dict[str, Any]) -> list[str]:
    raw = token.get("scope") or ""
    return [s for s in raw.replace(",", " ").split() if s] if isinstance(raw, str) else list(raw)
