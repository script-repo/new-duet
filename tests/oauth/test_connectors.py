"""Gmail, Google Calendar, Slack and Microsoft 365 against recorded responses (SPEC §15.3): polling and
normalization, push handshakes, and actions through the approval gate. No live network."""

from __future__ import annotations

import base64
import email
import email.policy
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from tandem.config import ConnectorConfig
from tandem.connectors.base import PushRequest
from tandem.connectors.simple import WebhookAuthError
from tandem.connectors.slack import slack_signature
from tandem.engine.triage import apply_rules
from tandem.server.app import create_app
from tests.conftest import eventually
from tests.oauth.helpers import (
    CAL,
    ENV,
    GMAIL,
    GRAPH,
    PUBLIC,
    SLACK,
    approve_call,
    cfg_with,
    connect,
    fx,
)


@pytest.fixture
async def make(make_rt):
    """A started runtime with one connected OAuth connector and a public https URL."""
    made = []

    async def build(conn: ConnectorConfig, provider: str, **extra):
        rt = make_rt(cfg=cfg_with(conn), env=dict(ENV), public_url=PUBLIC)
        await rt.start()
        connect(rt, conn.name, provider, **extra)
        made.append(rt)
        return rt, rt.connectors[conn.name]

    yield build
    for rt in made:
        await rt.stop()


def _q(call) -> dict[str, list[str]]:
    return parse_qs(urlparse(str(call.request.url)).query)


# ---- Gmail ------------------------------------------------------------------------------------------------


def _gmail_routes(http):
    http.get(f"{GMAIL}/profile").respond(json=fx("gmail_profile"))
    http.get(f"{GMAIL}/messages").respond(json=fx("gmail_list"))
    for mid in ("m1", "m2", "m3"):
        http.get(f"{GMAIL}/messages/{mid}").respond(json=fx(f"gmail_message_{mid}"))


async def test_gmail_poll_normalizes_and_follows_history(make, http):
    _rt, gmail = await make(ConnectorConfig(type="gmail", name="gmail"), "google")
    _gmail_routes(http)
    first = await gmail.poll(None)
    assert [e.title for e in first] == ["Q3 numbers", "Fall sale starts now"]
    q3, sale = first
    assert q3.sender == "dana@acme.example" and q3.external_id == "<q3-numbers@acme.example>"
    assert q3.body.startswith("Hi Sam,") and q3.data["thread_id"] == "t1" and q3.data["important"]
    assert "Fall sale" in sale.body and "<h1>" not in sale.body
    assert sale.data["headers"]["list-unsubscribe"] and "CATEGORY_PROMOTIONS" in sale.data["labels"]
    assert apply_rules(sale.row(), [], []).force_action == "ignore"
    assert gmail.kv_get("history_id") == "1000"

    history = http.get(f"{GMAIL}/history").respond(json=fx("gmail_history"))
    nxt = await gmail.poll(None)
    assert [e.title for e in nxt] == ["Lunch tomorrow?"]  # the SENT message is skipped
    assert _q(history.calls.last)["startHistoryId"] == ["1000"]
    assert gmail.kv_get("history_id") == "1005"


async def test_gmail_expired_history_resyncs(make, http):
    _rt, gmail = await make(ConnectorConfig(type="gmail", name="gmail"), "google")
    _gmail_routes(http)
    gmail.kv_set("history_id", "1")
    http.get(f"{GMAIL}/history").respond(
        404, json={"error": {"code": 404, "message": "Requested entity was not found."}}
    )
    events = await gmail.poll(None)
    assert len(events) == 2 and gmail.kv_get("history_id") == "1000"


