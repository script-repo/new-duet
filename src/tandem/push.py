"""Web Push to the installed PWA (SPEC §15.4).

VAPID (RFC 8292) identifies this server to the browser's push service with an ES256 JWT; the payload is
encrypted end to end with `aes128gcm` (RFC 8291 on top of RFC 8188), so the push service only sees
metadata. Everything is done with `cryptography`; no Web Push library.

Push never decides anything: `Notifier` calls `PushService.send` only for what `NotificationPolicy`
already chose to push (`POST /api/push/test` is the one exception, sent by you from Settings).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import struct
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import Column, Integer, String, Table, Text, delete, insert, select, update

from tandem.store import metadata
from tandem.util import new_id

log = logging.getLogger("tandem.push")

push_subscriptions = Table(
    "push_subscriptions",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("endpoint", String(1024), nullable=False, unique=True),
    Column("p256dh", String(128), nullable=False),
    Column("auth", String(64), nullable=False),
    Column("label", String(255)),
    Column("created_at", String(32), nullable=False),
    Column("last_ok_at", String(32)),
    Column("last_error", Text),
    Column("failures", Integer, nullable=False, default=0),
)

KV_KEY = "vapid_private_key"  # Fernet ciphertext of the raw private scalar (base64url)
DEFAULT_SUBJECT = "mailto:tandem@localhost"
MAX_FAILURES = 5
RECORD_SIZE = 4096
TTL_DEFAULT = 24 * 3600
TTL_TASK = 4 * 3600
# Tandem urgency -> the Web Push `Urgency` header (RFC 8030 §5.3)
URGENCY_HEADER = {"low": "low", "normal": "normal", "high": "high", "critical": "high"}
NO_QUICK_APPROVE = frozenset({"pay", "delete"})


class PushError(ValueError):
    pass


# ---- encoding helpers ------------------------------------------------------------------------------------------
def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(text: str) -> bytes:
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    """The uncompressed point (65 bytes, 0x04 || X || Y)."""
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def load_public(raw: bytes) -> ec.EllipticCurvePublicKey:
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)


def private_from_raw(raw: bytes) -> ec.EllipticCurvePrivateKey:
    if len(raw) != 32:
        raise PushError("a raw VAPID private key is 32 bytes")
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def private_raw(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_numbers().private_value.to_bytes(32, "big")


def load_vapid_private(text: str) -> ec.EllipticCurvePrivateKey:
    """`TANDEM_VAPID_PRIVATE_KEY`: a PEM (PKCS#8 or SEC1) or the raw 32-byte scalar in base64url."""
    text = text.strip()
    if text.startswith("-----BEGIN"):
        key = serialization.load_pem_private_key(text.replace("\\n", "\n").encode(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
            raise PushError("the VAPID key must be a P-256 (prime256v1) EC key")
        return key
    return private_from_raw(b64u_decode(text))


# ---- VAPID (RFC 8292) ------------------------------------------------------------------------------------------
def audience(endpoint: str) -> str:
    u = urlsplit(endpoint)
    return f"{u.scheme}://{u.netloc}"


def vapid_jwt(key: ec.EllipticCurvePrivateKey, aud: str, sub: str, exp: int) -> str:
    """An ES256 JWT with the raw r||s signature JOSE requires (not DER)."""
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": aud, "exp": exp, "sub": sub}, separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode()
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    return f"{header}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def verify_vapid_jwt(token: str, public_key: bytes) -> dict[str, Any]:
    """Check an ES256 JWT against a raw public key; returns its claims (what a push service does)."""
    header, claims, sig = token.split(".")
    raw = b64u_decode(sig)
    if len(raw) != 64:
        raise PushError("ES256 signatures are 64 bytes")
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    load_public(public_key).verify(der, f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    if json.loads(b64u_decode(header)).get("alg") != "ES256":
        raise PushError("unexpected JWT alg")
    return json.loads(b64u_decode(claims))


# ---- aes128gcm payload encryption (RFC 8291 + RFC 8188) ---------------------------------------------------------
def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def _hmac(key: bytes, data: bytes) -> bytes:
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def _keys(
    ecdh_secret: bytes, auth: bytes, ua_public: bytes, as_public: bytes, salt: bytes
) -> tuple[bytes, bytes]:
    ikm = _hkdf(auth, ecdh_secret, b"WebPush: info\x00" + ua_public + as_public, 32)
    prk = _hmac(salt, ikm)  # HKDF-Extract
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]  # HKDF-Expand, one block
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    return cek, nonce


def encrypt(
    plaintext: bytes,
    p256dh: bytes,
    auth: bytes,
    *,
    salt: bytes | None = None,
    as_private: ec.EllipticCurvePrivateKey | None = None,
    pad: int = 0,
) -> bytes:
    """One `aes128gcm` record for a subscription (`salt`/`as_private` are fixed only by tests)."""
    salt = salt or os.urandom(16)
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    as_public = public_bytes(as_private.public_key())
    secret = as_private.exchange(ec.ECDH(), load_public(p256dh))
    cek, nonce = _keys(secret, auth, p256dh, as_public, salt)
    record = plaintext + b"\x02" + b"\x00" * pad  # 0x02: the last (and only) record
    if len(record) + 16 > RECORD_SIZE:
        raise PushError("payload too large for one Web Push record")
    header = salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public
    return header + AESGCM(cek).encrypt(nonce, record, None)


def decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """The user agent's side: decrypt one `aes128gcm` record (used by the tests as the browser)."""
    salt, (rs, idlen) = body[:16], struct.unpack("!IB", body[16:21])
    as_public, ciphertext = body[21 : 21 + idlen], body[21 + idlen :]
    if len(ciphertext) > rs:
        raise PushError("multi-record payloads are not supported")
    ua_public = public_bytes(ua_private.public_key())
    secret = ua_private.exchange(ec.ECDH(), load_public(as_public))
    cek, nonce = _keys(secret, auth, ua_public, as_public, salt)
    try:
        record = AESGCM(cek).decrypt(nonce, ciphertext, None)
    except InvalidTag as exc:
        raise PushError("payload authentication failed") from exc
    record = record.rstrip(b"\x00")
    if not record.endswith(b"\x02"):
        raise PushError("missing last-record delimiter")
    return record[:-1]


# ---- payloads --------------------------------------------------------------------------------------------------
def approval_actions(tags: Any) -> list[dict[str, str]]:
    """Approve from the notification, except for payments and deletions, which need the card."""
    open_ = {"action": "open", "title": "Open"}
    if NO_QUICK_APPROVE & set(tags or ()):
        return [open_]
    return [{"action": "approve", "title": "Approve"}, open_]


def topic_for(tag: str | None) -> str | None:
    """`Topic` collapses repeats on the push service: at most 32 base64url characters."""
    if not tag:
        return None
    return re.sub(r"[^A-Za-z0-9_-]", "", tag)[-32:] or None


def label_from_agent(ua: str) -> str:
    """A short device label from a User-Agent string ("Chrome on Android")."""
    browser = next(
        (name for key, name in _BROWSERS if key in ua),
        "Browser",
    )
    system = next((name for key, name in _SYSTEMS if key in ua), "")
    return f"{browser} on {system}" if system else browser


_BROWSERS = (
    ("Edg/", "Edge"),
    ("Firefox/", "Firefox"),
    ("SamsungBrowser/", "Samsung Internet"),
    ("Chrome/", "Chrome"),
    ("CriOS/", "Chrome"),
    ("Safari/", "Safari"),
)
_SYSTEMS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Mac OS X", "macOS"),
    ("Windows", "Windows"),
    ("CrOS", "ChromeOS"),
    ("Linux", "Linux"),
)


# ---- the service -----------------------------------------------------------------------------------------------
class PushService:
    def __init__(self, rt: Any, env: dict[str, str] | None = None) -> None:
        self.rt = rt
        self.store = rt.store
        self.env = env if env is not None else dict(os.environ)
        self.timeout = 10.0
        self._key = self._load_key()
        self.public_key = b64u(public_bytes(self._key.public_key()))

    def _load_key(self) -> ec.EllipticCurvePrivateKey:
        if self.env.get("TANDEM_VAPID_PRIVATE_KEY"):
            return load_vapid_private(self.env["TANDEM_VAPID_PRIVATE_KEY"])
        stored = self.store.kv_get(KV_KEY)
        if stored:
            return private_from_raw(b64u_decode(self.rt.cipher.decrypt(stored)))
        key = ec.generate_private_key(ec.SECP256R1())
        self.store.kv_set(KV_KEY, self.rt.cipher.encrypt(b64u(private_raw(key))))
        log.info("generated a VAPID key pair")
        return key

    @property
    def subject(self) -> str:
        return self.env.get("TANDEM_VAPID_SUBJECT") or self.rt.cfg.notify.vapid_subject or DEFAULT_SUBJECT

    @property
    def enabled(self) -> bool:
        return bool(self.rt.cfg.notify.push_enabled)

    # ---- subscriptions -----------------------------------------------------------------------------------------
    def subscribe(self, sub: dict[str, Any], user_agent: str = "") -> dict[str, Any]:
        """Store a browser's `PushSubscription.toJSON()` (re-subscribing the same endpoint updates it)."""
        endpoint = str(sub.get("endpoint") or "")
        keys = sub.get("keys") or {}
        u = urlsplit(endpoint)
        if u.scheme != "https" or not u.netloc or len(endpoint) > 1024:
            raise PushError("endpoint must be an https URL")
        try:
            p256dh, auth = b64u_decode(str(keys["p256dh"])), b64u_decode(str(keys["auth"]))
            load_public(p256dh)
        except (KeyError, ValueError, TypeError) as exc:
            raise PushError("keys.p256dh must be a P-256 public key and keys.auth 16 bytes") from exc
        if len(p256dh) != 65 or len(auth) != 16:
            raise PushError("keys.p256dh must be a P-256 public key and keys.auth 16 bytes")
        fields = {
            "p256dh": b64u(p256dh),
            "auth": b64u(auth),
            "label": (label_from_agent(user_agent) if user_agent else "Browser")[:255],
            "last_error": None,
            "failures": 0,
        }
        row = self.store._one(select(push_subscriptions).where(push_subscriptions.c.endpoint == endpoint))
        if row:
            self.store._exec(
                update(push_subscriptions).where(push_subscriptions.c.id == row["id"]).values(**fields)
            )
            sub_id = row["id"]
        else:
            sub_id = new_id("push")
            self.store._exec(
                insert(push_subscriptions).values(
                    id=sub_id, endpoint=endpoint, created_at=self.store.now(), **fields
                )
            )
        self.store.add_activity("notify", "A device turned on notifications", fields["label"])
        return self._public(self._get(sub_id) or {})

    def unsubscribe(self, endpoint: str | None = None, sub_id: str | None = None) -> bool:
        if endpoint:
            where = push_subscriptions.c.endpoint == endpoint
        elif sub_id:
            where = push_subscriptions.c.id == sub_id
        else:
            return False
        return bool(self.store._exec(delete(push_subscriptions).where(where)).rowcount)

    def list(self) -> list[dict[str, Any]]:
        rows = self.store._all(select(push_subscriptions).order_by(push_subscriptions.c.created_at))
        return [self._public(r) for r in rows]

    def count(self) -> int:
        return self.store.count(push_subscriptions)

    def _get(self, sub_id: str) -> dict[str, Any] | None:
        return self.store._one(select(push_subscriptions).where(push_subscriptions.c.id == sub_id))

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("id"),
            "host": urlsplit(row.get("endpoint") or "").hostname,
            "label": row.get("label"),
            "created_at": row.get("created_at"),
            "last_ok_at": row.get("last_ok_at"),
            "last_error": row.get("last_error"),
            "failures": row.get("failures") or 0,
        }

    # ---- sending -----------------------------------------------------------------------------------------------
    def headers(self, endpoint: str, urgency: str, ttl: int, topic: str | None) -> dict[str, str]:
        exp = int((self.store.clock.now() + timedelta(hours=12)).timestamp())
        token = vapid_jwt(self._key, audience(endpoint), self.subject, exp)
        h = {
            "Authorization": f"vapid t={token}, k={self.public_key}",
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(max(0, int(ttl))),
            "Urgency": URGENCY_HEADER.get(urgency, "normal"),
        }
        if topic := topic_for(topic):
            h["Topic"] = topic
        return h

    async def send(
        self,
        payload: dict[str, Any],
        urgency: str = "normal",
        ttl: int = TTL_DEFAULT,
        topic: str | None = None,
    ) -> int:
        """Encrypt and deliver to every subscription; returns how many push services accepted it."""
        subs = self.store._all(select(push_subscriptions))
        if not subs:
            return 0
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            results = await asyncio.gather(
                *(self._deliver(client, s, data, urgency, ttl, topic) for s in subs)
            )
        return sum(results)

    async def _deliver(
        self,
        client: httpx.AsyncClient,
        sub: dict[str, Any],
        data: bytes,
        urgency: str,
        ttl: int,
        topic: str | None,
    ) -> bool:
        where = push_subscriptions.c.id == sub["id"]
        try:
            body = encrypt(data, b64u_decode(sub["p256dh"]), b64u_decode(sub["auth"]))
            r = await client.post(
                sub["endpoint"], content=body, headers=self.headers(sub["endpoint"], urgency, ttl, topic)
            )
        except (httpx.HTTPError, PushError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"[:300]
        else:
            if r.status_code < 300:
                self.store._exec(
                    update(push_subscriptions)
                    .where(where)
                    .values(last_ok_at=self.store.now(), last_error=None, failures=0)
                )
                return True
            if r.status_code in (404, 410):  # the browser unsubscribed or the subscription expired
                self.store._exec(delete(push_subscriptions).where(where))
                self.store.add_activity("notify", "A device's notifications expired", sub.get("label") or "")
                return False
            error = f"{r.status_code} {r.text[:200]}".strip()
        failures = int(sub.get("failures") or 0) + 1
        if failures >= MAX_FAILURES:
            self.store._exec(delete(push_subscriptions).where(where))
            self.store.add_activity(
                "notify", "Stopped notifying a device after repeated failures", error, level="warn"
            )
        else:
            self.store._exec(
                update(push_subscriptions).where(where).values(failures=failures, last_error=error)
            )
        log.warning("web push to %s failed: %s", urlsplit(sub["endpoint"]).hostname, error)
        return False
