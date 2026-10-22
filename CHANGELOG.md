# Changelog

## v0.2.1 (2026-10-21)

Fixes from the first live test on real models (OpenRouter's free router, `openrouter/free`).

- **Reasoning models that run out of tokens.** An empty answer with `finish_reason: length` (the
  model spent `max_tokens` thinking) used to get a "not valid JSON" repair prompt, which failed the
  same way. The OpenAI and OpenAI-compatible clients now retry once with 4× `max_tokens` (capped at
  64k) and low reasoning effort (`reasoning: {effort: low}`, or `reasoning_effort` on OpenAI) unless
  the profile sets its own; a server that rejects the field gets just the bigger budget.
  `openai_compatible` profiles now default to `max_tokens: 16000`.
- **A failed brief no longer eats events.** Events are marked briefed only after the brief reaches
  the thread, and only those it showed (the fallback summary shows 7). Items saved for the brief
  stay until a brief covers them, even if they predate the last one.
- **Brief counts are computed, not written by the model.** The greeting's "N things in progress and
  M that need you" comes from the running tasks, the items that need you (any with a draft) and
  pending approvals no item carries, so it can't contradict the badge. Events triage handed to a
  task (`acted`, e.g. a VIP request) now reach the brief.
- **Tasks orphaned by a restart.** At startup, queued tasks run again and running or waiting ones
  are marked failed ("server restarted") with a note in the thread, as pending approvals already were.
- **`payments_list(status="all")`** returned nothing. The fake connector's `status` now has an enum,
  and `all`, `any`, `*` and empty mean no filter. The real connectors have no filter of this kind.
- **Harness logs show tool arguments.** The OpenAI Agents, Claude Agent SDK and Deep Agents
  harnesses now log `tool(arg=value, …)` like the built-in one (shortened, secrets redacted).
- **Headless screenshots**: documented `--timeout=20000 --no-proxy-server` instead of
  `--virtual-time-budget` (frontend/README.md). The "Offline" dot in those shots is the shot
  stopping the page, not a bug in the stream status: a running browser shows "Live".

Live check after the fixes, all on `openrouter/free`, no `--max-tokens` or `--extra-body` needed,
$0 spent: triage eval on 30 items 50.0% accuracy, interrupt recall 100%, 0 missed urgent, 0 model
failures in 112 s (v0.2.0 with `--max-tokens 16000`: 56.7%; mock: 66.7%; the free router picks a
different model per call, so a few points either way is noise). Check-in, brief and chat all
worked. Details in [docs/REAL-MODELS.md §3.2](docs/REAL-MODELS.md#32-live-test-on-openrouters-free-router).

## v0.2.0 (2026-10-21)

Real apps, real phones, real measurements: token vault and OAuth (PKCE); Gmail, Google Calendar,
Slack and Microsoft 365 connectors; Web Push (VAPID); `tandem eval` with a 150-event labeled set;
OpenTelemetry GenAI traces and `harness.yaml` for the ambientq quality loop; Connections,
Notifications and Evals screens.

## v0.1 (2026-10-21, untagged)

First release: one thread, check-ins and triage, morning brief, approval gates, memory, artifacts,
sandboxed tasks on five harnesses, mock mode, PWA.
