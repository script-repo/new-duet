"""OAuth + PKCE, the state store, the connections API, refresh, revoke and token hygiene (SPEC §15.2)."""

from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tandem import oauth
from tandem.config import ConnectorConfig
from tandem.engine.triage import apply_rules
from tandem.server.app import create_app
from tandem.tools import ToolContext
from tests.conftest import local
from tests.oauth.helpers import ENV, GMAIL, GOOGLE_TOKEN, PUBLIC, cfg_with, connect, fake_jwt, fx


def test_pkce_and_authorize_urls():
    verifier, challenge = oauth.pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected

    g = oauth.resolve_app("google", {}, _Secrets(ENV), PUBLIC)
    q = parse_qs(urlparse(oauth.authorize_url(g, ["openid", "email"], "st", challenge)).query)
    assert q["redirect_uri"] == [f"{PUBLIC}/api/oauth/callback"]
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"] == [challenge]
    assert q["access_type"] == ["offline"] and q["scope"] == ["openid email"]

    s = oauth.resolve_app("slack", {}, _Secrets(ENV), PUBLIC)
    q = parse_qs(urlparse(oauth.authorize_url(s, ["im:history", "chat:write"], "st", challenge)).query)
    assert q["user_scope"] == ["im:history,chat:write"] and "scope" not in q

    m = oauth.resolve_app("microsoft", {"tenant": "contoso.example"}, _Secrets(ENV), PUBLIC)
    assert oauth.authorize_url(m, ["User.Read"], "st", challenge).startswith(
        "https://login.microsoftonline.com/contoso.example/oauth2/v2.0/authorize?"
    )
    with pytest.raises(oauth.OAuthError, match="MY_ID"):
        oauth.resolve_app("google", {"client_id_env": "MY_ID"}, _Secrets(ENV), PUBLIC)


class _Secrets:
    def __init__(self, env):
        self.env = env

    def get(self, name):
        return self.env.get(name) if name else None


async def test_state_is_single_use_encrypted_and_expires(rt):
    states = oauth.StateStore(rt.store, rt.cipher)
    st = states.create("gmail", "verifier-abc")
    row = rt.store._one(select(oauth.oauth_states).where(oauth.oauth_states.c.state == st))
    assert "verifier-abc" not in row["verifier"]
    assert states.consume(st) == ("gmail", "verifier-abc")
    with pytest.raises(oauth.OAuthError, match="already used"):
        states.consume(st)
    late = states.create("gmail", "v2")
    rt.clock.advance(minutes=11)
    with pytest.raises(oauth.OAuthError, match="too long"):
        states.consume(late)


@pytest.fixture
def client(make_rt):
    rt = make_rt(
        start=local(9, 0),
        cfg=cfg_with(ConnectorConfig(type="gmail", name="gmail")),
        env=dict(ENV),
        public_url=PUBLIC,
    )
    with TestClient(create_app(rt, run_scheduler=False)) as c:
        c.rt = rt  # type: ignore[attr-defined]
        yield c


