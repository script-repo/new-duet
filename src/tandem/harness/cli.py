"""CLI coding-agent harness: Claude Code (`claude -p`) or Codex CLI (`codex exec`) in the sandbox folder.

The whole run is one gated tool, `cli_run` (tag `exec`), so the user approves it on a card unless
the policy says otherwise. The CLI only sees the task's workspace directory; files it leaves there
with document extensions are added to the library. Steering is not supported (stop works).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess

from tandem.harness.base import Harness, HarnessContext, HarnessResult, TaskSpec, register_harness

AUTH_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "CODEX_HOME",
    "CLAUDE_CONFIG_DIR",
)


def pick_cli(preference: str = "auto") -> tuple[str, str] | None:
    """Returns (kind, path) for the first available CLI."""
    order = ["claude", "codex"] if preference == "auto" else [preference]
    for kind in order:
        path = shutil.which(kind)
        if path:
            return kind, path
    return None


def command_for(kind: str, path: str, prompt: str, model: str | None = None) -> list[str]:
    if kind == "claude":
        cmd = [
            path,
            "-p",
            prompt,
            "--output-format",
            "text",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            "Read,Write,Edit,Glob,Grep,Bash",
        ]
        if model:
            cmd += ["--model", model]
        return cmd
    cmd = [path, "exec", "--sandbox", "workspace-write", "--skip-git-repo-check", prompt]
    if model:
        cmd[2:2] = ["--model", model]
    return cmd


@register_harness
class CliHarness(Harness):
    name = "cli"
    display_name = "CLI coding agent"
    description = "Runs Claude Code or Codex CLI inside the task's workspace folder."

    def availability(self) -> tuple[bool, str]:
        found = pick_cli()
        return (True, f"found {found[0]}") if found else (False, "neither `claude` nor `codex` is on PATH")

    async def run(self, task: TaskSpec, ctx: HarnessContext) -> HarnessResult:
        pref = ctx.options.get("cli_command", "auto")
        found = pick_cli(pref)
        if not found:
            return HarnessResult("No coding CLI found (install Claude Code or Codex CLI).", ok=False)
        kind, path = found
        prompt = (
            f"{ctx.system_prompt}\n\n# Task\n{task.instructions}\n\nWork only inside the current directory. "
            "Save deliverables as files here (.md, .html, .docx, .xlsx, .pptx, .csv)."
        )
        model = ctx.options.get("cli_model")
        cmd = command_for(kind, path, prompt, model)
        gate = await ctx.tools.check_external(
            "cli_run",
            {"exec"},
            {"cli": kind, "task": task.title, "folder": str(ctx.sandbox.root)},
            title=f"Run {kind} in the task workspace",
        )
        if not gate.ok:
            return HarnessResult(gate.text, ok=False)
        before = {p: p.stat().st_mtime for p in ctx.sandbox.root.rglob("*") if p.is_file()}
        env = {
            k: v
            for k, v in os.environ.items()
            if k in ("PATH", "HOME", "USERPROFILE", "APPDATA", "LANG", "SYSTEMROOT", "TMP", "TEMP")
        }
        for k in AUTH_ENV:  # the CLI's own login; never shown to a model by Tandem
            if os.environ.get(k):
                env[k] = os.environ[k]
        await ctx.log("info", f"running {kind} in {ctx.sandbox.root.name}")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(ctx.sandbox.root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        lines: list[str] = []
        try:

            async def pump() -> None:
                assert proc.stdout
                async for raw in proc.stdout:
                    line = raw.decode(errors="replace").rstrip()
                    if line:
                        lines.append(line)
                        await ctx.log("thought", line[:500])

            await asyncio.wait_for(pump(), timeout=float(ctx.options.get("cli_timeout_s", 1800)))
            code = await proc.wait()
        except (TimeoutError, asyncio.CancelledError):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            raise
        produced = [
            p
            for p in ctx.sandbox.root.rglob("*")
            if p.is_file()
            and before.get(p) != p.stat().st_mtime
            and p.suffix.lower() in (".md", ".html", ".docx", ".xlsx", ".pptx", ".csv", ".pdf")
        ]
        ctx.options["produced_files"] = [str(p) for p in produced]
        tail = "\n".join(lines[-15:])
        return HarnessResult(summary=tail or f"{kind} exited with code {code}", ok=code == 0)