async def test_gmail_push_watch_and_token_check(make, http):
    rt, gmail = await make(
        ConnectorConfig(type="gmail", name="gmail", pubsub_topic="projects/p/topics/tandem"), "google"
    )
    _gmail_routes(http)
    watch = http.post(f"{GMAIL}/watch").respond(json={"historyId": "1000", "expiration": "1793000000000"})
    await gmail.on_connected()
    body = json.loads(watch.calls.last.request.content)
    assert body == {
        "topicName": "projects/p/topics/tandem",
        "labelIds": ["INBOX"],
        "labelFilterBehavior": "include",
    }
    st = gmail.status()
    assert st["push"]["mode"] == "push" and "Pub/Sub" in st["push"]["detail"]
    assert gmail.kv_get("history_id") == "1000"
    secret = gmail.push_secret()
    assert secret not in json.dumps(st) and gmail.push_endpoint().endswith(f"?token={secret}")

    note = {
        "message": {
            "data": base64.b64encode(b'{"emailAddress":"sam@example.com","historyId":"1005"}').decode()
        }
    }
    with pytest.raises(WebhookAuthError):
        await gmail.handle_push(PushRequest(note, {}, b"", {"token": "wrong"}))
    http.get(f"{GMAIL}/history").respond(json=fx("gmail_history"))
    res = await gmail.handle_push(PushRequest(note, {}, b"", {"token": secret}))
    assert res.body == {"accepted": 0, "queued": True}
    ev = await eventually(lambda: [e for e in rt.store.list_events() if e["source"] == "gmail"])
    assert ev[0]["title"] == "Lunch tomorrow?"  # fetched from the API, not taken from the payload


async def test_gmail_send_and_reply_go_through_the_gate(make, http):
    rt, _gmail = await make(ConnectorConfig(type="gmail", name="gmail"), "google")
    send = http.post(f"{GMAIL}/messages/send").respond(json={"id": "sent1", "threadId": "t1"})
    out = await approve_call(
        rt, "gmail_send", {"to": "dana@acme.example", "subject": "Hello", "body": "Hi Dana"}
    )
    assert out.ok and out.decision == "approved"
    msg = _mime(json.loads(send.calls.last.request.content)["raw"])
    assert msg["To"] == "dana@acme.example" and msg["Subject"] == "Hello" and msg["From"] == "sam@example.com"

    http.get(f"{GMAIL}/messages/m1").respond(json=fx("gmail_message_m1"))
    out = await approve_call(rt, "gmail_reply", {"id": "m1", "body": "Confirmed: numbers attached."})
    assert out.ok
    sent = json.loads(send.calls.last.request.content)
    assert sent["threadId"] == "t1"
    msg = _mime(sent["raw"])
    assert msg["In-Reply-To"] == "<q3-numbers@acme.example>"
    assert msg["References"] == "<kickoff@acme.example> <q3-numbers@acme.example>"
    assert msg["Subject"] == "Re: Q3 numbers" and "dana@acme.example" in msg["To"]
    assert msg.get_content().strip() == "Confirmed: numbers attached."
    audit = rt.store.list_audit(10)
    assert {a["tool"] for a in audit if a["decision"] == "approved"} == {"gmail_send", "gmail_reply"}


def _mime(raw: str) -> email.message.EmailMessage:
    return email.message_from_bytes(
        base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=email.policy.default
    )


# ---- Google Calendar ---------------------------------------------------------------------------------------


async def test_calendar_sync_tokens_and_resync(make, http):
    rt, cal = await make(ConnectorConfig(type="google_calendar", name="calendar"), "google")
    events_url = f"{CAL}/calendars/primary/events"
    route = http.get(events_url)
    route.side_effect = [httpx.Response(200, json=fx("calendar_full"))]
    assert await cal.poll(None) == []  # first sync is a baseline
    assert cal.kv_get("sync_token") == "sync-1"

    route.side_effect = [httpx.Response(200, json=fx("calendar_incremental"))]
    evs = await cal.poll(None)
    assert _q(route.calls.last)["syncToken"] == ["sync-1"]
    assert [e.title for e in evs] == ["Invitation: Budget review", "Cancelled: (no title)"]
    inv = evs[0]
    assert (
        inv.kind == "calendar"
        and inv.sender == "dana@acme.example"
        and inv.data["response_status"] == "needsAction"
    )
    assert {"email": "dana@acme.example", "response": "accepted"} in inv.data["attendees"]
    assert cal.kv_get("sync_token") == "sync-2"

    # 410 Gone: full resync, keeping only what changed since the last sync
    rt.clock.advance(minutes=5)
    changed = fx("calendar_incremental")
    changed["items"][0]["updated"] = "2026-10-21T13:03:00.000Z"
    changed["items"] = [*fx("calendar_full")["items"], changed["items"][0]]
    changed["nextSyncToken"] = "sync-3"
    route.side_effect = [
        httpx.Response(410, json={"error": {"code": 410, "message": "Sync token is no longer valid"}}),
        httpx.Response(200, json=changed),
    ]
    evs = await cal.poll(None)
    assert [e.title for e in evs] == ["Invitation: Budget review"]
    assert "syncToken" not in _q(route.calls.last) and cal.kv_get("sync_token") == "sync-3"


