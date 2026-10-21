"""Tandem's own tools: memory, skills, artifacts, the sandbox, and the chat agent's start_task."""

from __future__ import annotations

from typing import Any

from tandem.tools import Tool, ToolContext, schema

SANDBOX_TOOLS = ("sandbox_write", "sandbox_read", "sandbox_list", "sandbox_exec")
CHAT_ONLY = ("start_task", "today_summary", "approvals_list")


def _box(ctx: ToolContext) -> Any:
    if ctx.sandbox is None:
        raise RuntimeError("no sandbox in this context: start a task for work that needs files or commands")
    return ctx.sandbox


async def memory_search(args: dict[str, Any], ctx: ToolContext) -> Any:
    return {
        "results": [
            {"id": m["id"], "kind": m["kind"], "content": m["content"]}
            for m in ctx.rt.memory.search(args["query"], int(args.get("k") or 5))
        ]
    }


async def memory_write(args: dict[str, Any], ctx: ToolContext) -> Any:
    m = ctx.rt.memory.write(args["content"], args.get("kind") or "fact", args.get("key"))
    return {"saved": True, "id": m["id"], "kind": m["kind"]}


async def memory_forget(args: dict[str, Any], ctx: ToolContext) -> Any:
    return {"forgotten": ctx.rt.memory.forget(args["id"])}


async def skill_load(args: dict[str, Any], ctx: ToolContext) -> Any:
    return ctx.rt.skills.load(args["name"])


async def artifact_create(args: dict[str, Any], ctx: ToolContext) -> Any:
    art = ctx.rt.artifacts.create(args["kind"], args["title"], args["content"], ctx.task_id)
    return {"id": art["id"], "filename": art["filename"], "kind": art["kind"], "size": art["size"]}


async def sandbox_write(args: dict[str, Any], ctx: ToolContext) -> Any:
    path = _box(ctx).write(args["path"], args["content"])
    return {"written": path.relative_to(_box(ctx).root).as_posix(), "bytes": path.stat().st_size}


async def sandbox_read(args: dict[str, Any], ctx: ToolContext) -> Any:
    return _box(ctx).read(args["path"])


async def sandbox_list(args: dict[str, Any], ctx: ToolContext) -> Any:
    return {"files": _box(ctx).list(args.get("path") or ".")}


async def sandbox_exec(args: dict[str, Any], ctx: ToolContext) -> Any:
    timeout = min(float(args.get("timeout_s") or ctx.rt.cfg.sandbox.exec_timeout_s), 600)
    res = await _box(ctx).exec(args["command"], timeout)
    return res.text()


async def start_task(args: dict[str, Any], ctx: ToolContext) -> Any:
    task = await ctx.rt.tasks.start(
        args["instructions"], title=args.get("title"), harness=args.get("harness"), origin="chat"
    )
    return {"task_id": task["id"], "title": task["title"], "harness": task["harness"], "state": task["state"]}


async def today_summary(args: dict[str, Any], ctx: ToolContext) -> Any:
    t = await ctx.rt.today()
    return {
        "needs_you": [{"id": a["id"], "title": a["title"]} for a in t["needs_you"]],
        "in_progress": [{"id": x["id"], "title": x["title"], "state": x["state"]} for x in t["in_progress"]],
        "done": [{"id": x["id"], "title": x["title"]} for x in t["done"]],
        "upcoming": t["upcoming"][:5],
        "brief": (t["brief"] or {}).get("greeting"),
    }


async def approvals_list(args: dict[str, Any], ctx: ToolContext) -> Any:
    return {
        "pending": [
            {"id": a["id"], "title": a["title"], "expires_at": a["expires_at"]}
            for a in ctx.rt.store.list_approvals(state="pending")
        ]
    }


def builtin_tools() -> list[Tool]:
    return [
        Tool(
            "memory_search",
            "Search long-term memory (facts, preferences, past threads).",
            schema({"query": {"type": "string"}, "k": {"type": "integer"}}, ["query"]),
            memory_search,
            title="Search memory",
        ),
        Tool(
            "memory_write",
            "Save something worth remembering. kind: core (who the user is), preference (how they like "
            "things; 'vip: a@b.com' and 'mute: sender' shape triage), fact, or note. Same key replaces.",
            schema(
                {
                    "content": {"type": "string"},
                    "kind": {"type": "string", "enum": ["core", "preference", "fact", "note"]},
                    "key": {"type": "string"},
                },
                ["content"],
            ),
            memory_write,
            tags=frozenset({"write"}),
            title="Remember",
        ),
        Tool(
            "memory_forget",
            "Delete a memory entry by id.",
            schema({"id": {"type": "string"}}, ["id"]),
            memory_forget,
            tags=frozenset({"write"}),
            title="Forget",
        ),
        Tool(
            "skill_load",
            "Load a skill's full instructions by name.",
            schema({"name": {"type": "string"}}, ["name"]),
            skill_load,
            title="Load skill",
        ),
        Tool(
            "artifact_create",
            "Create a deliverable file. kind md|html: markdown/HTML text. docx: markdown (#, -, tables). "
            'xlsx: JSON {"sheets":[{"name":..,"rows":[[..]]}]} or CSV; cells starting with = are formulas. '
            'pptx: JSON {"slides":[{"title":..,"bullets":[..],"notes":..}]}.',
            schema(
                {
                    "kind": {"type": "string", "enum": ["md", "html", "docx", "xlsx", "pptx"]},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                },
                ["kind", "title", "content"],
            ),
            artifact_create,
            tags=frozenset({"write"}),
            summarize=lambda a: f"Create {a.get('kind')}: {a.get('title')}",
            title="Create file",
        ),
        Tool(
            "sandbox_write",
            "Write a text file in your workspace.",
            schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
            sandbox_write,
            tags=frozenset({"write"}),
            summarize=lambda a: f"Write {a.get('path')}",
            title="Write file",
        ),
        Tool(
            "sandbox_read",
            "Read a text file from your workspace.",
            schema({"path": {"type": "string"}}, ["path"]),
            sandbox_read,
            title="Read file",
        ),
        Tool(
            "sandbox_list",
            "List files in your workspace.",
            schema({"path": {"type": "string"}}),
            sandbox_list,
            title="List files",
        ),
        Tool(
            "sandbox_exec",
            "Run a shell command in your workspace (python3, etc.).",
            schema({"command": {"type": "string"}, "timeout_s": {"type": "number"}}, ["command"]),
            sandbox_exec,
            tags=frozenset({"exec"}),
            summarize=lambda a: f"Run: {a.get('command')}",
            title="Run command",
        ),
        Tool(
            "start_task",
            "Start a background task for real work. It runs on its own computer and reports back.",
            schema(
                {
                    "instructions": {"type": "string"},
                    "title": {"type": "string"},
                    "harness": {"type": "string"},
                },
                ["instructions"],
            ),
            start_task,
            tags=frozenset({"read"}),
            title="Start task",
        ),
        Tool(
            "today_summary",
            "What needs the user, what is in progress, what is done today, and what's coming up.",
            schema({}),
            today_summary,
            title="Check today",
        ),
        Tool(
            "approvals_list",
            "List actions waiting for the user's approval.",
            schema({}),
            approvals_list,
            title="List approvals",
        ),
    ]
