"""`tandem` command line: serve the app, run a check-in or brief once, run a task, inspect config."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from tandem import __version__


def _runtime() -> Any:
    from tandem.runtime import Runtime

    return Runtime()


async def _once(kind: str, text: str | None = None, harness: str | None = None) -> Any:
    rt = _runtime()
    await rt.start(run_scheduler=False)
    try:
        if kind == "checkin":
            return await rt.checkin()
        if kind == "brief":
            b = await rt.brief.run()
            return {"greeting": b["greeting"], "items": b["items"]}
        if kind == "task":
            task = await rt.tasks.start(text or "", harness=harness)
            done = await rt.tasks.wait(task["id"], timeout=3600)
            return {k: done[k] for k in ("id", "title", "state", "summary", "artifact_ids")}
        if kind == "config":
            return rt.public_config()
    finally:
        await rt.stop()
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="tandem", description="Tandem: a proactive agent that gets you back to what needs you."
    )
    p.add_argument("--version", action="version", version=f"tandem {__version__}")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="run the web app and the background scheduler")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument(
        "--no-scheduler", action="store_true", help="serve the UI/API without background check-ins"
    )
    s.add_argument("--forwarded-allow-ips", default=None)
    sub.add_parser("checkin", help="run one check-in now and print the result")
    sub.add_parser("brief", help="generate the morning brief now")
    t = sub.add_parser("task", help="run one task to completion (approvals still apply: approve in the UI)")
    t.add_argument("instructions")
    t.add_argument("--harness")
    sub.add_parser("config", help="print the effective configuration (no secrets)")
    sub.add_parser("harnesses", help="list harnesses and whether they are available")
    args = p.parse_args(argv)

    if args.cmd in (None, "serve"):
        import uvicorn

        from tandem.config import Settings
        from tandem.runtime import Runtime
        from tandem.server.app import create_app

        settings = Settings()
        if getattr(args, "no_scheduler", False):
            settings.autostart = False
        rt = Runtime(settings)
        host = getattr(args, "host", None) or settings.host
        port = getattr(args, "port", None) or settings.port
        print(
            f"Tandem {__version__} ({'mock' if rt.cfg.mock else 'live'} mode) on http://{host}:{port}",
            file=sys.stderr,
        )
        if host not in ("127.0.0.1", "localhost", "::1") and not settings.auth_token:
            print(
                "WARNING: listening beyond localhost with no TANDEM_AUTH_TOKEN; anyone who can reach this "
                "port can use your assistant.",
                file=sys.stderr,
            )
        kwargs: dict[str, Any] = {"log_level": settings.log_level}
        if getattr(args, "forwarded_allow_ips", None):
            kwargs["forwarded_allow_ips"] = args.forwarded_allow_ips
            kwargs["proxy_headers"] = True
        uvicorn.run(create_app(rt), host=host, port=port, **kwargs)
        return 0
    if args.cmd == "harnesses":
        from tandem.harness import describe

        for h in describe("builtin"):
            print(f"{h['name']:15} {'yes' if h['available'] else 'no ':4} {h['reason']}")
        return 0
    result = asyncio.run(_once(args.cmd, getattr(args, "instructions", None), getattr(args, "harness", None)))
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
