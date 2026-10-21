"""Harness adapters. Real SDKs are used when installed (CI's extras job); a fake module covers the
Claude Agent SDK wiring, and a fake `claude` binary covers the CLI harness.
"""

from __future__ import annotations

import os
import stat
import sys
import types
from pathlib import Path

import pytest

from tandem.harness import HARNESSES, describe, get_harness
from tests.conftest import eventually

MEMO = "Make a short memo about the cash position this week"


def test_registry_lists_all_harnesses():
    names = {h["name"] for h in describe("builtin")}
    assert {"builtin", "deepagents", "claude-sdk", "openai-agents", "cli"} <= names
    default = next(h for h in describe("builtin") if h["default"])
    assert default["name"] == "builtin" and default["available"]
    with pytest.raises(KeyError):
        get_harness("nope")


async def test_builtin_steer_while_waiting_then_approve(rt):
    task = await rt.tasks.start("Prepare a cash summary for this week and email it to Sam.")
    ap = await eventually(lambda: next(iter(rt.store.list_approvals(state="pending")), None))
    rt.tasks.steer(task["id"], "Mention the overdue Oakridge invoice")
    await rt.approvals.decide(ap["id"], "approve")
    done = await rt.tasks.wait(task["id"], 20)
    assert done["state"] == "done"
    assert "Applied your note: Mention the overdue Oakridge invoice" in done["summary"]
    kinds = [log["kind"] for log in rt.store.task_logs(task["id"])]
    assert "steer" in kinds and "tool" in kinds


async def test_stop_cancels_task_and_its_approvals(rt):
    task = await rt.tasks.start("Prepare a cash summary and email it to Sam.")
    ap = await eventually(lambda: next(iter(rt.store.list_approvals(state="pending")), None))
    stopped = await rt.tasks.stop(task["id"])
    assert stopped["state"] == "stopped"
    assert rt.store.get_approval(ap["id"])["state"] == "cancelled"
    assert not rt.outbox()
    with pytest.raises(ValueError):
        rt.tasks.steer(task["id"], "too late")