async def test_calendar_upcoming_feeds_today(make, http):
    rt, _cal = await make(ConnectorConfig(type="google_calendar", name="calendar"), "google")
    http.get(f"{CAL}/calendars/primary/events").respond(json=fx("calendar_upcoming"))
    today = await rt.today()
    assert [i["title"] for i in today["upcoming"]] == ["1:1 with Dana"]
    assert today["upcoming"][0]["source"] == "calendar"


async def test_calendar_watch_channel_and_token(make, http):
    rt, cal = await make(ConnectorConfig(type="google_calendar", name="calendar"), "google")
    watch = http.post(f"{CAL}/calendars/primary/events/watch").respond(
        json={"resourceId": "res-1", "expiration": "1793000000000"}
    )
    await cal.on_connected()
    body = json.loads(watch.calls.last.request.content)
    assert body["type"] == "web_hook" and body["address"] == f"{PUBLIC}/api/webhooks/calendar"
    assert body["token"] == cal.push_secret() and cal.kv_get("channel")["resource_id"] == "res-1"
    channel = cal.kv_get("channel")["id"]

    with pytest.raises(WebhookAuthError):
        await cal.handle_push(PushRequest({}, {"x-goog-channel-token": "nope", "x-goog-channel-id": channel}))
    with pytest.raises(WebhookAuthError):
        await cal.handle_push(
            PushRequest({}, {"x-goog-channel-token": body["token"], "x-goog-channel-id": "other"})
        )
    hdrs = {
        "x-goog-channel-token": body["token"],
        "x-goog-channel-id": channel,
        "x-goog-resource-state": "sync",
    }
    assert (await cal.handle_push(PushRequest({}, hdrs))).body == {"accepted": 0}
    cal.kv_set("sync_token", "sync-1")
    cal.kv_set("synced_at", "2026-10-21T12:00:00.000000Z")
    http.get(f"{CAL}/calendars/primary/events").respond(json=fx("calendar_incremental"))
    res = await cal.handle_push(PushRequest({}, {**hdrs, "x-goog-resource-state": "exists"}))
    assert res.body["queued"]
    await eventually(lambda: any(e["title"] == "Invitation: Budget review" for e in rt.store.list_events()))

    # renewal a day before expiry: stop the old channel, open a new one
    stop = http.post(f"{CAL}/channels/stop").respond(204)
    cal.kv_set("push_expires_at", "2026-10-21T20:00:00.000000Z")
    await cal.ensure_push()
    assert json.loads(stop.calls.last.request.content) == {"id": channel, "resourceId": "res-1"}
    assert cal.kv_get("channel")["id"] != channel


async def test_calendar_create_sends_invites_after_approval(make, http):
    rt, _cal = await make(ConnectorConfig(type="google_calendar", name="calendar"), "google")
    create = http.post(f"{CAL}/calendars/primary/events").respond(
        json={"id": "new1", "htmlLink": "https://cal/x"}
    )
    out = await approve_call(
        rt,
        "calendar_create",
        {
            "title": "Budget review",
            "start": "2026-10-22T14:00:00-04:00",
            "duration_min": 45,
            "attendees": ["dana@acme.example"],
        },
    )
    assert out.ok
    assert _q(create.calls.last)["sendUpdates"] == ["all"]
    body = json.loads(create.calls.last.request.content)
    assert body["summary"] == "Budget review" and body["attendees"] == [{"email": "dana@acme.example"}]
    assert body["end"]["dateTime"] == "2026-10-22T14:45:00-04:00"


