"""Web Push (SPEC §15.4): RFC 8291 crypto, VAPID, delivery to a fake push service, policy, settings, routes."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from tandem.config import NotifyChannel, NotifyConfig
from tandem.connectors.base import InboundEvent
from tandem.engine.triage import Verdict
from tandem.push import (
    KV_KEY,
    PushError,
    PushService,
    approval_actions,
    b64u,
    b64u_decode,
    decrypt,
    encrypt,
    private_from_raw,
    public_bytes,
    topic_for,
    verify_vapid_jwt,
)
from tandem.server.app import create_app
from tandem.tools import ToolContext

from .conftest import local, mock_cfg

PUSH_HOST = "https://push.example.com"
NTFY_URL = "https://ntfy.example.com/tandem"


# ---- RFC 8291 Appendix A ---------------------------------------------------------------------------------------
RFC = {
    "plaintext": b"When I grow up, I want to be a watermelon",
    "as_private": "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
    "as_public": "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8",
    "ua_private": "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94",
    "ua_public": "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
    "salt": "DGv6ra1nlYgDCS1FRnbzlw",
    "auth": "BTBZMqHH6r4Tts7J_aSIgg",
    "body": "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN",
}


def test_rfc8291_vector_encrypts_and_decrypts():
    as_private = private_from_raw(b64u_decode(RFC["as_private"]))
    ua_private = private_from_raw(b64u_decode(RFC["ua_private"]))
    assert b64u(public_bytes(as_private.public_key())) == RFC["as_public"]
    assert b64u(public_bytes(ua_private.public_key())) == RFC["ua_public"]
    auth = b64u_decode(RFC["auth"])
    body = encrypt(
        RFC["plaintext"],
        b64u_decode(RFC["ua_public"]),
        auth,
        salt=b64u_decode(RFC["salt"]),
        as_private=as_private,
    )
    assert b64u(body) == RFC["body"]
    assert decrypt(b64u_decode(RFC["body"]), ua_private, auth) == RFC["plaintext"]


def test_encrypt_round_trip_with_padding_and_tamper_detection():
    ua = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    body = encrypt(b'{"hi":1}', public_bytes(ua.public_key()), auth, pad=40)
    assert decrypt(body, ua, auth) == b'{"hi":1}'
    with pytest.raises(PushError):
        decrypt(body[:-1] + bytes([body[-1] ^ 1]), ua, auth)
    with pytest.raises(PushError):
        encrypt(b"x" * 5000, public_bytes(ua.public_key()), auth)


def test_actions_and_topic():
    assert [a["action"] for a in approval_actions(["send"])] == ["approve", "open"]
    assert [a["action"] for a in approval_actions(["pay"])] == ["open"]
    assert [a["action"] for a in approval_actions(["write", "delete"])] == ["open"]
    t = topic_for("tandem-approval-ap_0123456789ab/extra?")
    assert t and len(t) <= 32 and all(c.isalnum() or c in "-_" for c in t)


def test_webpush_channel_rule():
    assert NotifyConfig().webpush_on()
    assert not NotifyConfig(push_enabled=False).webpush_on()
    assert not NotifyConfig(channels=[NotifyChannel(type="ntfy", url=NTFY_URL)]).webpush_on()
    assert NotifyConfig(channels=[NotifyChannel(type="ui"), NotifyChannel(type="webpush")]).webpush_on()


# ---- VAPID keys ------------------------------------------------------------------------------------------------
def test_vapid_key_generated_once_encrypted_and_env_override(make_rt):
    rt = make_rt()
    stored = rt.store.kv_get(KV_KEY)
    assert stored and rt.push.public_key not in stored  # ciphertext, not the key
    assert PushService(rt, {}).public_key == rt.push.public_key  # reloaded, not regenerated
    key = ec.generate_private_key(ec.SECP256R1())
    expected = b64u(public_bytes(key.public_key()))
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    raw = b64u(key.private_numbers().private_value.to_bytes(32, "big"))
    assert PushService(rt, {"TANDEM_VAPID_PRIVATE_KEY": pem}).public_key == expected
    assert PushService(rt, {"TANDEM_VAPID_PRIVATE_KEY": raw}).public_key == expected
    assert len(b64u_decode(expected)) == 65


# ---- a fake push service ---------------------------------------------------------------------------------------
class Device:
    """A browser: its key pair, auth secret and endpoint, and what it received."""

    def __init__(self, name: str) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.endpoint = f"{PUSH_HOST}/send/{name}"
        self.received: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.status = 201

    def subscription(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "expirationTime": None,
            "keys": {"p256dh": b64u(public_bytes(self.key.public_key())), "auth": b64u(self.auth)},
        }


class FakePushService:
    def __init__(self, mock: respx.MockRouter, vapid_public_key: str, subject: str) -> None:
        self.devices: dict[str, Device] = {}
        self.vapid_public_key = vapid_public_key
        self.subject = subject
        mock.post(url__startswith=f"{PUSH_HOST}/send/").mock(side_effect=self.handle)

    def add(self, name: str) -> Device:
        self.devices[name] = Device(name)
        return self.devices[name]

    def handle(self, request: httpx.Request) -> httpx.Response:
        dev = self.devices[request.url.path.rsplit("/", 1)[-1]]
        h = request.headers
        assert h["content-encoding"] == "aes128gcm"
        assert int(h["ttl"]) > 0
        assert h["urgency"] in ("very-low", "low", "normal", "high")
        if "topic" in h:
            assert len(h["topic"]) <= 32
        scheme, _, params = h["authorization"].partition(" ")
        assert scheme == "vapid"
        parts = dict(p.strip().split("=", 1) for p in params.split(","))
        assert parts["k"] == self.vapid_public_key
        claims = verify_vapid_jwt(parts["t"], b64u_decode(parts["k"]))
        assert claims["aud"] == PUSH_HOST and claims["sub"] == self.subject
        dev.headers.append(h)
        if dev.status >= 300:
            return httpx.Response(dev.status, text="gone" if dev.status == 410 else "error")
        dev.received.append(json.loads(decrypt(request.content, dev.key, dev.auth)))
        return httpx.Response(201)


@pytest.fixture(autouse=True)
def _no_proxy_env(monkeypatch):
    # httpx can't parse a bracketed IPv6 entry in NO_PROXY (some sandboxes set one); respx needs no proxy
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def pushmock():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


def fake_service(rt, mock) -> FakePushService:
    return FakePushService(mock, rt.push.public_key, rt.push.subject)


async def notify_event(rt, title: str, urgency: str) -> dict[str, int]:
    """One event through `_process` with a fixed triage verdict."""
    ev = rt.store.add_event(
        InboundEvent("demo", "email", f"x-{title}", title, sender="boss@example.com").row()
    )

    async def verdicts(events):
        return [Verdict(ev["id"], urgency, "notify", f"{urgency} test")]

    rt.triage.run = verdicts
    return await rt._process([ev], rt.clock.now())


async def test_delivery_headers_jwt_and_payload(make_rt, pushmock):
    rt = make_rt(env={"TANDEM_VAPID_SUBJECT": "mailto:me@example.com"})
    svc = fake_service(rt, pushmock)
    dev = svc.add("phone")
    rt.push.subscribe(dev.subscription(), "Mozilla/5.0 (Linux; Android 15) Chrome/140.0 Mobile Safari/537.36")
    assert rt.push.list()[0]["label"] == "Chrome on Android"
    assert "p256dh" not in rt.push.list()[0] and rt.push.list()[0]["host"] == "push.example.com"

    res = await notify_event(rt, "Server down", "high")
    assert res["notified"] == 1
    assert len(dev.received) == 1
    got = dev.received[0]
    assert got["title"] == "Server down" and got["urgency"] == "high"
    assert got["url"] and got["tag"] == f"tandem-{got['message_id']}"
    assert [a["action"] for a in got["actions"]] == ["open"]
    assert dev.headers[0]["urgency"] == "high" and dev.headers[0]["ttl"] == str(24 * 3600)
    sub = rt.push.list()[0]
    assert sub["last_ok_at"] and sub["failures"] == 0
    assert rt.notifier.sent[-1]["webpush_delivered"] == 1


async def test_gone_subscription_is_removed_and_failures_drop_after_five(make_rt, pushmock):
    rt = make_rt()
    svc = fake_service(rt, pushmock)
    gone, flaky = svc.add("gone"), svc.add("flaky")
    rt.push.subscribe(gone.subscription())
    rt.push.subscribe(flaky.subscription())
    gone.status, flaky.status = 410, 500
    assert await rt.push.send({"title": "t"}) == 0
    subs = rt.push.list()
    assert len(subs) == 1 and subs[0]["failures"] == 1 and subs[0]["last_error"].startswith("500")
    for _ in range(4):
        await rt.push.send({"title": "t"})
    assert rt.push.list() == []


async def test_quiet_hours_hold_high_but_critical_pushes(make_rt, pushmock):
    rt = make_rt(start=local(22, 30))
    dev = fake_service(rt, pushmock).add("phone")
    rt.push.subscribe(dev.subscription())
    held = await notify_event(rt, "Invoice due", "high")
    assert held["held"] == 1 and dev.received == []
    crit = await notify_event(rt, "Fraud alert", "critical")
    assert crit["notified"] == 1 and [p["title"] for p in dev.received] == ["Fraud alert"]


async def test_fallback_ntfy_only_when_web_push_does_not_deliver(make_rt, pushmock):
    cfg = mock_cfg()
    cfg.notify = NotifyConfig(
        channels=[
            NotifyChannel(type="ui"),
            NotifyChannel(type="webpush"),
            NotifyChannel(type="ntfy", url=NTFY_URL, when="fallback"),
        ]
    )
    rt = make_rt(cfg=cfg)
    ntfy = pushmock.post(NTFY_URL).mock(return_value=httpx.Response(200))
    # no subscriptions: the fallback fires
    await rt.notifier.push("A", "first", "high", 1)
    assert ntfy.call_count == 1
    # a working subscription: no fallback
    dev = fake_service(rt, pushmock).add("phone")
    rt.push.subscribe(dev.subscription())
    await rt.notifier.push("B", "second", "high", 2)
    assert ntfy.call_count == 1 and len(dev.received) == 1
    # the push service fails: the fallback fires again
    dev.status = 500
    await rt.notifier.push("C", "third", "high", 3)
    assert ntfy.call_count == 2


async def test_always_channels_still_fire_with_web_push(make_rt, pushmock):
    cfg = mock_cfg()
    cfg.notify = NotifyConfig(channels=[NotifyChannel(type="webhook", url="https://hook.example.com/n")])
    rt = make_rt(cfg=cfg)
    hook = pushmock.post("https://hook.example.com/n").mock(return_value=httpx.Response(204))
    dev = fake_service(rt, pushmock).add("phone")
    rt.push.subscribe(dev.subscription())
    await rt.notifier.push("A", "b", "high", 1)
    # channels listed without `webpush`: Web Push is off, the webhook carries it
    assert hook.call_count == 1 and dev.received == []
    assert json.loads(hook.calls[0].request.content)["title"] == "A"


async def test_approval_push_has_approve_except_for_pay(make_rt, pushmock):
    rt = make_rt()
    dev = fake_service(rt, pushmock).add("phone")
    rt.push.subscribe(dev.subscription())
    ctx = ToolContext(origin="chat")
    await rt._on_approval({"id": "ap_send1", "title": "Send email to Dana", "tags": ["send"]}, ctx)
    await rt._on_approval({"id": "ap_pay1", "title": "Pay Acme $40", "tags": ["pay"]}, ctx)
    send, pay = dev.received
    assert send["approval_id"] == "ap_send1" and send["tag"] == "tandem-approval-ap_send1"
    assert [a["action"] for a in send["actions"]] == ["approve", "open"]
    assert pay["approval_id"] == "ap_pay1" and [a["action"] for a in pay["actions"]] == ["open"]
    assert dev.headers[0]["topic"].endswith("ap_send1")


async def test_settings_change_behaviour_live(make_rt, pushmock):
    rt = make_rt(start=local(12, 0))
    dev = fake_service(rt, pushmock).add("phone")
    rt.push.subscribe(dev.subscription())
    await notify_event(rt, "One", "high")
    assert len(dev.received) == 1
    rt.update_notify_settings({"min_urgency": "critical"})
    await notify_event(rt, "Two", "high")
    assert len(dev.received) == 1
    rt.update_notify_settings({"min_urgency": "high", "quiet_hours": "11:00-13:00"})
    await notify_event(rt, "Three", "high")
    assert len(dev.received) == 1
    rt.update_notify_settings({"quiet_hours": None, "push_enabled": False})
    await notify_event(rt, "Four", "high")
    assert len(dev.received) == 1 and rt.notifier.sent[-1]["title"] == "Four"  # thread + SSE still
    rt.update_notify_settings({"push_enabled": True})
    await notify_event(rt, "Five", "high")
    assert [p["title"] for p in dev.received] == ["One", "Five"]
    # stored in kv: a restart keeps them
    rt2 = type(rt)(rt.settings, mock_cfg(), rt.clock, env={})
    assert rt2.notify_settings() == {
        "min_urgency": "high",
        "quiet_hours": None,
        "max_per_hour": 4,
        "push_enabled": True,
    }


# ---- routes ----------------------------------------------------------------------------------------------------
def test_push_routes(make_rt, pushmock):
    rt = make_rt()
    dev = fake_service(rt, pushmock).add("laptop")
    with TestClient(create_app(rt, run_scheduler=False)) as c:
        v = c.get("/api/push/vapid").json()
        assert v == {"public_key": rt.push.public_key, "enabled": True}
        assert c.post("/api/push/test").status_code == 409  # nobody subscribed yet
        bad = {"endpoint": "http://insecure.example.com/x", "keys": dev.subscription()["keys"]}
        assert c.post("/api/push/subscriptions", json=bad).status_code == 422
        r = c.post(
            "/api/push/subscriptions",
            json=dev.subscription(),
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) Version/18.0 Safari/605.1.15"
            },
        )
        assert r.status_code == 200, r.text
        sub = r.json()
        assert sub["label"] == "Safari on macOS" and sub["host"] == "push.example.com" and sub["id"]
        assert [s["id"] for s in c.get("/api/push/subscriptions").json()] == [sub["id"]]
        assert c.get("/api/config").json()["notify"]["push"] == {
            "enabled": True,
            "subscriptions": 1,
            "vapid_public_key": rt.push.public_key,
        }
        assert c.post("/api/push/test").json() == {"delivered": 1, "subscriptions": 1}
        assert dev.received[0]["tag"] == "tandem-test"

        s = c.get("/api/settings/notify").json()
        assert s == {
            "min_urgency": "high",
            "quiet_hours": "21:00-07:00",
            "max_per_hour": 4,
            "push_enabled": True,
        }
        assert c.put("/api/settings/notify", json={"quiet_hours": "25:00-07:00"}).status_code == 422
        assert c.put("/api/settings/notify", json={"max_per_hour": -1}).status_code == 422
        s = c.put("/api/settings/notify", json={"quiet_hours": None, "max_per_hour": 2}).json()
        assert s["quiet_hours"] is None and s["max_per_hour"] == 2 and rt.cfg.notify.max_per_hour == 2
        s = c.put("/api/settings/notify", json={"push_enabled": False}).json()
        assert s["push_enabled"] is False and c.get("/api/push/vapid").json()["enabled"] is False
        assert c.post("/api/push/test").status_code == 409

        assert c.request("DELETE", "/api/push/subscriptions", json={}).status_code == 422
        r = c.request("DELETE", "/api/push/subscriptions", json={"endpoint": dev.endpoint})
        assert r.json() == {"ok": True} and c.get("/api/push/subscriptions").json() == []
