from __future__ import annotations

import json
import re
from collections import Counter

import httpx
import pytest
from fastapi.testclient import TestClient

from tandem.cli import main
from tandem.config import AppConfig, ModelProfile, Settings
from tandem.evals import run_brief_eval, run_triage_eval, save_result
from tandem.evals.metrics import LABELS, binary, confusion, coverage, per_label, triage_metrics
from tandem.evals.models import resolve_profile
from tandem.evals.report import brief_report, triage_report
from tandem.evals.runner import evals_dir, load_jsonl, load_triage_dataset
from tandem.runtime import Runtime
from tandem.server.app import create_app

# ---- metrics, checked by hand -------------------------------------------------------------------------------

GOLD = ["ignore", "ignore", "brief", "brief", "notify", "notify", "notify", "act"]
PRED = ["ignore", "brief", "brief", "notify", "notify", "brief", "notify", "notify"]


def test_confusion_and_per_label_by_hand():
    assert confusion(GOLD, PRED) == [
        [1, 1, 0, 0],  # ignore
        [0, 1, 1, 0],  # brief
        [0, 1, 2, 0],  # notify
        [0, 0, 1, 0],  # act
    ]
    pl = per_label(GOLD, PRED)
    # notify: tp=2, fp=2 (a brief and an act predicted notify), fn=1 (one notify predicted brief)
    assert pl["notify"] == {"precision": 0.5, "recall": 0.6667, "f1": 0.5714, "support": 3}
    assert pl["act"] == {"precision": None, "recall": 0.0, "f1": None, "support": 1}
    assert pl["ignore"]["precision"] == 1.0 and pl["ignore"]["recall"] == 0.5


def test_interrupt_and_missed_urgent_by_hand():
    b = binary(GOLD, PRED)
    # positives (notify/act) gold: idx 4,5,6,7; predicted positive: 3,4,6,7
    assert (b["tp"], b["fp"], b["fn"]) == (3, 1, 1)
    assert b["precision"] == 0.75 and b["recall"] == 0.75
    items = [
        {"id": f"i{n}", "gold": g, "pred": p, "tags": ["x"] if n < 4 else []}
        for n, (g, p) in enumerate(zip(GOLD, PRED, strict=True))
    ]
    m = triage_metrics(items)
    assert m["accuracy"] == 0.5
    assert m["missed_urgent_ids"] == ["i5"]
    assert m["false_alarm_ids"] == ["i3"]
    assert m["dropped"] == 0
    assert m["by_tag"]["x"] == {"n": 4, "accuracy": 0.5}
    assert sum(map(sum, m["confusion"])) == len(items)


def test_coverage_groups():
    hit, missing = coverage(
        "POS card declined; Mira wants Q3", [["card", "POS"], ["Q3"], ["Trailhead", "11:00"]]
    )
    assert hit == 2 and missing == [["Trailhead", "11:00"]]


# ---- datasets ------------------------------------------------------------------------------------------------


def test_triage_dataset_is_well_formed():
    rows, meta = load_triage_dataset()
    assert len(rows) >= 150
    assert len({r["id"] for r in rows}) == len(rows)
    counts = Counter(r["label"] for r in rows)
    assert set(counts) == set(LABELS) and min(counts.values()) >= 10
    assert {r["kind"] for r in rows} >= {"email", "calendar", "chat", "payment"}
    assert all(r.get("reason") and r.get("title") is not None for r in rows)
    assert meta["now"] and meta["memory"]


def test_brief_dataset_is_well_formed():
    rows = load_jsonl(evals_dir() / "briefs.jsonl")
    assert len(rows) >= 10
    for r in rows:
        assert isinstance(r["must_mention"], list) and all(isinstance(g, list) for g in r["must_mention"])


# ---- runs with the mock and the noisy fake ----------------------------------------------------------------------