# ---- Slack ------------------------------------------------------------------------------------------------


def _slack_routes(http):
    http.get(f"{SLACK}/users.conversations").respond(json=fx("slack_conversations"))

    def history(request):
        ch = parse_qs(urlparse(str(request.url)).query)["channel"][0]
        return httpx.Response(200, json=fx({"D01": "slack_history_dm", "C01": "slack_history_general"}[ch]))

    def users(request):
        uid = parse_qs(urlparse(str(request.url)).query)["user"][0]
        if uid == "U02":
            return httpx.Response(200, json=fx("slack_user_U02"))
        return httpx.Response(200, json={"ok": False, "error": "user_not_found"})

    hist = http.get(f"{SLACK}/conversations.history")
    hist.side_effect = history
    http.get(f"{SLACK}/users.info").side_effect = users
    return hist


async def test_slack_poll_dms_mentions_and_cursors(make, http):
    _rt, slack = await make(
        ConnectorConfig(type="slack", name="slack", channels=["general"]), "slack", user_id="U01"
    )
    hist = _slack_routes(http)
    evs = await slack.poll(None)
    assert {c.request.url.params["channel"] for c in hist.calls} == {"D01", "C01"}  # not #random
    assert [e.body for e in evs] == ["Can you send the deck before 3?", "<@U01> the build is red"]
    dm, mention = evs
    assert dm.kind == "chat" and dm.sender == "dana@acme.example" and dm.title.startswith("Dana in DM:")
    assert dm.data["channel_type"] == "im" and dm.external_id == "D01:1792328400.000100"
    assert mention.data["mentions_me"] and mention.data["channel_type"] == "channel"
    assert slack.kv_get("ts") == {"D01": "1792328500.000200", "C01": "1792328300.000100"}
    await slack.poll(None)
    assert hist.calls.last.request.url.params["oldest"] in ("1792328500.000200", "1792328300.000100")


def _signed(secret: str, body: dict, ts: str) -> PushRequest:
    raw = json.dumps(body).encode()
    return PushRequest(
        body, {"x-slack-request-timestamp": ts, "x-slack-signature": slack_signature(secret, ts, raw)}, raw
    )


async def test_slack_events_api_handshake_and_signatures(make, http):
    rt, slack = await make(ConnectorConfig(type="slack", name="slack"), "slack", user_id="U01")
    now = str(int(rt.clock.now().timestamp()))
    secret = ENV["SLACK_SIGNING_SECRET"]
    res = await slack.handle_push(_signed(secret, {"type": "url_verification", "challenge": "ch-123"}, now))
    assert res.body == {"challenge": "ch-123"}
    with pytest.raises(WebhookAuthError, match="X-Slack-Signature"):
        await slack.handle_push(_signed("wrong-secret", {"type": "url_verification", "challenge": "x"}, now))
    stale = str(int(rt.clock.now().timestamp()) - 6 * 60)
    with pytest.raises(WebhookAuthError, match="stale"):
        await slack.handle_push(_signed(secret, {"type": "url_verification", "challenge": "x"}, stale))
    assert slack.status()["push"]["mode"] == "push"

    _slack_routes(http)
    cb = {"type": "event_callback", "event": {"type": "message", "channel": "D01", "text": "IGNORED PAYLOAD"}}
    res = await slack.handle_push(_signed(secret, cb, now))
    assert res.body["queued"]
    evs = await eventually(lambda: [e for e in rt.store.list_events() if e["source"] == "slack"])
    assert all("IGNORED PAYLOAD" not in e["body"] for e in evs)


