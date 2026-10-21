"""Pilot helpers: turn a real week's events into a labeling file, and the numbers to watch (REAL-MODELS.md)."""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from tandem.util import iso, parse_iso

STATUS_TO_LABEL = {
    "ignored": "ignore",
    "brief": "brief",
    "briefed": "brief",
    "notified": "notify",
    "acted": "act",
}


def export_events(store: Any, since: datetime) -> list[dict[str, Any]]:
    """Events since `since` in the dataset format, labeled with what Tandem did. Correct `label` by hand."""
    rows = []
    for e in reversed(store.list_events(since=iso(since), limit=5000)):
        pred = STATUS_TO_LABEL.get(e["status"])
        if not pred:
            continue
        data = e.get("data") or {}
        rows.append(
            {
                "id": e["id"],
                "kind": e["kind"],
                "source": e["source"],
                "sender": e.get("sender"),
                "title": e["title"],
                "body": (e.get("body") or "")[:4000],
                **({"headers": data["headers"]} if data.get("headers") else {}),
                **({"labels": data["labels"]} if data.get("labels") else {}),
                "label": pred,
                "predicted": pred,
                "reason": (e.get("triage") or {}).get("reason") or "",
                "tags": [],
            }
        )
    return rows


def pilot_stats(store: Any, since: datetime, now: datetime) -> dict[str, Any]:
    """Interruptions per day, notify latency, approvals, tasks, model fallbacks: the pilot's dashboard."""
    s = iso(since) or ""
    events = store.list_events(since=s, limit=10000)
    days = max(1.0, (now - since) / timedelta(days=1))
    notified = [e for e in events if e["status"] == "notified"]
    lat = []
    for e in notified:
        occurred, seen = parse_iso(e.get("occurred_at")), parse_iso(e.get("created_at"))
        if occurred and seen and seen >= occurred:
            lat.append((seen - occurred).total_seconds() / 60)
    approvals = [a for a in store.list_approvals(limit=5000) if a["created_at"] >= s]
    decisions = Counter(a["state"] for a in approvals)
    edited = sum(1 for r in store.list_audit(limit=5000) if r["ts"] >= s and r["decision"] == "edited")
    tasks = [t for t in store.list_tasks(limit=5000) if t["created_at"] >= s]
    warns = [a for a in store.list_activity(limit=5000) if a["ts"] >= s and a["level"] == "warn"]
    by_status = Counter(e["status"] for e in events)
    return {
        "since": s,
        "days": round(days, 1),
        "events": len(events),
        "by_status": dict(by_status),
        "interruptions_per_day": round(len(notified) / days, 1),
        "notify_latency_min": {
            "median": round(statistics.median(lat), 1) if lat else None,
            "p90": round(sorted(lat)[int(0.9 * (len(lat) - 1))], 1) if lat else None,
        },
        "approvals": {"total": len(approvals), **dict(decisions), "edited": edited},
        "tasks": dict(Counter(t["state"] for t in tasks)),
        "triage_fallbacks": len([w for w in warns if w["kind"] == "triage"]),
        "warnings": len(warns),
    }
