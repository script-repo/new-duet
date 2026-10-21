"""Task manager (SPEC §3.7): queue, run in a sandbox with a harness, steer, stop, report back."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from tandem.engine import prompts
from tandem.engine.builtin_tools import CHAT_ONLY
from tandem.harness import HARNESSES, HarnessContext, TaskSpec, get_harness
from tandem.tools import GatedTools, ToolContext

log = logging.getLogger("tandem.tasks")


def _title(instructions: str) -> str:
    first = " ".join(instructions.split())
    return first if len(first) <= 70 else first[:69].rstrip() + "…"


class TaskManager:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self._running: dict[str, asyncio.Task[None]] = {}
        self._steer: dict[str, asyncio.Queue[str]] = {}
        self._sem: asyncio.Semaphore | None = None

    @property
    def sem(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(max(1, self.rt.cfg.harness.max_concurrent))
        return self._sem

    async def start(
        self, instructions: str, title: str | None = None, harness: str | None = None, origin: str = "user"
    ) -> dict[str, Any]:
        harness = harness or self.rt.cfg.harness.default
        if harness not in HARNESSES:
            raise ValueError(f"unknown harness {harness!r}; known: {', '.join(sorted(HARNESSES))}")
        ok, reason = get_harness(harness).availability()
        if not ok:
            raise ValueError(f"harness {harness!r} is not available: {reason}")
        task = self.rt.store.create_task(title or _title(instructions), instructions, harness, origin)
        self._steer[task["id"]] = asyncio.Queue()
        self.rt.store.add_activity(
            "task", f"Task queued: {task['title']}", f"harness: {harness}, from: {origin}", task["id"]
        )
        self.rt.store.add_message(
            "agent", f"Working on it: {task['title']}", {"type": "task", "ref": task["id"]}
        )
        self._running[task["id"]] = asyncio.create_task(self._run(task))
        return task

    async def wait(self, task_id: str, timeout: float = 60) -> dict[str, Any]:
        t = self._running.get(task_id)
        if t:
            await asyncio.wait_for(asyncio.shield(t), timeout)
        task = self.rt.store.get_task(task_id)
        assert task
        return task

    async def wait_all(self, timeout: float = 60) -> None:
        if self._running:
            await asyncio.wait_for(
                asyncio.gather(*list(self._running.values()), return_exceptions=True), timeout
            )

    def steer(self, task_id: str, text: str) -> dict[str, Any]:
        task = self.rt.store.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        if task["state"] not in ("queued", "running", "waiting"):
            raise ValueError(f"task is {task['state']}")
        self._steer.setdefault(task_id, asyncio.Queue()).put_nowait(text)
        self.rt.store.add_task_log(task_id, "steer", text)
        self.rt.store.add_activity("task", f"You steered: {task['title']}", text, task_id)
        return task

    async def stop(self, task_id: str) -> dict[str, Any]:
        task = self.rt.store.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        running = self._running.get(task_id)
        # Cancel the run before its approvals: resolving a pending approval first would let the task
        # resume and finish in the gap.
        if running and not running.done():
            running.cancel()
        self.rt.approvals.cancel_for_task(task_id)
        if running and not running.done():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await running
        task = self.rt.store.get_task(task_id)
        assert task
        if task["state"] in ("queued", "running", "waiting"):
            task = self.rt.store.update_task(task_id, state="stopped", finished_at=self.rt.store.now())
        return task  # type: ignore[return-value]

    def worker_tools(self) -> list[Any]:
        return [t for t in self.rt.registry.tools.values() if t.name not in CHAT_ONLY]

    def system_prompt(self) -> str:
        cfg = self.rt.cfg
        persona = prompts.PERSONA.format(user=cfg.user_name or "the user")
        return prompts.WORKER.format(
            persona=persona,
            core=self.rt.memory.core_block(),
            skills=self.rt.skills.index(),
            rules=prompts.RULES,
            extra=cfg.persona,
        )

    async def _run(self, task: dict[str, Any]) -> None:
        rt = self.rt
        tid = task["id"]
        sandbox = None
        async with self.sem:
            try:
                sandbox = await rt.sandboxes.create(tid)
                rt.store.update_task(tid, state="running", started_at=rt.store.now(), sandbox=sandbox.kind)
                rt.store.add_activity(
                    "task", f"Started: {task['title']}", f"{task['harness']} in a {sandbox.kind} sandbox", tid
                )

                def on_approval(ap: dict[str, Any]) -> None:
                    rt.store.update_task(tid, state="waiting")

                ctx = ToolContext(
                    task_id=tid, origin=task["origin"], sandbox=sandbox, rt=rt, on_approval=on_approval
                )
                gated = GatedTools(rt.registry, self.worker_tools(), ctx)
                steps = {"n": 0}

                async def log_fn(kind: str, text: str) -> None:
                    text = rt.redactor.text(text)
                    rt.store.add_task_log(tid, kind, text)
                    if kind == "tool":
                        steps["n"] += 1
                        rt.store.update_task(tid, steps=steps["n"], state="running")
                    elif kind == "result":
                        current = rt.store.get_task(tid)
                        if current and current["state"] == "waiting":
                            rt.store.update_task(tid, state="running")

                hctx = HarnessContext(
                    model=rt.models.for_role("worker"),
                    profile=rt.models.profile("worker"),
                    tools=gated,
                    sandbox=sandbox,
                    system_prompt=self.system_prompt(),
                    log=log_fn,
                    steer=self._steer.setdefault(tid, asyncio.Queue()),
                    max_steps=rt.cfg.harness.max_steps,
                    secrets=rt.secrets,
                    options={
                        "cli_command": rt.cfg.harness.cli_command,
                        "cli_timeout_s": rt.cfg.harness.cli_timeout_s,
                        **rt.harness_options,
                    },
                )
                spec = TaskSpec(tid, task["title"], task["instructions"], task["harness"], task["origin"])
                result = await get_harness(task["harness"]).run(spec, hctx)
                for path in hctx.options.get("produced_files", []):
                    rt.artifacts.register_file(Path(path), tid)
                summary = rt.redactor.text(result.summary)[:4000]
                state = "done" if result.ok else "failed"
                final = rt.store.update_task(
                    tid,
                    state=state,
                    summary=summary,
                    finished_at=rt.store.now(),
                    steps=max(steps["n"], int(hctx.options.get("steps") or 0)),
                    error=None if result.ok else summary[:500],
                )
                assert final
                rt.store.add_activity(
                    "task",
                    f"{'Done' if result.ok else 'Failed'}: {task['title']}",
                    summary[:300],
                    tid,
                    "notify" if result.ok else "error",
                )
                await rt.post_task_done(final)
            except asyncio.CancelledError:
                rt.store.update_task(tid, state="stopped", finished_at=rt.store.now())
                rt.store.add_activity("task", f"Stopped: {task['title']}", None, tid, "warn")
                rt.store.add_message(
                    "agent", f"Stopped “{task['title']}” as you asked.", {"type": "task", "ref": tid}
                )
                raise
            except Exception as exc:
                log.exception("task %s failed", tid)
                msg = rt.redactor.text(f"{type(exc).__name__}: {exc}")[:1000]
                rt.store.update_task(tid, state="failed", error=msg, finished_at=rt.store.now())
                rt.store.add_task_log(tid, "error", msg)
                rt.store.add_activity("task", f"Failed: {task['title']}", msg, tid, "error")
                rt.store.add_message(
                    "agent", f"I couldn't finish “{task['title']}”: {msg}", {"type": "task", "ref": tid}
                )
            finally:
                if sandbox:
                    with contextlib.suppress(Exception):
                        await sandbox.close()
                self._running.pop(tid, None)
                self._steer.pop(tid, None)