def test_slack_handshake_over_http(make_rt, http):
    rt = make_rt(cfg=cfg_with(ConnectorConfig(type="slack", name="slack")), env=dict(ENV), public_url=PUBLIC)
    with TestClient(create_app(rt, run_scheduler=False)) as c:
        ts = str(int(rt.clock.now().timestamp()))
        raw = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
        sig = slack_signature(ENV["SLACK_SIGNING_SECRET"], ts, raw)
        r = c.post(
            "/api/webhooks/slack",
            content=raw,
            headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig},
        )
        assert r.status_code == 200 and r.json() == {"challenge": "abc"}
        r = c.post(
            "/api/webhooks/slack",
            content=raw,
            headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": "v0=bad"},
        )
        assert r.status_code == 401


async def test_slack_post_is_gated_as_send_and_external_post(make, http):
    rt, _slack = await make(ConnectorConfig(type="slack", name="slack"), "slack", user_id="U01")
    assert rt.registry.get("slack_post").tags == {"send", "external_post"}
    post = http.post(f"{SLACK}/chat.postMessage").respond(json={"ok": True, "channel": "D01", "ts": "1.2"})
    out = await approve_call(
        rt, "slack_post", {"channel": "D01", "text": "Deck sent!", "thread_ts": "1792328400.000100"}
    )
    assert out.ok
    assert json.loads(post.calls.last.request.content) == {
        "channel": "D01",
        "text": "Deck sent!",
        "thread_ts": "1792328400.000100",
    }
    # Slack's 200 {"ok": false} errors become readable tool errors
    http.get(f"{SLACK}/search.messages").respond(json={"ok": False, "error": "missing_scope"})
    from tandem.tools import ToolContext

    bad = await rt.registry.invoke("slack_search", {"query": "deck"}, ToolContext(rt=rt))
    assert not bad.ok and "missing_scope" in bad.text


# ---- Microsoft 365 ---------------------------------------------------------------------------------------


def _graph_routes(http):
    mail = http.get(url__startswith=f"{GRAPH}/me/mailFolders/inbox/messages/delta")
    mail.side_effect = [
        httpx.Response(200, json=fx("graph_mail_delta_1")),
        httpx.Response(200, json=fx("graph_mail_delta_2")),
        httpx.Response(200, json=fx("graph_mail_delta_3")),
    ]
    cal = http.get(url__startswith=f"{GRAPH}/me/calendarView/delta")
    cal.side_effect = [
        httpx.Response(200, json=fx("graph_calendar_delta_1")),
        httpx.Response(200, json=fx("graph_calendar_delta_2")),
    ]
    return mail, cal


async def test_m365_delta_links_persist_and_normalize(make, http):
    _rt, ms = await make(ConnectorConfig(type="m365", name="outlook", push=False), "microsoft")
    mail, cal = _graph_routes(http)
    evs = await ms.poll(None)
    assert [e.title for e in evs] == ["Contract signature needed today", "Weekly digest"]  # read mail skipped
    assert mail.calls[0].request.url.params["$filter"] == "receivedDateTime ge 2026-10-20T13:00:00Z"
    assert "skiptoken=page2" in str(mail.calls[1].request.url)
    assert 'outlook.body-content-type="text"' in mail.calls[0].request.headers["prefer"]
    contract, digest = evs
    assert contract.sender == "legal@partner.example" and contract.data["importance"] == "high"
    assert contract.data["thread_id"] == "conv-1" and contract.external_id == "<contract@legal.example>"
    assert apply_rules(digest.row(), [], []).force_action == "ignore"  # Focused Inbox "Other"
    assert ms.kv_get("mail_delta").endswith("deltatoken=mail-d1")
    assert ms.kv_get("calendar_delta").endswith("deltatoken=cal-d1")

    evs = await ms.poll(None)
    assert "deltatoken=mail-d1" in str(mail.calls[2].request.url)
    assert "deltatoken=cal-d1" in str(cal.calls[1].request.url)
    assert [e.title for e in evs] == ["Re: offsite", "Invitation: Vendor sync", "Cancelled: an event"]
    assert evs[1].kind == "calendar" and evs[1].sender == "vendor@supplier.example"
    assert ms.kv_get("mail_delta").endswith("deltatoken=mail-d2")


