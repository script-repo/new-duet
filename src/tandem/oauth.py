"""OAuth 2.0 authorization code + PKCE (SPEC §15.2): providers, the state store, code exchange, refresh, revoke.

The browser only ever carries the provider's `code` and our random `state`; the PKCE verifier stays on the
server (encrypted, single use, ten minutes), and the client secret is resolved from the environment at
call time so the `Redactor` knows it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets as pysecrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import Column, String, Table, Text, delete, insert, select

from tandem.store import Store, metadata
from tandem.util import iso, parse_iso
from tandem.vault import Cipher, Refresher

STATE_TTL = timedelta(minutes=10)
TIMEOUT = 30.0

oauth_states = Table(
    "oauth_states",
    metadata,
    Column("state", String(64), primary_key=True),
    Column("name", String(64), nullable=False),
    Column("verifier", Text, nullable=False),  # Fernet ciphertext
    Column("created_at", String(32), nullable=False),
    Column("expires_at", String(32), nullable=False),
)


class OAuthError(RuntimeError):
    pass


def _jwt_claims(token: str | None) -> dict[str, Any]:
    """The payload of an id_token we just received from the token endpoint over TLS (not re-verified)."""
    if not token or token.count(".") != 2:
        return {}
    part = token.split(".")[1]
    try:
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except ValueError:
        return {}


def _google_account(tok: dict[str, Any]) -> str | None:
    return _jwt_claims(tok.get("id_token")).get("email")


def _microsoft_account(tok: dict[str, Any]) -> str | None:
    claims = _jwt_claims(tok.get("id_token"))
    return claims.get("preferred_username") or claims.get("email")


def _slack_account(tok: dict[str, Any]) -> str | None:
    team = (tok.get("team") or {}).get("name")
    user = tok.get("user_id")
    return f"{user} @ {team}" if team and user else team or user


def _slack_token(raw: dict[str, Any]) -> dict[str, Any]:
    """Slack answers 200 with `ok: false` on errors and nests user tokens under `authed_user`."""
    if raw.get("ok") is False:
        raise OAuthError(f"slack: {raw.get('error') or 'error'}")
    user = raw.get("authed_user") or {}
    out = {k: v for k, v in raw.items() if k not in ("authed_user", "ok")}
    if user.get("access_token"):
        out.update(
            {k: user[k] for k in ("access_token", "refresh_token", "expires_in", "scope") if k in user}
        )
        out["user_id"] = user.get("id")
    return out


@dataclass(frozen=True)
class OAuthProvider:
    name: str
    display_name: str
    authorize_url: str  # may contain {tenant}
    token_url: str
    revoke_url: str | None
    client_id_env: str
    client_secret_env: str
    scope_param: str = "scope"
    scope_sep: str = " "
    extra_params: dict[str, str] = field(default_factory=dict)
    account: Callable[[dict[str, Any]], str | None] = lambda tok: None
    normalize: Callable[[dict[str, Any]], dict[str, Any]] = lambda raw: raw


PROVIDERS: dict[str, OAuthProvider] = {
    "google": OAuthProvider(
        name="google",
        display_name="Google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        revoke_url="https://oauth2.googleapis.com/revoke",
        client_id_env="GOOGLE_CLIENT_ID",
        client_secret_env="GOOGLE_CLIENT_SECRET",
        extra_params={"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"},
        account=_google_account,
    ),
    "slack": OAuthProvider(
        name="slack",
        display_name="Slack",
        authorize_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        revoke_url="https://slack.com/api/auth.revoke",
        client_id_env="SLACK_CLIENT_ID",
        client_secret_env="SLACK_CLIENT_SECRET",
        scope_param="user_scope",
        scope_sep=",",
        account=_slack_account,
        normalize=_slack_token,
    ),
    "microsoft": OAuthProvider(
        name="microsoft",
        display_name="Microsoft",
        authorize_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        revoke_url=None,  # Microsoft has no token revocation endpoint; we delete our copy
        client_id_env="MS_CLIENT_ID",
        client_secret_env="MS_CLIENT_SECRET",
        extra_params={"response_mode": "query", "prompt": "select_account"},
        account=_microsoft_account,
    ),
}


@dataclass
class OAuthApp:
    """A provider plus this deployment's client registration."""

    provider: OAuthProvider
    client_id: str
    client_secret: str
    redirect_uri: str
    tenant: str = "common"

    def _url(self, template: str) -> str:
        return template.format(tenant=self.tenant)

    @property
    def token_url(self) -> str:
        return self._url(self.provider.token_url)


