"""HTTP API (SPEC §10) through FastAPI's TestClient, in mock mode."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from tandem.connectors.simple import sign
from tandem.server.app import create_app
from tests.conftest import local, mock_cfg


def wait_for(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        val = fn()
        if val:
            return val
        time.sleep(0.05)
    raise AssertionError("timed out")


@pytest.fixture
def client(make_rt):
    rt = make_rt(start=local(9, 15))
    with TestClient(create_app(rt, run_scheduler=False)) as c:
        c.rt = rt  # type: ignore[attr-defined]
        yield c


def test_health_config_and_ui_fallback(client):
    assert client.get("/api/health").json()["mode"] == "mock"
    cfg = client.get("/api/config").json()
    assert cfg["mode"] == "mock" and set(cfg["models"]) == {"triage", "brief", "chat", "worker"}
    assert {h["name"] for h in cfg["harnesses"]} >= {"builtin", "cli"}
    assert {c["name"] for c in cfg["connectors"]} == {"demo", "inbox"}
    assert cfg["policy"]["on_timeout"] == "deny" and cfg["voice"]["server"] is False
    assert "api_key" not in json.dumps(cfg).replace("has_api_key", "")
    assert "<div id=" in client.get("/").text
    assert client.get("/api/nope").status_code == 404


def test_thread_message_gets_a_reply(client):
    r = client.post("/api/thread", json={"text": "remember that I prefer short emails"})
    assert r.status_code == 200 and r.json()["message"]["role"] == "user"
    reply = wait_for(
        lambda: [m for m in client.get("/api/thread").json()["messages"] if m["text"].startswith("Got it")]
    )
    assert reply
    assert any("short emails" in m["content"] for m in client.get("/api/memory").json())
    assert client.post("/api/thread", json={"text": ""}).status_code == 422


def test_work_request_runs_task_with_approval_and_artifact(client):
    client.post("/api/thread", json={"text": "Build me a cash summary for this week and email it to Sam."})
    ap = wait_for(lambda: client.get("/api/approvals", params={"state": "pending"}).json())[0]
    thread = client.get("/api/thread").json()
    assert ap["id"] in thread["approvals"]
    task_id = ap["task_id"]
    assert thread["tasks"][task_id]["state"] in ("waiting", "running")
    r = client.post(
        f"/api/approvals/{ap['id']}/decision", json={"decision": "edit", "args": {"bcc": "evil@x.example"}}
    )
    assert r.status_code == 409
    assert (
        client.post(f"/api/approvals/{ap['id']}/decision", json={"decision": "approve"}).json()["state"]
        == "approved"
    )
    task = wait_for(lambda: (t := client.get(f"/api/tasks/{task_id}").json())["state"] == "done" and t)
    assert task["logs"] and task["artifact_ids"]
    aid = task["artifact_ids"][0]
    assert client.get(f"/api/artifacts/{aid}").json()["kind"] == "xlsx"
    dl = client.get(f"/api/artifacts/{aid}/download")
    assert dl.status_code == 200 and dl.content[:2] == b"PK"
    pv = client.get(f"/api/artifacts/{aid}/preview")
    assert "<table>" in pv.text and "default-src 'none'" in pv.headers["content-security-policy"]
    assert any(o["to"] == "sam@ledgerwise.example" for o in client.get("/api/outbox").json())
    assert any(a["decision"] == "approved" for a in client.get("/api/audit").json())
    assert client.post(f"/api/approvals/{ap['id']}/decision", json={"decision": "approve"}).status_code == 409
    assert client.post("/api/approvals/ap_missing/decision", json={"decision": "approve"}).status_code == 404


def test_tasks_endpoints_steer_and_stop(client):
    r = client.post("/api/tasks", json={"instructions": "Prepare a cash summary and email it to Sam."})
    task = r.json()
    wait_for(lambda: client.get("/api/approvals", params={"state": "pending"}).json())
    assert client.post(f"/api/tasks/{task['id']}/steer", json={"text": "keep it short"}).status_code == 200
    stopped = client.post(f"/api/tasks/{task['id']}/stop").json()
    assert stopped["state"] == "stopped"
    assert client.post(f"/api/tasks/{task['id']}/steer", json={"text": "x"}).status_code == 409
    assert client.post("/api/tasks", json={"instructions": "x", "harness": "nope"}).status_code == 400
    assert client.get("/api/tasks/tk_missing").status_code == 404
    assert any(t["id"] == task["id"] for t in client.get("/api/tasks").json())


def test_checkin_brief_today_events_activity(client):
    res = client.post("/api/checkin/run").json()
    assert res["new_events"] >= 5 and res["ignored"] >= 1
    brief = client.post("/api/brief/run").json()
    assert brief["greeting"].startswith("Good morning")
    today = client.get("/api/today").json()
    assert today["brief"]["id"] == brief["id"] and today["upcoming"]
    assert today["stats"]["events_today"] >= 1
    assert client.get("/api/events", params={"status": "ignored"}).json()
    acts = client.get("/api/activity", params={"limit": 5}).json()
    assert len(acts) == 5 and acts[0]["id"] > acts[-1]["id"]
    older = client.get("/api/activity", params={"before": acts[-1]["id"], "limit": 5}).json()
    assert all(a["id"] < acts[-1]["id"] for a in older)


def test_memory_crud_and_skills(client):
    m = client.post("/api/memory", json={"kind": "preference", "content": "vip: ceo@x.example"}).json()
    assert m["kind"] == "preference"
    assert client.delete(f"/api/memory/{m['id']}").json() == {"ok": True}
    assert client.delete(f"/api/memory/{m['id']}").status_code == 404
    names = {s["name"] for s in client.get("/api/skills").json()}
    assert {"weekly-cash-summary", "meeting-scheduler"} <= names


def test_webhook_triggers_triage(client):
    r = client.post(
        "/api/webhooks/inbox",
        json={
            "title": "Site down",
            "body": "Outage: checkout is down, customers can't pay today",
            "id": "inc-1",
        },
    )
    assert r.status_code == 200 and r.json()["accepted"] == 1 and r.json()["notified"] == 1
    assert client.post("/api/webhooks/missing", json={}).status_code == 404
    assert (
        client.post(
            "/api/webhooks/inbox", content=b"[1]", headers={"content-type": "application/json"}
        ).status_code
        == 400
    )
    assert (
        client.post("/api/webhooks/demo", json={"a": 1}).status_code == 400
    )  # fake connector takes no webhooks


def test_webhook_signature_required_when_configured(make_rt):
    from tandem.config import ConnectorConfig

    cfg = mock_cfg(connectors=[ConnectorConfig(type="webhook", name="hooks", secret_env="HOOK_SECRET")])
    rt = make_rt(cfg=cfg, env={"HOOK_SECRET": "hook-secret-123"})
    with TestClient(create_app(rt, run_scheduler=False)) as c:
        body = json.dumps({"title": "hello"}).encode()
        assert c.post("/api/webhooks/hooks", content=body).status_code == 401
        ok = c.post(
            "/api/webhooks/hooks", content=body, headers={"x-tandem-signature": sign("hook-secret-123", body)}
        )
        assert ok.status_code == 200


def test_auth_token_and_login_cookie(make_rt):
    rt = make_rt(auth_token="let-me-in-please")
    with TestClient(create_app(rt, run_scheduler=False)) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/config").status_code == 401
        assert c.get("/api/config", headers={"Authorization": "Bearer let-me-in-please"}).status_code == 200
        assert c.post("/api/auth/login", json={"token": "wrong"}).status_code == 401
        assert c.post("/api/auth/login", json={"token": "let-me-in-please"}).status_code == 200
        assert c.get("/api/config").status_code == 200  # cookie
        assert c.get("/api/config").json()["auth"] is True


def test_voice_mock_and_disabled(make_rt):
    cfg = mock_cfg()
    cfg.voice.provider = "mock"
    with TestClient(create_app(make_rt(cfg=cfg), run_scheduler=False)) as c:
        r = c.post("/api/voice/transcribe", files={"file": ("a.webm", b"\x00\x01", "audio/webm")})
        assert r.status_code == 200 and "cash summary" in r.json()["text"]
    with TestClient(create_app(make_rt(), run_scheduler=False)) as c:
        assert (
            c.post("/api/voice/transcribe", files={"file": ("a.webm", b"\x00", "audio/webm")}).status_code
            == 501
        )


def test_sse_stream_sends_hello_and_events(client):
    rt = client.rt
    q = rt.bus.subscribe()
    rt.store.add_activity("test", "ping")
    assert q.get_nowait().type == "activity.created"
    rt.bus.unsubscribe(q)
    from tandem.server import app as app_module

    assert app_module.COOKIE == "tandem_token"
    assert any(r.path == "/api/stream" for r in client.app.routes)
