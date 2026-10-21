"""The agent's computer (SPEC §3.10): a Docker container per task when Docker is available, otherwise a
restricted local directory. File tools are confined to the workspace; no host credentials go in.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
import sys
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from tandem.config import SandboxConfig


@dataclass
class ExecResult:
    code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    def text(self, limit: int = 6000) -> str:
        out = f"exit code {self.code}{' (timed out)' if self.timed_out else ''}\n"
        if self.stdout:
            out += f"--- stdout ---\n{self.stdout[-limit:]}\n"
        if self.stderr:
            out += f"--- stderr ---\n{self.stderr[-limit // 2 :]}\n"
        return out


class SandboxError(ValueError):
    pass


class Sandbox(ABC):
    kind: Literal["docker", "local"]

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, rel: str) -> Path:
        """Map a workspace-relative path to the host, refusing anything that escapes the root."""
        rel = (rel or ".").replace("\\", "/")
        if rel.startswith("/work/"):
            rel = rel[len("/work/") :]
        candidate = (self.root / rel.lstrip("/")).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise SandboxError(f"path {rel!r} is outside the workspace")
        return candidate

    def read(self, rel: str, limit: int = 200_000) -> str:
        path = self.resolve(rel)
        if not path.is_file():
            raise SandboxError(f"no such file: {rel}")
        return path.read_text(encoding="utf-8", errors="replace")[:limit]

    def write(self, rel: str, text: str) -> Path:
        path = self.resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def list(self, rel: str = ".") -> list[str]:
        base = self.resolve(rel)
        if not base.exists():
            return []
        out = []
        for p in sorted(base.rglob("*")):
            if p.is_file() and ".git" not in p.parts:
                out.append(p.relative_to(self.root).as_posix())
        return out[:500]

    @abstractmethod
    async def exec(self, cmd: str, timeout: float = 120) -> ExecResult: ...

    async def close(self) -> None:  # noqa: B027 - optional hook
        pass

    def public(self) -> dict[str, Any]:
        return {"kind": self.kind, "root": str(self.root)}


def _limits() -> Any:  # pragma: no cover - POSIX only, runs in the child
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    resource.setrlimit(resource.RLIMIT_FSIZE, (200 * 1024 * 1024, 200 * 1024 * 1024))
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    os.setsid()


class LocalSandbox(Sandbox):
    """A fresh directory, a scrubbed environment, timeouts and (on POSIX) resource limits.

    It cannot block network access: use Docker for untrusted work.
    """

    kind = "local"

    def env(self) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.root),
            "LANG": "C.UTF-8",
            "PYTHONUNBUFFERED": "1",
        }
        if sys.platform == "win32":
            for key in ("SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP"):
                if key in os.environ:
                    env[key] = os.environ[key]
        return env

    async def exec(self, cmd: str, timeout: float = 120) -> ExecResult:
        kwargs: dict[str, Any] = {
            "cwd": str(self.root),
            "env": self.env(),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if sys.platform != "win32":
            kwargs["preexec_fn"] = _limits
        proc = await asyncio.create_subprocess_shell(cmd, **kwargs)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
            return ExecResult(
                proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
            )
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            return ExecResult(-9, "", "", timed_out=True)


class DockerSandbox(Sandbox):
    kind = "docker"

    def __init__(self, root: Path, cfg: SandboxConfig) -> None:
        super().__init__(root)
        self.cfg = cfg
        self.container: str | None = None

    async def start(self) -> None:
        name = f"tandem-{uuid.uuid4().hex[:10]}"
        args = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--memory",
            self.cfg.memory,
            "--cpus",
            self.cfg.cpus,
            "--pids-limit",
            "256",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=256m",
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "-v",
            f"{self.root}:/work",
            "-w",
            "/work",
        ]
        if not self.cfg.network:
            args += ["--network", "none"]
        if sys.platform != "win32" and hasattr(os, "getuid"):
            args += ["--user", f"{os.getuid()}:{os.getgid()}"]
        args += [self.cfg.image, "sleep", "infinity"]
        proc = await asyncio.create_subprocess_exec(*args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _out, err = await proc.communicate()
        if proc.returncode != 0:
            raise SandboxError(f"docker run failed: {err.decode(errors='replace')[:500]}")
        self.container = name

    async def exec(self, cmd: str, timeout: float = 120) -> ExecResult:
        if not self.container:
            await self.start()
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            self.container or "",
            "sh",
            "-lc",
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
            return ExecResult(
                proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
            )
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            return ExecResult(-9, "", "", timed_out=True)

    async def close(self) -> None:
        if self.container:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", self.container, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            await proc.wait()
            self.container = None


_docker_ok: bool | None = None


def docker_available() -> bool:
    global _docker_ok
    if _docker_ok is None:
        if not shutil.which("docker"):
            _docker_ok = False
        else:
            try:
                _docker_ok = (
                    subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
                )
            except (OSError, subprocess.TimeoutExpired):
                _docker_ok = False
    return _docker_ok


class SandboxFactory:
    def __init__(self, base: Path, cfg: SandboxConfig) -> None:
        self.base = base
        self.cfg = cfg

    @property
    def mode(self) -> str:
        if self.cfg.mode == "docker" or (self.cfg.mode == "auto" and docker_available()):
            return "docker"
        return "local"

    async def create(self, task_id: str) -> Sandbox:
        root = self.base / task_id
        if self.mode == "docker":
            box = DockerSandbox(root, self.cfg)
            await box.start()
            return box
        return LocalSandbox(root)
