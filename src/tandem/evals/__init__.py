"""Evals: measure triage and brief quality on labeled data, with any model (SPEC §15.5)."""

from tandem.evals.metrics import LABELS, triage_metrics
from tandem.evals.runner import latest, run_brief_eval, run_triage_eval, save_result

__all__ = ["LABELS", "latest", "run_brief_eval", "run_triage_eval", "save_result", "triage_metrics"]