def resolve_app(provider: str, options: dict[str, Any], secrets: Any, base_url: str) -> OAuthApp:
    """Build the client from env vars named in the connector options (defaults per provider)."""
    p = PROVIDERS[provider]
    id_env = options.get("client_id_env") or p.client_id_env
    secret_env = options.get("client_secret_env") or p.client_secret_env
    client_id = secrets.get(id_env) if secrets else None
    client_secret = secrets.get(secret_env) if secrets else None
    if not client_id or not client_secret:
        raise OAuthError(
            f"set {id_env} and {secret_env} to connect {p.display_name} (see docs/CONNECTORS.md)"
        )
    return OAuthApp(
        provider=p,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=f"{base_url.rstrip('/')}/api/oauth/callback",
        tenant=str(options.get("tenant") or "common"),
    )


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge)."""
    verifier = pysecrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(app: OAuthApp, scopes: list[str], state: str, challenge: str) -> str:
    params = {
        "client_id": app.client_id,
        "redirect_uri": app.redirect_uri,
        "response_type": "code",
        app.provider.scope_param: app.provider.scope_sep.join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **app.provider.extra_params,
    }
    return f"{app._url(app.provider.authorize_url)}?{urlencode(params)}"


async def _token_request(app: OAuthApp, form: dict[str, str]) -> dict[str, Any]:
    form = {"client_id": app.client_id, "client_secret": app.client_secret, **form}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(app.token_url, data=form, headers={"Accept": "application/json"})
    try:
        raw = r.json()
    except ValueError:
        raw = {}
    if r.status_code >= 400 or not isinstance(raw, dict):
        err = raw.get("error_description") or raw.get("error") if isinstance(raw, dict) else None
        raise OAuthError(f"{app.provider.name} token endpoint answered {r.status_code}: {err or 'error'}")
    tok = app.provider.normalize(raw)
    if not tok.get("access_token"):
        raise OAuthError(f"{app.provider.name} returned no access token")
    return tok


async def exchange_code(app: OAuthApp, code: str, verifier: str) -> dict[str, Any]:
    """Trade the authorization code (plus the PKCE verifier) for tokens."""
    return await _token_request(
        app,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": app.redirect_uri,
            "code_verifier": verifier,
        },
    )


def refresher(app: OAuthApp) -> Refresher:
    """A `Refresher` for `TokenVault.access_token`."""

    async def refresh(tok: dict[str, Any]) -> dict[str, Any]:
        if not tok.get("refresh_token"):
            raise OAuthError("no refresh token; reconnect the app")
        return await _token_request(
            app, {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]}
        )

    return refresh


async def revoke(app: OAuthApp, tok: dict[str, Any]) -> bool:
    """Best effort: True when the provider confirmed the revocation."""
    url = app.provider.revoke_url
    if not url:
        return False
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        if app.provider.name == "slack":
            r = await client.post(url, headers={"Authorization": f"Bearer {tok.get('access_token', '')}"})
            return r.status_code < 400 and bool(r.json().get("ok"))
        token = tok.get("refresh_token") or tok.get("access_token") or ""
        r = await client.post(url, data={"token": token})
        return r.status_code < 400


def _scope_list(tok: dict[str, Any]) -> list[str]:
    raw = tok.get("scope") or ""
    return [s for s in str(raw).replace(",", " ").split() if s]


class StateStore:
    """Single-use `state` values that carry the encrypted PKCE verifier for ten minutes."""

    def __init__(self, store: Store, cipher: Cipher) -> None:
        self.store = store
        self.cipher = cipher

    def create(self, name: str, verifier: str) -> str:
        now = self.store.clock.now()
        self.store._exec(delete(oauth_states).where(oauth_states.c.expires_at < iso(now)))
        state = pysecrets.token_urlsafe(32)
        self.store._exec(
            insert(oauth_states).values(
                state=state,
                name=name,
                verifier=self.cipher.encrypt(verifier),
                created_at=iso(now),
                expires_at=iso(now + STATE_TTL),
            )
        )
        return state

    def consume(self, state: str) -> tuple[str, str]:
        """(connector name, verifier); raises `OAuthError` for an unknown, used or expired state."""
        if not state:
            raise OAuthError("missing state")
        row = self.store._one(select(oauth_states).where(oauth_states.c.state == state))
        if row is None:
            raise OAuthError("unknown or already used state")
        self.store._exec(delete(oauth_states).where(oauth_states.c.state == state))
        exp = parse_iso(row["expires_at"])
        if exp is None or exp <= self.store.clock.now():
            raise OAuthError("the sign-in took too long; try again")
        return row["name"], self.cipher.decrypt(row["verifier"])