async def test_unknown_or_unavailable_harness_is_refused(rt, monkeypatch):
    with pytest.raises(ValueError, match="unknown harness"):
        await rt.tasks.start("x", harness="nope")
    monkeypatch.setattr(HARNESSES["cli"], "availability", lambda self: (False, "missing"))
    with pytest.raises(ValueError, match="not available"):
        await rt.tasks.start("x", harness="cli")


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX shell script as a fake CLI")
async def test_cli_harness_with_fake_claude(rt, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(
        "#!/bin/sh\necho 'reading the task'\nprintf '# Report\\n\\nAll good.\\n' > report.md\necho 'wrote report.md'\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert get_harness("cli").availability()[0]
    task = await rt.tasks.start("Write a short report", harness="cli")
    ap = await eventually(lambda: next(iter(rt.store.list_approvals(state="pending")), None))
    assert ap["tool"] == "cli_run" and ap["args"]["cli"] == "claude"
    await rt.approvals.decide(ap["id"], "approve")
    done = await rt.tasks.wait(task["id"], 20)
    assert done["state"] == "done" and "wrote report.md" in done["summary"]
    art = rt.store.get_artifact(done["artifact_ids"][0])
    assert art["filename"] == "report.md" and "All good." in Path(art["path"]).read_text()


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX shell script as a fake CLI")
async def test_cli_harness_rejected_run_does_nothing(rt, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "ran"
    fake = bin_dir / "codex"
    fake.write_text(f"#!/bin/sh\ntouch {marker}\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_dir))
    rt.cfg.harness.cli_command = "codex"
    task = await rt.tasks.start("Write code", harness="cli")
    ap = await eventually(lambda: next(iter(rt.store.list_approvals(state="pending")), None))
    await rt.approvals.decide(ap["id"], "reject")
    done = await rt.tasks.wait(task["id"], 20)
    assert done["state"] == "failed" and not marker.exists()


def _fake_claude_sdk() -> types.ModuleType:
    mod = types.ModuleType("claude_agent_sdk")
    seen: dict = {"prompts": []}

    class Allow:
        pass

    class Deny:
        def __init__(self, message=""):
            self.message = message

    class SdkTool:
        def __init__(self, name, desc, schema, handler):
            self.name, self.handler = name, handler

    def tool(name, desc, schema):
        return lambda fn: SdkTool(name, desc, schema, fn)

    class Options:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            seen["options"] = self

    class Block:
        def __init__(self, text):
            self.text = text

    class Msg:
        def __init__(self, text=None, result=None):
            self.content = [Block(text)] if text else []
            self.result = result

    class Client:
        def __init__(self, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def query(self, prompt):
            seen["prompts"].append(prompt)

        async def receive_response(self):
            server = self.options.mcp_servers["tandem"]
            by = {t.name: t for t in server["tools"]}
            out = await by["artifact_create"].handler({"kind": "md", "title": "Memo", "content": "# Memo"})
            seen["tool_result"] = out
            perm = await self.options.can_use_tool("Read", {"path": "x"}, None)
            seen["read_perm"] = perm
            yield Msg(text="Working on it")
            yield Msg(result="Memo written.")

    mod.tool = tool
    mod.create_sdk_mcp_server = lambda name, version, tools: {"name": name, "tools": tools}
    mod.ClaudeAgentOptions = Options
    mod.ClaudeSDKClient = Client
    mod.PermissionResultAllow = Allow
    mod.PermissionResultDeny = Deny
    mod.seen = seen
    return mod


async def test_claude_sdk_harness_wiring(rt, monkeypatch):
    fake = _fake_claude_sdk()
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake)
    assert get_harness("claude-sdk").availability()[0]
    task = await rt.tasks.start(MEMO, harness="claude-sdk")
    done = await rt.tasks.wait(task["id"], 20)
    assert done["state"] == "done" and done["summary"] == "Memo written."
    opts = fake.seen["options"]
    assert Path(opts.cwd) == Path(rt.settings.state_dir) / "sandboxes" / task["id"]
    assert "mcp__tandem__artifact_create" in opts.allowed_tools and "Bash" in opts.allowed_tools
    assert fake.seen["tool_result"]["is_error"] is False
    assert type(fake.seen["read_perm"]).__name__ == "Allow"
    assert done["artifact_ids"]
    assert any(a["tool"] == "claude_read" for a in rt.store.list_audit())


async def test_deepagents_harness_real(rt):
    pytest.importorskip("deepagents")
    task = await rt.tasks.start(MEMO, harness="deepagents")
    done = await rt.tasks.wait(task["id"], 60)
    assert done["state"] == "done", done
    assert rt.store.get_artifact(done["artifact_ids"][0])["kind"] == "docx"


async def test_openai_agents_harness_real(rt):
    pytest.importorskip("agents")
    task = await rt.tasks.start(MEMO, harness="openai-agents")
    done = await rt.tasks.wait(task["id"], 60)
    assert done["state"] == "done", done
    assert done["artifact_ids"]


async def test_openai_bridge_round_trip(rt):
    import httpx

    from tandem.models.openai_bridge import ChatCompletionsBridge

    async with httpx.AsyncClient(
        transport=ChatCompletionsBridge(rt.models.for_role("chat")), base_url="http://b/v1"
    ) as c:
        r = await c.post(
            "/chat/completions",
            json={
                "model": "x",
                "messages": [{"role": "user", "content": "remember I like tea"}],
                "tools": [{"type": "function", "function": {"name": "memory_write", "parameters": {}}}],
            },
        )
        body = r.json()
        call = body["choices"][0]["message"]["tool_calls"][0]
        assert (
            call["function"]["name"] == "memory_write" and body["choices"][0]["finish_reason"] == "tool_calls"
        )
        assert (await c.post("/embeddings", json={})).status_code == 404
        assert (await c.post("/chat/completions", json={"stream": True, "messages": []})).status_code == 400