async def test_m365_subscriptions_validation_and_client_state(make, http):
    rt, ms = await make(ConnectorConfig(type="m365", name="outlook"), "microsoft")
    http.get(f"{GRAPH}/me").respond(json={"mail": "sam@example.com"})
    subs = http.post(f"{GRAPH}/subscriptions")
    subs.side_effect = [
        httpx.Response(201, json={"id": "sub-mail"}),
        httpx.Response(201, json={"id": "sub-cal"}),
    ]
    await ms.on_connected()
    bodies = [json.loads(c.request.content) for c in subs.calls]
    assert [b["resource"] for b in bodies] == ["me/mailFolders('inbox')/messages", "me/events"]
    assert all(b["notificationUrl"] == f"{PUBLIC}/api/webhooks/outlook" for b in bodies)
    assert bodies[0]["clientState"] == ms.push_secret()
    assert ms.kv_get("subscriptions") == {"mail": "sub-mail", "calendar": "sub-cal"}

    good = {
        "value": [{"subscriptionId": "sub-mail", "clientState": ms.push_secret(), "changeType": "created"}]
    }
    bad = {"value": [{"subscriptionId": "sub-mail", "clientState": "forged"}]}
    with pytest.raises(WebhookAuthError):
        await ms.handle_push(PushRequest(bad, {}))
    _graph_routes(http)
    res = await ms.handle_push(PushRequest(good, {}))
    assert res.status == 202
    await eventually(lambda: any(e["source"] == "outlook" for e in rt.store.list_events()))

    # renewal PATCHes the existing subscriptions
    patch = http.patch(url__startswith=f"{GRAPH}/subscriptions/").respond(json={})
    ms.kv_set("push_expires_at", "2026-10-21T20:00:00.000000Z")
    await ms.ensure_push()
    assert patch.call_count == 2 and "expirationDateTime" in json.loads(patch.calls.last.request.content)


def test_m365_validation_token_echo_over_http(make_rt, http):
    rt = make_rt(cfg=cfg_with(ConnectorConfig(type="m365", name="outlook")), env=dict(ENV), public_url=PUBLIC)
    with TestClient(create_app(rt, run_scheduler=False)) as c:
        r = c.post("/api/webhooks/outlook", params={"validationToken": "Validation: token 123"})
        assert r.status_code == 200 and r.text == "Validation: token 123"
        assert r.headers["content-type"].startswith("text/plain")
        r = c.post("/api/webhooks/outlook", json={"value": [{"clientState": "forged"}]})
        assert r.status_code == 401


async def test_m365_send_reply_and_event_go_through_the_gate(make, http):
    rt, ms = await make(ConnectorConfig(type="m365", name="outlook"), "microsoft")
    send = http.post(f"{GRAPH}/me/sendMail").respond(202)
    out = await approve_call(
        rt, "outlook_send", {"to": "a@x.example, b@x.example", "subject": "Hi", "body": "Hello"}
    )
    assert out.ok
    msg = json.loads(send.calls.last.request.content)["message"]
    assert [r["emailAddress"]["address"] for r in msg["toRecipients"]] == ["a@x.example", "b@x.example"]
    assert msg["body"] == {"contentType": "Text", "content": "Hello"}

    reply = http.post(f"{GRAPH}/me/messages/AAM1/replyAll").respond(202)
    out = await approve_call(rt, "outlook_reply", {"id": "AAM1", "body": "Signed.", "reply_all": True})
    assert out.ok and json.loads(reply.calls.last.request.content) == {"comment": "Signed."}

    ev = http.post(f"{GRAPH}/me/events").respond(201, json={"id": "EVN", "webLink": "https://outlook/x"})
    out = await approve_call(
        rt,
        "outlook_calendar_create",
        {"title": "Sync", "start": "2026-10-22T10:00:00-04:00", "attendees": ["v@s.example"]},
    )
    assert out.ok
    body = json.loads(ev.calls.last.request.content)
    assert body["start"] == {"dateTime": "2026-10-22T14:00:00", "timeZone": "UTC"}
    assert body["attendees"][0]["emailAddress"]["address"] == "v@s.example"
    assert ms.send_tool == "outlook_send"