async def test_mock_triage_eval_is_deterministic_and_consistent():
    a = await run_triage_eval("mock", AppConfig())
    b = await run_triage_eval("mock", AppConfig())
    ma, mb = a["metrics"], b["metrics"]
    assert ma["confusion"] == mb["confusion"]
    n = ma["n"]
    assert n == 150 and sum(map(sum, ma["confusion"])) == n
    diag = sum(ma["confusion"][i][i] for i in range(4))
    assert ma["accuracy"] == round(diag / n, 4)
    gold_interrupt = ma["gold_counts"]["notify"] + ma["gold_counts"]["act"]
    assert (
        ma["missed_urgent"] == ma["interrupt"]["fn"]
        and ma["interrupt"]["tp"] + ma["interrupt"]["fn"] == gold_interrupt
    )
    # bulk and muted mail is always ignored by the rules, whatever the model says
    by_id = {i["id"]: i for i in a["items"]}
    assert by_id["t002"]["pred"] == "ignore" and by_id["t005"]["pred"] == "ignore"
    assert by_id["t001"]["pred"] == "notify"
    assert a["usage"]["input_tokens"] > 0 and a["model_failures"] == 0
    assert "missed urgent" in triage_report(a)


async def test_noisy_model_degrades_the_scores_deterministically():
    mock = (await run_triage_eval("mock", AppConfig()))["metrics"]
    n1 = (await run_triage_eval("noisy", AppConfig()))["metrics"]
    n2 = (await run_triage_eval("noisy", AppConfig()))["metrics"]
    assert n1["confusion"] == n2["confusion"]
    assert n1["accuracy"] < mock["accuracy"]
    assert n1["false_alarms"] > mock["false_alarms"]
    heavy = (await run_triage_eval("noisy:0.9", AppConfig()))["metrics"]
    assert heavy["accuracy"] < n1["accuracy"]


