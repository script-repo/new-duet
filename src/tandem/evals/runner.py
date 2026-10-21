"""Run the real triage and brief pipelines on labeled datasets and measure them (SPEC §15.5)."""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from tandem.config import AppConfig, ConnectorConfig, ModelProfile, Settings, examples_dir
from tandem.evals.metrics import LABELS, coverage, triage_metrics
from tandem.evals.models import build_eval_client, judge_messages, resolve_profile
from tandem.models.base import ModelClient
from tandem.util import FakeClock, iso, parse_iso

JUDGE_CRITERIA = ("coverage", "prioritization", "faithfulness", "concision", "actionability")


def evals_dir() -> Path:
    return examples_dir() / "evals"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{n}: {exc}") from exc
    return rows


def load_triage_dataset(path: Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = path or evals_dir() / "triage.jsonl"
    rows = load_jsonl(path)
    for r in rows:
        if r.get("label") not in LABELS:
            raise ValueError(f"{path}: item {r.get('id')!r} has label {r.get('label')!r}; expected {LABELS}")
    meta_path = path.with_name(path.stem + ".meta.yaml")
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    return rows, meta or {}


@dataclass
class _Harness:
    """A throwaway runtime around the real engine: in-memory store, no connectors polled."""

    rt: Any
    tmp: tempfile.TemporaryDirectory[str]

    def close(self) -> None:
        self.rt.store.engine.dispose()
        self.tmp.cleanup()


def _runtime(meta: dict[str, Any], clients: dict[str, ModelClient]) -> _Harness:
    from tandem.runtime import Runtime

    tmp = tempfile.TemporaryDirectory(prefix="tandem-eval-")
    cfg = AppConfig(
        models={"eval": ModelProfile(name="eval", provider="mock", model="mock-eval")},
        connectors=[ConnectorConfig(type="webhook", name="eval")],
        demo_autoplay=False,
        user_name=str(meta.get("user_name") or ""),
    )
    cfg.schedule.timezone = str(meta.get("timezone") or "UTC")
    cfg.sandbox.mode = "local"
    now = parse_iso(str(meta["now"])) if meta.get("now") else None
    settings = Settings(data_dir=Path(tmp.name), database_url="sqlite://", mock=False, autostart=False)
    rt = Runtime(settings, cfg, FakeClock(now) if now else None, env={})
    for m in meta.get("memory") or []:
        rt.memory.write(m["content"], m.get("kind", "fact"), m.get("key"), source="eval")
    rt.models._clients.update(clients)
    return _Harness(rt, tmp)


def _usage(clients: dict[str, ModelClient]) -> dict[str, int]:
    return {
        "input_tokens": sum(c.usage.input_tokens for c in clients.values()),
        "output_tokens": sum(c.usage.output_tokens for c in clients.values()),
    }


async def run_triage_eval(
    model: str,
    cfg: AppConfig,
    *,
    dataset: Path | None = None,
    limit: int | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    max_tokens: int | None = None,
    extra_body: dict[str, Any] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Triage every item with rules + `model` + combine, exactly as a check-in would, and score it."""
    rows, meta = load_triage_dataset(dataset)
    rows = rows[:limit] if limit else rows
    opts: dict[str, Any] = {
        "base_url": base_url,
        "api_key_env": api_key_env,
        "max_tokens": max_tokens,
        "extra_body": extra_body,
    }
    client = build_eval_client(model, cfg, "triage", transport=transport, **opts)
    h = _runtime(meta, {"triage": client})
    started = time.perf_counter()
    try:
        store = h.rt.store
        events = []
        for r in rows:
            data: dict[str, Any] = {"headers": r.get("headers") or {}}
            if r.get("labels"):
                data["labels"] = r["labels"]
            ev = store.add_event(
                {
                    "source": r.get("source") or "eval",
                    "kind": r.get("kind") or "email",
                    "external_id": r["id"],
                    "title": r.get("title") or "",
                    "body": r.get("body") or "",
                    "sender": r.get("sender"),
                    "occurred_at": iso(h.rt.clock.now()),
                    "data": data,
                }
            )
            events.append(ev)
        by_event = {e["id"]: r for e, r in zip(events, rows, strict=True)}
        verdicts = []
        for start in range(0, len(events), 20):
            verdicts += await h.rt.triage.run(events[start : start + 20])
            if progress:
                progress(min(start + 20, len(events)), len(events))
        warnings = [a["detail"] for a in store.list_activity(limit=50) if a["level"] == "warn"]
    finally:
        h.close()
    elapsed = time.perf_counter() - started
    items = []
    for v in verdicts:
        r = by_event[v.event_id]
        items.append(
            {
                "id": r["id"],
                "title": r.get("title"),
                "kind": r.get("kind"),
                "gold": r["label"],
                "pred": v.action,
                "urgency": v.urgency,
                "reason": v.reason,
                "decided_by": v.source,
                "gold_reason": r.get("reason"),
                "tags": r.get("tags") or [],
            }
        )
    items.sort(key=lambda i: i["id"])
    profile = resolve_profile(model, cfg, **opts)
    return {
        "kind": "triage",
        "model": model,
        "provider": profile.provider,
        "model_name": profile.model,
        "dataset": str(meta.get("name") or (dataset.name if dataset else "triage")),
        "ran_at": iso(datetime.now().astimezone()),
        "seconds": round(elapsed, 2),
        "usage": _usage({"triage": client}),
        "model_failures": len(warnings),
        "model_failure_details": [w[:300] for w in warnings[:5]],
        "metrics": triage_metrics(items),
        "items": items,
    }


async def run_brief_eval(
    model: str,
    judge: str,
    cfg: AppConfig,
    *,
    dataset: Path | None = None,
    limit: int | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    max_tokens: int | None = None,
    extra_body: dict[str, Any] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Write each scenario's brief with the real brief prompt, then grade it with a second model."""
    path = dataset or evals_dir() / "briefs.jsonl"
    scenarios = load_jsonl(path)[: limit or None]
    _, meta = load_triage_dataset()
    opts: dict[str, Any] = {
        "base_url": base_url,
        "api_key_env": api_key_env,
        "max_tokens": max_tokens,
        "extra_body": extra_body,
    }
    writer = build_eval_client(model, cfg, "brief", transport=transport, **opts)
    # a provider/model judge shares the endpoint options (same gateway, e.g. NAI or OpenRouter)
    grader = build_eval_client(judge, cfg, "chat", transport=transport, **(opts if "/" in judge else {}))
    h = _runtime(meta, {"brief": writer, "judge": grader})
    started = time.perf_counter()
    results = []
    try:
        for sc in scenarios:
            data = {
                "kind": "brief",
                "date": str(meta.get("now", ""))[:10],
                "user_name": h.rt.cfg.user_name,
                "draft_tool": None,
                "events": [
                    {"id": f"{sc['id']}-e{n}", **e}
                    for n, e in enumerate(sc.get("events") or [])
                    if e.get("status") != "ignored"  # the real brief never sees ignored events
                ],
                "calendar": sc.get("calendar") or [],
                "approvals": sc.get("approvals") or [],
                "tasks_running": sc.get("tasks_running") or [],
                "tasks_done": sc.get("tasks_done") or [],
            }
            try:
                brief = await writer.complete_json(h.rt.brief.messages(data))
            except Exception as exc:
                results.append({"id": sc["id"], "title": sc.get("title"), "error": f"brief failed: {exc}"})
                continue
            text = " ".join(
                [str(brief.get("greeting", ""))]
                + [f"{i.get('topic', '')} {i.get('summary', '')}" for i in brief.get("items") or []]
            )
            hit, missing = coverage(text, sc.get("must_mention") or [])
            leaks = [g for g in sc.get("must_not_mention") or [] if coverage(text, [g])[0]]
            try:
                verdict = await grader.complete_json(judge_messages(sc, brief))
                scores = {
                    k: int(verdict.get("scores", {}).get(k, 0))
                    for k in JUDGE_CRITERIA
                    if str(verdict.get("scores", {}).get(k, "")).strip().isdigit()
                }
                notes = str(verdict.get("notes") or "")[:400]
            except Exception as exc:
                scores, notes = {}, f"judge failed: {exc}"
            results.append(
                {
                    "id": sc["id"],
                    "title": sc.get("title"),
                    "coverage": f"{hit}/{len(sc.get('must_mention') or [])}",
                    "missing": missing,
                    "leaks": leaks,
                    "scores": scores,
                    "notes": notes,
                    "brief": brief,
                }
            )
    finally:
        h.close()
    graded = [r for r in results if r.get("scores")]
    total_groups = sum(len(sc.get("must_mention") or []) for sc in scenarios)
    covered = sum(int(r["coverage"].split("/")[0]) for r in results if "coverage" in r)
    means = {
        k: round(sum(r["scores"].get(k, 0) for r in graded) / len(graded), 2) if graded else None
        for k in JUDGE_CRITERIA
    }
    overall = [v for v in means.values() if v is not None]
    return {
        "kind": "brief",
        "model": model,
        "judge": judge,
        "dataset": path.stem,
        "ran_at": iso(datetime.now().astimezone()),
        "seconds": round(time.perf_counter() - started, 2),
        "usage": _usage({"brief": writer, "judge": grader}),
        "metrics": {
            "n": len(scenarios),
            "errors": len([r for r in results if r.get("error")]),
            "coverage": round(covered / total_groups, 4) if total_groups else None,
            "leaks": sum(len(r.get("leaks") or []) for r in results),
            "judge_means": means,
            "judge_overall": round(sum(overall) / len(overall), 2) if overall else None,
        },
        "items": results,
    }


# ---- results ------------------------------------------------------------------------------------------------

KV_LATEST = "eval:latest:{kind}"
KV_HISTORY = "eval:history"


def summary(result: dict[str, Any]) -> dict[str, Any]:
    """The part the UI shows: metrics without per-item rows, except the misses worth looking at."""
    out = {k: v for k, v in result.items() if k != "items"}
    if result["kind"] == "triage":
        wrong = [i for i in result["items"] if i["gold"] != i["pred"]]
        out["errors"] = wrong[:60]
    else:
        out["scenarios"] = [{k: v for k, v in i.items() if k != "brief"} for i in result["items"]]
    return out


def save_result(store: Any, data_dir: Path, result: dict[str, Any]) -> Path:
    """Write the full result under `<data>/evals/` and keep the latest summary per kind in `kv`."""
    folder = data_dir / "evals"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (result.get("ran_at") or "")[:19].replace(":", "").replace("-", "")
    path = folder / f"{result['kind']}-{stamp or 'run'}.json"
    path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    store.kv_set(KV_LATEST.format(kind=result["kind"]), summary(result))
    hist = store.kv_get(KV_HISTORY, []) or []
    m = result["metrics"]
    hist.insert(
        0,
        {
            "kind": result["kind"],
            "model": result["model"],
            "ran_at": result.get("ran_at"),
            "headline": m.get("accuracy") if result["kind"] == "triage" else m.get("judge_overall"),
            "missed_urgent": m.get("missed_urgent"),
        },
    )
    store.kv_set(KV_HISTORY, hist[:30])
    return path


def latest(store: Any) -> dict[str, Any]:
    return {
        "triage": store.kv_get(KV_LATEST.format(kind="triage")),
        "brief": store.kv_get(KV_LATEST.format(kind="brief")),
        "history": store.kv_get(KV_HISTORY, []) or [],
    }
