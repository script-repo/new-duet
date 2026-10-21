"""Plain-text reports for `tandem eval`."""

from __future__ import annotations

from typing import Any

from tandem.evals.metrics import LABELS, format_confusion


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def triage_report(r: dict[str, Any]) -> str:
    m = r["metrics"]
    lines = [
        f"Triage eval: {r['model']} ({r['provider']}/{r['model_name']}) on {r['dataset']}, {m['n']} items, "
        f"{r['seconds']}s, {r['usage']['input_tokens']:,} in / {r['usage']['output_tokens']:,} out tokens",
        "",
        f"accuracy        {_pct(m['accuracy'])}   macro-F1 {m['macro_f1']}",
        f"interrupt       precision {_pct(m['interrupt']['precision'])}  recall {_pct(m['interrupt']['recall'])}"
        "   (notify + act vs the rest)",
        f"notify          precision {_pct(m['notify']['precision'])}  recall {_pct(m['notify']['recall'])}",
        f"act             precision {_pct(m['act']['precision'])}  recall {_pct(m['act']['recall'])}",
        f"missed urgent   {m['missed_urgent']}   false alarms {m['false_alarms']}   dropped {m['dropped']}",
    ]
    if r.get("model_failures"):
        lines.append(f"model failures  {r['model_failures']} batch(es) fell back to rules only")
        lines += [f"  - {d}" for d in r.get("model_failure_details", [])[:3]]
    lines += ["", format_confusion(m["confusion"]), "", "per label       precision  recall     F1  support"]
    for lab in LABELS:
        p = m["per_label"][lab]
        lines.append(
            f"  {lab:<13} {_pct(p['precision']):>9} {_pct(p['recall']):>7} {p['f1'] if p['f1'] is not None else 'n/a':>6}"
            f" {p['support']:>8}"
        )
    if m["by_tag"]:
        lines += ["", "accuracy by tag"]
        lines += [f"  {t:<15} {_pct(v['accuracy']):>7}  (n={v['n']})" for t, v in m["by_tag"].items()]
    missed = [i for i in r["items"] if i["id"] in set(m["missed_urgent_ids"])]
    if missed:
        lines += ["", "missed urgent (should have interrupted or acted)"]
        lines += [f"  {i['id']}  {i['gold']:>6} -> {i['pred']:<6} {i['title'][:70]}" for i in missed]
    return "\n".join(lines)


def brief_report(r: dict[str, Any]) -> str:
    m = r["metrics"]
    lines = [
        f"Brief eval: {r['model']} judged by {r['judge']} on {r['dataset']}, {m['n']} scenarios, {r['seconds']}s",
        "",
        f"key-point coverage {_pct(m['coverage'])}   leaks {m['leaks']}   errors {m['errors']}",
        f"judge overall      {m['judge_overall']} / 5",
    ]
    lines += [f"  {k:<15} {v}" for k, v in m["judge_means"].items()]
    lines += ["", "scenario  coverage  judge  notes"]
    for i in r["items"]:
        if i.get("error"):
            lines.append(f"  {i['id']:<7} error: {i['error'][:80]}")
            continue
        s = i["scores"]
        avg = round(sum(s.values()) / len(s), 1) if s else "n/a"
        lines.append(f"  {i['id']:<7} {i['coverage']:>8}  {avg:>5}  {i['notes'][:80]}")
    return "\n".join(lines)