async def test_live_provider_wire_format_with_recorded_answers(monkeypatch):
    """A real provider client (Anthropic Messages) against a fake server: everything is 'notify'."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        assert request.url.path == "/v1/messages" and body["model"] == "claude-haiku-5-5"
        content = body["messages"][-1]["content"]
        if isinstance(content, list):  # content blocks
            content = "".join(b.get("text", "") for b in content)
        data = json.loads(re.search(r"<data>(.*)</data>", content, re.S).group(1))
        results = [
            {"id": e["id"], "urgency": "high", "action": "notify", "reason": "test"} for e in data["events"]
        ]
        text = json.dumps({"results": results})
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1000, "output_tokens": 200},
            },
        )

    r = await run_triage_eval(
        "anthropic/claude-haiku-5-5", AppConfig(), limit=40, transport=httpx.MockTransport(handler)
    )
    assert len(seen) == 2 and r["model_failures"] == 0  # 40 items in batches of 20
    m = r["metrics"]
    assert r["usage"] == {"input_tokens": 2000, "output_tokens": 400}
    # every prediction is notify except what the rules force to ignore (bulk, muted)
    forced = {i["id"] for i in r["items"] if i["decided_by"] == "rules"}
    assert all(i["pred"] == "notify" for i in r["items"] if i["id"] not in forced)
    assert all(i["pred"] == "ignore" for i in r["items"] if i["id"] in forced)
    assert m["interrupt"]["recall"] == 1.0 or m["missed_urgent"] == len(
        [i for i in r["items"] if i["id"] in forced and i["gold"] in ("notify", "act")]
    )


def test_model_specs():
    cfg = AppConfig(
        models={"cheap": ModelProfile(provider="openai_compatible", model="llama", base_url="http://x")}
    )
    assert resolve_profile("cheap", cfg).model == "llama"
    assert resolve_profile("triage", cfg).model == "llama"
    p = resolve_profile(
        "openai_compatible/llama-3-1-8b",
        cfg,
        base_url="https://nai.example/api/v1",
        api_key_env="NAI_API_KEY",
    )
    assert (p.provider, p.model, p.base_url, p.api_key_env) == (
        "openai_compatible",
        "llama-3-1-8b",
        "https://nai.example/api/v1",
        "NAI_API_KEY",
    )
    assert resolve_profile("anthropic/claude-haiku-5-5", cfg).api_key_env == "ANTHROPIC_API_KEY"
    with pytest.raises(ValueError):
        resolve_profile("nonsense", cfg)


async def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        await run_triage_eval("openai/gpt-5-mini", AppConfig())


async def test_brief_eval_with_mock_writer_and_judge():
    r = await run_brief_eval("mock", "mock", AppConfig())
    m = r["metrics"]
    assert m["n"] == 10 and m["errors"] == 0
    assert 0.5 < m["coverage"] <= 1.0
    # the injected "ALL CLEAR" in scenario b07 is quoted by the naive mock: the eval catches it
    b07 = next(i for i in r["items"] if i["id"] == "b07")
    assert b07["leaks"] == [["ALL CLEAR"]] and b07["scores"]["faithfulness"] < 5
    assert set(m["judge_means"]) == {
        "coverage",
        "prioritization",
        "faithfulness",
        "concision",
        "actionability",
    }
    assert "judge overall" in brief_report(r)


# ---- CLI and API ----------------------------------------------------------------------------------------------


def test_cli_eval(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TANDEM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TANDEM_MOCK", "1")
    out = tmp_path / "r.json"
    assert main(["eval", "triage", "--model", "mock", "--json", str(out)]) == 0
    assert json.loads(out.read_text())["metrics"]["n"] == 150
    assert "accuracy" in capsys.readouterr().out
    assert main(["eval", "triage", "--model", "mock", "--no-save", "--fail-under", "0.99"]) == 1
    assert main(["eval", "triage", "--model", "bogus", "--no-save"]) == 2
    assert main(["eval", "brief", "--model", "mock", "--judge", "mock", "--no-save", "--limit", "3"]) == 0
    assert list((tmp_path / "data" / "evals").glob("triage-*.json"))


async def test_api_evals(tmp_path):
    rt = Runtime(Settings(data_dir=tmp_path, database_url="sqlite://", mock=True, autostart=False))
    rt.cfg.demo_autoplay = False
    save_result(rt.store, tmp_path, await run_triage_eval("noisy", rt.cfg))
    with TestClient(create_app(rt, run_scheduler=False)) as c:
        body = c.get("/api/evals").json()
        assert body["triage"]["model"] == "noisy" and "items" not in body["triage"]
        assert body["triage"]["errors"] and body["history"][0]["kind"] == "triage"
        assert c.post("/api/evals/triage/run", json={"model": "openai/gpt-5"}).status_code == 400
        assert c.post("/api/evals/triage/run", json={"model": "mock"}).status_code == 202
        for _ in range(200):
            body = c.get("/api/evals").json()
            if body["triage"]["model"] == "mock" and not body["running"]:
                break
        assert body["triage"]["model"] == "mock"


async def test_pilot_export_and_stats(rt):
    from datetime import timedelta

    from tandem.evals.pilot import export_events, pilot_stats

    rt.clock.advance(hours=7)  # 16:00: the demo inbox has arrived
    await rt.checkin()
    since = rt.clock.now() - timedelta(days=1)
    rows = export_events(rt.store, since)
    assert rows and all(r["label"] == r["predicted"] for r in rows)
    assert {r["label"] for r in rows} <= set(LABELS)
    payment = next(r for r in rows if "declined" in r["title"])
    assert payment["label"] == "notify"
    stats = pilot_stats(rt.store, since, rt.clock.now())
    assert stats["events"] >= len(rows) and stats["interruptions_per_day"] >= 1
    assert stats["notify_latency_min"]["median"] is not None


def test_cli_pilot(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TANDEM_DATA_DIR", str(tmp_path / "data"))
    assert main(["eval", "stats", "--since", "1d"]) == 0
    assert json.loads(capsys.readouterr().out)["events"] == 0
    assert main(["eval", "export", "--out", str(tmp_path / "x.jsonl")]) == 0
