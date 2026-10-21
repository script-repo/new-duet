"""Classification metrics for triage evals. Pure functions, no dependencies, tested by hand-made cases."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

LABELS = ("ignore", "brief", "notify", "act")
INTERRUPT = frozenset({"notify", "act"})  # "needs you now": the outcomes that interrupt or start work


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def _f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None:
        return None
    return round(2 * p * r / (p + r), 4) if (p + r) else 0.0


def confusion(gold: Sequence[str], pred: Sequence[str], labels: Sequence[str] = LABELS) -> list[list[int]]:
    """`m[i][j]` = items whose gold label is `labels[i]` and prediction is `labels[j]`."""
    index = {lab: i for i, lab in enumerate(labels)}
    m = [[0] * len(labels) for _ in labels]
    for g, p in zip(gold, pred, strict=True):
        m[index[g]][index[p]] += 1
    return m


def per_label(gold: Sequence[str], pred: Sequence[str], labels: Sequence[str] = LABELS) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for lab in labels:
        tp = sum(1 for g, p in zip(gold, pred, strict=True) if g == lab and p == lab)
        fp = sum(1 for g, p in zip(gold, pred, strict=True) if g != lab and p == lab)
        fn = sum(1 for g, p in zip(gold, pred, strict=True) if g == lab and p != lab)
        prec, rec = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
        out[lab] = {"precision": prec, "recall": rec, "f1": _f1(prec, rec), "support": tp + fn}
    return out


def binary(gold: Sequence[str], pred: Sequence[str], positive: Iterable[str] = INTERRUPT) -> dict[str, Any]:
    pos = frozenset(positive)
    tp = sum(1 for g, p in zip(gold, pred, strict=True) if g in pos and p in pos)
    fp = sum(1 for g, p in zip(gold, pred, strict=True) if g not in pos and p in pos)
    fn = sum(1 for g, p in zip(gold, pred, strict=True) if g in pos and p not in pos)
    prec, rec = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    return {"precision": prec, "recall": rec, "f1": _f1(prec, rec), "tp": tp, "fp": fp, "fn": fn}


def triage_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Metrics from `[{id, gold, pred, tags?}]`.

    - **missed urgent**: gold notify/act, predicted ignore/brief (the costly error: it waits until tomorrow).
    - **false alarms**: gold ignore/brief, predicted notify/act (it interrupted for nothing).
    - **dropped**: gold anything but ignore, predicted ignore (it was never shown at all).
    """
    gold = [i["gold"] for i in items]
    pred = [i["pred"] for i in items]
    labels = per_label(gold, pred)
    f1s = [v["f1"] for v in labels.values() if v["f1"] is not None and v["support"]]
    by_tag: dict[str, list[bool]] = defaultdict(list)
    for i in items:
        for tag in i.get("tags") or []:
            by_tag[tag].append(i["gold"] == i["pred"])
    missed = [i["id"] for i in items if i["gold"] in INTERRUPT and i["pred"] not in INTERRUPT]
    alarms = [i["id"] for i in items if i["gold"] not in INTERRUPT and i["pred"] in INTERRUPT]
    dropped = [i["id"] for i in items if i["gold"] != "ignore" and i["pred"] == "ignore"]
    return {
        "n": len(items),
        "accuracy": _ratio(sum(1 for g, p in zip(gold, pred, strict=True) if g == p), len(items)),
        "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else None,
        "labels": list(LABELS),
        "confusion": confusion(gold, pred),
        "per_label": labels,
        "interrupt": binary(gold, pred),
        "notify": per_label(gold, pred)["notify"],
        "act": per_label(gold, pred)["act"],
        "missed_urgent": len(missed),
        "missed_urgent_ids": missed,
        "false_alarms": len(alarms),
        "false_alarm_ids": alarms,
        "dropped": len(dropped),
        "dropped_ids": dropped,
        "gold_counts": dict(Counter(gold)),
        "pred_counts": dict(Counter(pred)),
        "by_tag": {
            t: {"n": len(v), "accuracy": _ratio(sum(v), len(v))}
            for t, v in sorted(by_tag.items())
            if len(v) >= 3
        },
    }


def coverage(text: str, groups: Sequence[Sequence[str]]) -> tuple[int, list[list[str]]]:
    """How many keyword groups appear in `text` (any alternative, case-insensitive), and the missing ones."""
    low = text.lower()
    missing = [list(g) for g in groups if not any(alt.lower() in low for alt in g)]
    return len(groups) - len(missing), missing


def format_confusion(m: list[list[int]], labels: Sequence[str] = LABELS) -> str:
    width = max(7, *(len(lab) for lab in labels)) + 1
    head = "gold \\ pred".ljust(12) + "".join(lab.rjust(width) for lab in labels)
    rows = [
        lab.ljust(12) + "".join(str(v).rjust(width) for v in row) for lab, row in zip(labels, m, strict=True)
    ]
    return "\n".join([head, *rows])