def test_callback_happy_path_and_replay(client, http):
    rt = client.rt
    url = client.post("/api/connections/gmail/authorize").json()["url"]
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "https://www.googleapis.com/auth/gmail.send" in q["scope"][0]
    token_route = http.post(GOOGLE_TOKEN).respond(
        json={
            "access_token": "fake-access-from-code",
            "refresh_token": "fake-refresh-from-code",
            "expires_in": 3599,
            "scope": "openid email https://www.googleapis.com/auth/gmail.readonly",
            "id_token": fake_jwt({"email": "sam@example.com"}),
        }
    )
    http.get(f"{GMAIL}/profile").respond(json=fx("gmail_profile"))

    r = client.get(
        "/api/oauth/callback", params={"code": "auth-code", "state": q["state"][0]}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"] == "/#/connections?connected=gmail"
    form = parse_qs(token_route.calls.last.request.content.decode())
    assert form["grant_type"] == ["authorization_code"] and form["code"] == ["auth-code"]
    assert form["client_secret"] == [ENV["GOOGLE_CLIENT_SECRET"]]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"][0].encode()).digest())
    assert challenge.rstrip(b"=").decode() == q["code_challenge"][0]

    conns = client.get("/api/connections").json()
    gmail = next(c for c in conns["connections"] if c["name"] == "gmail")
    assert gmail["connected"] and gmail["account"] == "sam@example.com" and gmail["oauth"]
    assert gmail["push"]["mode"] == "poll" and "pubsub_topic" in gmail["push"]["detail"]
    assert gmail["tools"] == ["gmail_search", "gmail_read", "gmail_send", "gmail_reply"]
    assert {a["type"] for a in conns["available"]} == {"gmail", "google_calendar", "slack", "m365"}
    _wait(lambda: any(a["title"] == "Connected Gmail (sam@example.com)" for a in rt.store.list_activity(50)))
    blob = json.dumps(conns) + json.dumps(client.get("/api/config").json())
    blob += json.dumps(rt.store.list_activity(100)) + json.dumps(rt.store.list_audit(100))
    for secret in ("fake-access-from-code", "fake-refresh-from-code", ENV["GOOGLE_CLIENT_SECRET"]):
        assert secret not in blob

    # the state was used up
    r = client.get(
        "/api/oauth/callback", params={"code": "again", "state": q["state"][0]}, follow_redirects=False
    )
    assert r.status_code == 303 and "error=invalid_state" in r.headers["location"]


def _wait(pred, timeout=5.0):
    import time

    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def test_callback_errors_redirect_to_the_ui(client, http):
    r = client.get("/api/oauth/callback", params={"code": "x", "state": "forged"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/#/connections?error=invalid_state")
    state = parse_qs(urlparse(client.post("/api/connections/gmail/authorize").json()["url"]).query)["state"][
        0
    ]
    r = client.get(
        "/api/oauth/callback", params={"error": "access_denied", "state": state}, follow_redirects=False
    )
    assert r.headers["location"] == "/#/connections?error=access_denied"
    # the denied state can't be reused either
    r = client.get("/api/oauth/callback", params={"code": "x", "state": state}, follow_redirects=False)
    assert "invalid_state" in r.headers["location"]
    # token endpoint failure
    state = parse_qs(urlparse(client.post("/api/connections/gmail/authorize").json()["url"]).query)["state"][
        0
    ]
    http.post(GOOGLE_TOKEN).respond(400, json={"error": "invalid_grant", "error_description": "Bad code"})
    r = client.get("/api/oauth/callback", params={"code": "bad", "state": state}, follow_redirects=False)
    assert "error=" in r.headers["location"] and "Bad+code" in r.headers["location"]
    assert not client.rt.vault.has("gmail")
    assert client.post("/api/connections/nope/authorize").status_code == 404
    assert client.post("/api/connections/inbox/authorize").status_code == 400


def test_authorize_needs_client_env(make_rt):
    rt = make_rt(cfg=cfg_with(ConnectorConfig(type="slack", name="slack")), env={})
    with TestClient(create_app(rt, run_scheduler=False)) as c:
        r = c.post("/api/connections/slack/authorize")
        assert r.status_code == 400 and "SLACK_CLIENT_ID" in r.json()["detail"]
        slack = c.get("/api/connections").json()["connections"][1]
        assert slack["client_configured"] is False and slack["status"] == "disconnected"


async def test_refresh_on_expiry_and_on_401(make_rt, http):
    rt = make_rt(cfg=cfg_with(ConnectorConfig(type="gmail", name="gmail")), env=dict(ENV))
    await rt.start()
    connect(rt, "gmail", "google")
    gmail = rt.connectors["gmail"]
    token = http.post(GOOGLE_TOKEN)
    token.side_effect = [
        httpx.Response(200, json={"access_token": "fake-access-gmail-2", "expires_in": 3600}),
        httpx.Response(200, json={"access_token": "fake-access-gmail-3", "expires_in": 3600}),
    ]
    profile = http.get(f"{GMAIL}/profile")
    profile.side_effect = [
        httpx.Response(200, json=fx("gmail_profile")),
        httpx.Response(200, json=fx("gmail_profile")),
        httpx.Response(401, json={"error": {"code": 401, "message": "Invalid Credentials"}}),
        httpx.Response(200, json=fx("gmail_profile")),
    ]
    assert await gmail.fetch_account() == "sam@example.com"
    assert profile.calls[0].request.headers["authorization"] == "Bearer fake-access-gmail-1"
    rt.clock.advance(minutes=59)  # within the 2-minute margin: refreshed first
    await gmail.fetch_account()
    assert profile.calls[1].request.headers["authorization"] == "Bearer fake-access-gmail-2"
    assert parse_qs(token.calls[0].request.content.decode())["refresh_token"] == ["fake-refresh-gmail"]
    await gmail.fetch_account()  # 401 → forced refresh → retry once
    assert profile.calls[3].request.headers["authorization"] == "Bearer fake-access-gmail-3"
    assert token.call_count == 2
    # a refresh that fails surfaces as a readable status
    token.side_effect = [httpx.Response(400, json={"error": "invalid_grant"})]
    profile.side_effect = [httpx.Response(401, json={"error": {"message": "nope"}})]
    with pytest.raises(Exception, match="refresh failed"):
        await gmail.fetch_account()
    st = gmail.status()
    assert st["status"] == "error" and "reconnect" in st["error"]
    await rt.stop()


def test_disconnect_revokes_and_forgets(client, http):
    rt = client.rt
    connect(rt, "gmail", "google")
    rt.connectors["gmail"].kv_set("history_id", "1000")
    revoke = http.post("https://oauth2.googleapis.com/revoke").respond(200)
    r = client.delete("/api/connections/gmail")
    assert r.json() == {"ok": True, "revoked": True}
    assert parse_qs(revoke.calls.last.request.content.decode())["token"] == ["fake-refresh-gmail"]
    assert not rt.vault.has("gmail") and rt.connectors["gmail"].kv_get("history_id") is None
    assert any(a["title"] == "Disconnected Gmail" for a in rt.store.list_activity(20))
    assert not client.get("/api/connections").json()["connections"][1]["connected"]


async def test_slack_and_microsoft_revoke(make_rt, http):
    rt = make_rt(
        cfg=cfg_with(
            ConnectorConfig(type="slack", name="slack"), ConnectorConfig(type="m365", name="outlook")
        ),
        env=dict(ENV),
    )
    await rt.start()
    connect(rt, "slack", "slack", user_id="U01")
    connect(rt, "outlook", "microsoft")
    auth_revoke = http.post("https://slack.com/api/auth.revoke").respond(json={"ok": True, "revoked": True})
    assert await rt.connectors["slack"].disconnect() is True
    assert auth_revoke.calls.last.request.headers["authorization"] == "Bearer fake-access-slack-1"
    assert await rt.connectors["outlook"].disconnect() is False  # no revocation endpoint: local delete
    assert not rt.vault.has("outlook")
    await rt.stop()


async def test_tokens_echoed_by_an_api_are_masked(make_rt, http):
    rt = make_rt(cfg=cfg_with(ConnectorConfig(type="gmail", name="gmail")), env=dict(ENV))
    await rt.start()
    connect(rt, "gmail", "google")
    msg = fx("gmail_message_m3")
    msg["snippet"] = "your token is fake-access-gmail-1"
    msg["payload"]["body"] = {"data": base64.urlsafe_b64encode(b"leak fake-refresh-gmail here").decode()}
    http.get(f"{GMAIL}/messages/m3").respond(json=msg)
    out = await rt.registry.invoke("gmail_read", {"id": "m3"}, ToolContext(rt=rt))
    assert out.ok and "fake-access-gmail-1" not in out.text and "fake-refresh-gmail" not in out.text
    assert "fake-refresh-gmail" not in json.dumps(out.value)
    assert "[redacted]" in out.text
    await rt.stop()


async def test_not_connected_is_quiet_and_tools_say_connect_first(make_rt):
    rt = make_rt(cfg=cfg_with(ConnectorConfig(type="gmail", name="gmail")), env=dict(ENV))
    await rt.start()
    gmail = rt.connectors["gmail"]
    assert await gmail.poll(None) == [] and await gmail.upcoming(rt.clock.now(), rt.clock.now()) == []
    assert "gmail_send" in rt.registry.tools and gmail.send_tool is None
    out = await rt.registry.invoke("gmail_search", {"query": "x"}, ToolContext(rt=rt))
    assert not out.ok and "connect Gmail first" in out.text
    st = gmail.status()
    assert st["connected"] is False and st["status"] == "disconnected" and st["push"]["mode"] == "poll"
    await rt.stop()


async def test_auto_offer_when_client_env_is_set(make_rt):
    rt = make_rt(env={"GOOGLE_CLIENT_ID": "x", "MS_CLIENT_ID": "y"})
    assert {"gmail", "calendar", "outlook"} <= set(rt.connectors) and "slack" not in rt.connectors
    assert rt.connectors["gmail"].status()["configured"] is False
    # a name already taken falls back to the type name; a configured type is never doubled
    cfg = cfg_with(
        ConnectorConfig(type="ics", name="calendar", path="x.ics"), ConnectorConfig(type="gmail", name="mail")
    )
    rt2 = make_rt(cfg=cfg, env={"GOOGLE_CLIENT_ID": "x"})
    assert rt2.connectors["google_calendar"].type == "google_calendar"
    assert "gmail" not in rt2.connectors and rt2.connectors["mail"].type == "gmail"
    # mock mode with no env stays offline
    assert set(make_rt().connectors) == {"demo", "inbox"}


def test_provider_labels_count_as_bulk():
    promo = {
        "title": "Hi",
        "body": "",
        "sender": "a@b.c",
        "data": {"labels": ["INBOX", "CATEGORY_PROMOTIONS"]},
    }
    other = {"title": "Hi", "body": "", "sender": "a@b.c", "data": {"inference_classification": "other"}}
    plain = {"title": "Hi", "body": "", "sender": "a@b.c", "data": {"labels": ["INBOX"]}}
    assert apply_rules(promo, [], []).force_action == "ignore"
    assert apply_rules(other, [], []).force_action == "ignore"
    assert apply_rules(plain, [], []).force_action is None
