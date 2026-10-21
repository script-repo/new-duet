"""Policy, approvals, the tool gate, audit and redaction."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from tandem.policy import Policy, Rule
from tandem.security import MASK, Redactor, SecretResolver
from tandem.store import Store
from tandem.tools import ApprovalService, Tool, ToolContext, ToolRegistry, schema, validate_args
from tandem.util import FakeClock
from tests.conftest import eventually, local


def make_registry(policy: Policy | None = None, env: dict[str, str] | None = None):
    clock = FakeClock(local(9))
    store = Store("sqlite://", clock=clock)
    policy = policy or Policy()
    approvals = ApprovalService(store, policy)
    secrets = SecretResolver(env or {})
    reg = ToolRegistry(store, policy, approvals, Redactor(secrets))
    sent: list[dict] = []

    async def send(args, ctx):
        sent.append(args)
        return {"sent": True, "echo": args.get("body", "")}

    async def read(args, ctx):
        return {"secret_seen": env.get("API_TOKEN") if env else None, "text": "hello"}

    async def boom(args, ctx):
        raise RuntimeError("kaput")

    reg.add(
        Tool(
            "email_send",
            "Send an email.",
            schema({"to": {"type": "string"}, "body": {"type": "string"}}, ["to"]),
            send,
            tags=frozenset({"send"}),
            editable=("body",),
            summarize=lambda a: f"Email {a['to']}",
        )
    )
    reg.add(Tool("data_read", "Read data.", schema({}), read))
    reg.add(
        Tool("pay", "Pay.", schema({"amount": {"type": "number"}}, ["amount"]), send, tags=frozenset({"pay"}))
    )
    reg.add(Tool("boom", "Fails.", schema({}), boom, tags=frozenset({"write"})))
    return reg, store, approvals, sent, clock


def test_policy_first_match_and_conditions():
    p = Policy.from_dict(
        {
            "default": "allow",
            "rules": [
                {
                    "match": {"tool": "pay*", "args": {"amount": {"gt": 100}}},
                    "action": "deny",
                    "reason": "too much",
                },
                {"match": {"tags": ["pay"]}, "action": "ask"},
                {"match": {"tool": "x", "args": {"to": {"regex": "@corp\\.example$"}}}, "action": "allow"},
                {"match": {"tool": "x"}, "action": "ask"},
            ],
        }
    )
    assert p.evaluate("pay", {"pay"}, {"amount": "1,000"}).action == "deny"
    assert p.evaluate("pay", {"pay"}, {"amount": 50}).action == "ask"
    assert p.evaluate("x", set(), {"to": "a@corp.example"}).action == "allow"
    assert p.evaluate("x", set(), {"to": "a@else.example"}).action == "ask"
    assert p.evaluate("other", set(), {}).action == "allow"


def test_policy_defaults_and_sandbox_rule():
    p = Policy()
    assert p.evaluate("anything_send", {"send"}, {}).action == "ask"
    assert p.evaluate("sandbox_exec", {"exec"}, {}, sandbox="local").action == "ask"
    assert p.evaluate("sandbox_exec", {"exec"}, {}, sandbox="docker").action == "allow"
    assert p.evaluate("read_it", {"read"}, {}).action == "allow"


def test_policy_refuses_approve_on_timeout():
    with pytest.raises(ValueError):
        Policy.from_dict({"on_timeout": "allow"})


def test_policy_loads_shipped_file():
    from tandem.config import examples_dir

    p = Policy.load(examples_dir() / "policy.yaml")
    assert p.timeout_s == 900
    assert p.evaluate("payments_pay", {"pay"}, {"amount": 9000}).action == "deny"
    assert p.evaluate("payments_pay", {"pay"}, {"amount": 90}).action == "ask"


def test_validate_args():
    params = schema(
        {"n": {"type": "integer"}, "s": {"type": "string"}, "x": {"type": ["number", "null"]}}, ["s"]
    )
    assert validate_args(params, {"s": "a", "n": 2}) == []
    assert "missing required argument 's'" in validate_args(params, {})[0]
    assert validate_args(params, {"s": "a", "n": "two"})
    assert validate_args(params, {"s": "a", "n": True})  # bool is not an integer here
    assert validate_args(params, {"s": "a", "x": 1.5}) == []


async def test_allow_runs_and_audits():
    reg, store, *_ = make_registry()
    out = await reg.invoke("data_read", {}, ToolContext())
    assert out.ok and out.decision == "allow" and "hello" in out.text
    assert store.list_audit()[0]["decision"] == "allow"


async def test_deny_never_runs():
    p = Policy(rules=[Rule({"tags": ["pay"]}, "deny", "no money")])
    reg, store, _, sent, _ = make_registry(p)
    out = await reg.invoke("pay", {"amount": 5}, ToolContext())
    assert not out.ok and out.decision == "denied" and "no money" in out.text
    assert sent == [] and store.list_audit()[0]["decision"] == "denied"


async def test_ask_then_approve():
    reg, store, approvals, sent, _ = make_registry()
    seen = []
    task = asyncio.create_task(
        reg.invoke("email_send", {"to": "a@x.example", "body": "hi"}, ToolContext(on_approval=seen.append))
    )
    ap = await eventually(lambda: next(iter(store.list_approvals(state="pending")), None))
    assert ap["title"] == "Email a@x.example" and ap["editable"] == ["body"] and seen
    assert sent == []
    await approvals.decide(ap["id"], "approve")
    out = await task
    assert out.ok and out.decision == "approved" and sent == [{"to": "a@x.example", "body": "hi"}]
    assert store.get_approval(ap["id"])["result"]["ok"] is True


async def test_ask_then_reject_with_note():
    reg, store, approvals, sent, _ = make_registry()
    task = asyncio.create_task(reg.invoke("email_send", {"to": "a"}, ToolContext()))
    ap = await eventually(lambda: next(iter(store.list_approvals(state="pending")), None))
    await approvals.decide(ap["id"], "reject", note="not now")
    out = await task
    assert not out.ok and out.decision == "rejected" and "not now" in out.text and sent == []
    with pytest.raises(ValueError):
        await approvals.decide(ap["id"], "approve")  # already decided


async def test_edit_only_editable_fields():
    reg, store, approvals, sent, _ = make_registry()
    task = asyncio.create_task(reg.invoke("email_send", {"to": "a", "body": "v1"}, ToolContext()))
    ap = await eventually(lambda: next(iter(store.list_approvals(state="pending")), None))
    with pytest.raises(ValueError, match="not editable"):
        await approvals.decide(ap["id"], "edit", {"to": "evil@x.example"})
    await approvals.decide(ap["id"], "edit", {"to": "a", "body": "v2"})  # full args, only body changed
    out = await task
    assert out.decision == "edited" and sent[-1] == {"to": "a", "body": "v2"}


async def test_expiry_denies():
    reg, store, approvals, sent, clock = make_registry()
    task = asyncio.create_task(reg.invoke("email_send", {"to": "a"}, ToolContext()))
    ap = await eventually(lambda: next(iter(store.list_approvals(state="pending")), None))
    assert approvals.expire_due() == []
    clock.advance(seconds=approvals.policy.timeout_s + 1)
    assert [a["id"] for a in approvals.expire_due()] == [ap["id"]]
    out = await task
    assert out.decision == "expired" and sent == []
    assert store.list_audit()[0]["decision"] == "expired"


async def test_cancel_for_task_and_orphans():
    reg, store, approvals, sent, _ = make_registry()
    task = asyncio.create_task(reg.invoke("email_send", {"to": "a"}, ToolContext(task_id="tk_1")))
    await eventually(lambda: store.list_approvals(state="pending"))
    approvals.cancel_for_task("tk_1")
    out = await task
    assert out.decision == "cancelled"
    store.create_approval(tool="x", title="x", args={}, expires_at=store.now())
    approvals.cancel_orphans()
    assert store.list_approvals(state="pending") == []


async def test_errors_and_invalid_calls():
    reg, *_ = make_registry()
    assert (await reg.invoke("nope", {}, ToolContext())).decision == "invalid"
    assert (await reg.invoke("email_send", {}, ToolContext())).decision == "invalid"
    out = await reg.invoke("boom", {}, ToolContext())
    assert not out.ok and out.decision == "error" and "kaput" in out.text


async def test_secret_values_never_leave_the_gate():
    token = "tok_live_9f8e7d6c5b4a3210"
    reg, store, *_ = make_registry(env={"API_TOKEN": token})
    reg.secrets = reg.redactor.secrets
    reg.redactor.secrets.get("API_TOKEN")
    out = await reg.invoke("data_read", {}, ToolContext())
    assert token not in out.text and MASK in out.text
    assert token not in str(store.list_audit())


def test_redactor_patterns_and_keys():
    r = Redactor(SecretResolver({"MY_SERVICE_KEY": "supersecretvalue123"}))
    text = "keys: sk-ant-abcdefghijklmnopqrstu ghp_abcdefghijklmnopqrstuvwxyz0123 AKIAABCDEFGHIJKLMNOP Bearer abcdefghijklmnopqrstuv supersecretvalue123"
    out = r.text(text)
    for leaked in (
        "sk-ant-abcdefghijklmnopqrstu",
        "ghp_abcdefghijklmnopqrstuvwxyz0123",
        "AKIAABCDEFGHIJKLMNOP",
        "abcdefghijklmnopqrstuv",
        "supersecretvalue123",
    ):
        assert leaked not in out
    assert out.count(MASK) >= 5
    assert r.value({"password": "x", "nested": [{"token": "y"}], "ok": "fine"}) == {
        "password": MASK,
        "nested": [{"token": MASK}],
        "ok": "fine",
    }


async def test_check_external_gates_harness_builtins():
    reg, store, approvals, *_ = make_registry()
    ctx = ToolContext()
    task = asyncio.create_task(
        reg.check_external("claude_bash", {"exec", "send"}, {"command": "rm -rf x"}, ctx, "Claude Bash")
    )
    ap = await eventually(lambda: next(iter(store.list_approvals(state="pending")), None))
    assert ap["tool"] == "claude_bash"
    await approvals.decide(ap["id"], "reject")
    assert not (await task).ok
    assert "claude_bash" not in reg.tools


async def test_approval_expires_at_uses_policy_timeout():
    reg, store, approvals, *_ = make_registry(Policy(timeout_s=60))
    task = asyncio.create_task(reg.invoke("email_send", {"to": "a"}, ToolContext()))
    ap = await eventually(lambda: next(iter(store.list_approvals(state="pending")), None))
    from tandem.util import parse_iso

    assert parse_iso(ap["expires_at"]) - parse_iso(ap["created_at"]) == timedelta(seconds=60)
    approvals.cancel(ap["id"])
    await task
