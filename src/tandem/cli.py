"""`tandem` command line: serve the app, run a check-in or brief once, run a task, inspect config."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
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


def _pilot(args: Any, settings: Any) -> int:
    from datetime import UTC, datetime

    from tandem.config import parse_duration
    from tandem.evals.pilot import export_events, pilot_stats
    from tandem.store import Store

    store = Store(settings.db_url)
    now = datetime.now(UTC)
    since = now - parse_duration(args.since)
    if args.eval_kind == "stats":
        print(json.dumps(pilot_stats(store, since, now), indent=2))
        return 0
    lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in export_events(store, since))
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(lines, encoding="utf-8")
        print(f"wrote {lines.count(chr(10))} events to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(lines)
    return 0


async def _eval(args: Any) -> int:
    from pathlib import Path

    from tandem.config import Settings, load_config
    from tandem.evals import run_brief_eval, run_triage_eval, save_result
    from tandem.evals.report import brief_report, triage_report

    settings = Settings()
    if args.eval_kind in ("export", "stats"):
        return _pilot(args, settings)
    cfg = load_config(settings)
    common = {
        "dataset": Path(args.dataset) if args.dataset else None,
        "limit": args.limit,
        "base_url": args.base_url,
        "api_key_env": args.api_key_env,
        "max_tokens": args.max_tokens,
        "extra_body": json.loads(args.extra_body) if args.extra_body else None,
    }
    try:
        if args.eval_kind == "triage":
            result = await run_triage_eval(
                args.model, cfg, progress=lambda d, n: print(f"  triaged {d}/{n}", file=sys.stderr), **common
            )
            print(triage_report(result))
            headline = result["metrics"]["accuracy"] or 0
        else:
            result = await run_brief_eval(args.model, args.judge, cfg, **common)
            print(brief_report(result))
            headline = (result["metrics"]["judge_overall"] or 0) / 5
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    if not args.no_save:
        from tandem.store import Store

        path = save_result(Store(settings.db_url), settings.state_dir, result)
        print(f"\nsaved {path}", file=sys.stderr)
    if args.fail_under is not None and headline < args.fail_under:
        print(f"below --fail-under {args.fail_under}", file=sys.stderr)
        return 1
    return 0


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
    ev = sub.add_parser("eval", help="measure triage or brief quality on a labeled dataset")
    evsub = ev.add_subparsers(dest="eval_kind", required=True)
    for kind in ("triage", "brief"):
        e = evsub.add_parser(kind, help=f"run the {kind} eval")
        e.add_argument(
            "--model",
            default=kind,
            help="a role (triage), a profile in tandem.yaml (cheap), provider/model "
            "(anthropic/claude-haiku-5-5), mock, or noisy[:rate]",
        )
        if kind == "brief":
            e.add_argument("--judge", default="chat", help="the model that grades each brief (same forms)")
        e.add_argument("--dataset", help="a .jsonl dataset (default: examples/evals)")
        e.add_argument("--limit", type=int)
        e.add_argument("--base-url", help="for provider/model specs: the endpoint (NAI, vLLM, Ollama...)")
        e.add_argument("--api-key-env", help="for provider/model specs: the env var holding the key")
        e.add_argument("--max-tokens", type=int, help="override max_tokens (reasoning models need room)")
        e.add_argument(
            "--extra-body", help='JSON merged into each request, e.g. \'{"reasoning": {"effort": "low"}}\''
        )
        e.add_argument("--json", dest="json_out", help="also write the full result to this file")
        e.add_argument("--no-save", action="store_true", help="don't record the result for the Evals page")
        e.add_argument(
            "--fail-under", type=float, help="exit 1 if accuracy (triage) or judge score (brief) is lower"
        )
    for kind, text in (
        ("export", "write this instance's recent events as a labeling file (dataset format)"),
        ("stats", "print the pilot numbers: interruptions/day, notify latency, approvals, tasks"),
    ):
        e = evsub.add_parser(kind, help=text)
        e.add_argument("--since", default="7d", help="how far back: 24h, 7d, ...")
        if kind == "export":
            e.add_argument("--out", help="file to write (default: stdout)")
    hx = sub.add_parser("harness", help="the harness manifest quality tools grade (harness.yaml)")
    hxsub = hx.add_subparsers(dest="harness_cmd", required=True)
    he = hxsub.add_parser("export", help="write harness/tools.yaml, guardrails.yaml and agent.yaml")
    he.add_argument("--root", default=".", help="repository root holding harness.yaml (default: .)")
    he.add_argument("--check", action="store_true", help="write nothing; exit 1 if the files are stale")
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
    if args.cmd == "eval":
        return asyncio.run(_eval(args))
    if args.cmd == "harness":
        from tandem import quality

        stale = quality.write(Path(args.root), check=args.check)
        if args.check and stale:
            print("stale, run `tandem harness export`: " + ", ".join(stale), file=sys.stderr)
            return 1
        print("\n".join(stale) if stale else "harness/ is up to date")
        return 0
    result = asyncio.run(_once(args.cmd, getattr(args, "instructions", None), getattr(args, "harness", None)))
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
