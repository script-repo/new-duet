"""`/api/evals`: the latest triage and brief eval results, and a way to run one (SPEC §15.5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from tandem.config import ROLES
from tandem.evals import latest, run_triage_eval, save_result


class EvalRun(BaseModel):
    model: str = "triage"


def evals_router(runtime: Any, auth: Any) -> APIRouter:
    r = APIRouter(dependencies=[auth])
    running: set[str] = set()

    @r.get("/api/evals")
    async def evals() -> dict[str, Any]:
        return {**latest(runtime.store), "running": sorted(running)}

    @r.post("/api/evals/triage/run", status_code=202)
    async def run(body: EvalRun) -> dict[str, Any]:
        # Only models already configured here (or the offline fakes): the UI can't point Tandem at a new endpoint.
        allowed = {*ROLES, *runtime.cfg.models, "mock", "noisy"}
        if body.model not in allowed:
            raise HTTPException(400, f"model must be one of {', '.join(sorted(allowed))}")
        if "triage" in running:
            raise HTTPException(409, "a triage eval is already running")
        running.add("triage")

        async def job() -> None:
            try:
                result = await run_triage_eval(body.model, runtime.cfg)
                save_result(runtime.store, runtime.settings.state_dir, result)
                m = result["metrics"]
                runtime.store.add_activity(
                    "eval",
                    f"Triage eval: {m['accuracy'] * 100:.0f}% accurate ({body.model})",
                    f"{m['missed_urgent']} missed urgent, {m['false_alarms']} false alarms on {m['n']} items",
                )
                runtime.bus.publish("eval.updated", {"kind": "triage"})
            except Exception as exc:
                runtime.store.add_activity("eval", "Triage eval failed", str(exc)[:300], level="warn")
                runtime.bus.publish("eval.updated", {"kind": "triage", "error": str(exc)[:300]})
            finally:
                running.discard("triage")

        runtime.background(job())
        return {"started": True, "model": body.model}

    return r
