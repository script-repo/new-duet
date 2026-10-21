# Tandem: plan

**Tandem** is an open-source, self-hosted *proactive* agent. It works in the background on its own
sandboxed computer, watches the apps you connect, decides what is urgent, and reaches out only when
something needs a decision or is done. It is inspired by Anthropic's **Claude Duet** research preview
(see [README](../README.md#inspiration)), rebuilt in the open so that it runs with **any model** and
**any agent harness**.

This document is the plan: goals, milestones, workstreams and the survey of prior art. The design
itself is in [SPEC.md](SPEC.md).

## 1. Goals

1. **Get back to what needs you.** A background service that checks in on its own, triages, briefs
   you each morning, and stays quiet until something needs you.
2. **One thread.** Text or voice, no prompt setup, memory across conversations, inline cards for
   drafts, approvals, status and artifacts. A daily dashboard and a timeline of what it did and is
   doing, with steer and stop.
3. **Any model.** Anthropic, OpenAI, Gemini and any OpenAI-compatible endpoint (Nutanix Enterprise
   AI, vLLM, Ollama, LM Studio, LiteLLM), chosen per role: a cheap model for frequent check-ins, a
   strong one for real work. A mock mode that runs everything offline.
4. **Any harness.** The worker that executes tasks is a plug-in: built-in loop, LangChain Deep
   Agents, Claude Agent SDK, OpenAI Agents SDK, or a CLI coding agent (Claude Code, Codex CLI).
5. **Safe by construction.** Approval gates from a policy file, deny on timeout, audit log,
   sandboxed tasks, credentials that never reach a model.
6. **Easy to run.** One process, SQLite by default, `uv run tandem serve`, Docker/Compose, works on
   Linux, macOS and Windows.

Non-goals for v0.1: multi-tenant SaaS, native mobile apps (the PWA installs on iOS/Android),
fine-tuned triage models, a connector marketplace.

## 2. Milestones

All eight milestones shipped in v0.2.0 (2026-10-21).

| # | Milestone | Done when |
|---|---|---|
| M0 | Plan and spec | `docs/PLAN.md` and `docs/SPEC.md` on `main` |
| M1 | Core skeleton | settings, SQLite/Postgres store, event bus, FastAPI app, health, mock models |
| M2 | Models | `ModelClient` with Anthropic, OpenAI, Gemini, OpenAI-compatible, mock; per-role routing |
| M3 | Proactive engine | scheduler (check-ins, morning brief, webhooks, polling), triage, notification policy |
| M4 | Tools, approvals, sandbox | tool registry, policy file, approval cards with deny-on-timeout, audit log, Docker/local sandbox, secret redaction |
| M5 | Harnesses | `Harness` interface; built-in, Deep Agents, Claude Agent SDK, OpenAI Agents SDK, CLI adapters |
| M6 | Connectors | MCP client; IMAP/SMTP, CalDAV/ICS, webhook inbox, filesystem; fake connectors with seeded demo data |
| M7 | Memory, skills, artifacts | facts/preferences/thread memory; markdown skills; docx/xlsx/pptx/md/html artifacts |
| M8 | Web app | Svelte 5 PWA: thread, voice, cards, Today dashboard, Activity timeline, Library, Settings |
| M9 | Tests and CI | unit + scripted-day integration tests in mock mode, frontend tests, GitHub Actions on Linux/macOS/Windows + Docker build, all green |
| M10 | Docs and release | README (Linux/macOS/Windows/Docker), config reference, architecture diagram, screenshots |

## 3. Workstreams (parallel)

The spec fixes the interfaces first (models, harness, tools, connectors, HTTP API and event shapes),
so these streams can proceed in parallel on their own branches and git worktrees, then merge:

| Stream | Branch | Scope | Depends on |
|---|---|---|---|
| A. Core engine | `core` | settings, store, bus, scheduler, triage, brief, notifications, approvals, tasks, API | spec |
| B. Models + harnesses | `core` (same stream, separate packages) | `tandem.models`, `tandem.harness` | A's tool/approval interfaces |
| C. Connectors + artifacts | `core` | `tandem.connectors`, `tandem.artifacts`, demo seed data | tool interface |
| D. Web UI | `ui` | `frontend/` Svelte PWA, built into `src/tandem/server/static` | HTTP API + event shapes in SPEC §10 |
| E. Ops | `main` after merge | Dockerfile, compose, CI, README, screenshots | A-D |

Integration owner: the main session merges `ui` into `main` after the backend lands, runs the
full suite and fixes the seams. Tests are written alongside each stream; the scripted-day
integration test is the acceptance gate for M9.

## 4. Survey of prior art

The person asked us to learn from open projects that deliver similar outcomes, and to reuse code only
where the license allows it (MIT, Apache-2.0, BSD), with attribution in [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).
Licenses below were read from each repository's GitHub metadata on 2026-10-21.

| Project | License | What it does | What we take |
|---|---|---|---|
| **Claude Duet** (Anthropic, product page) | proprietary | Proactive agent: morning brief, quiet until needed, approvals, one thread, own cloud computer | The product concept and feature list only. No code, copy or branding. |
| **NanoClaw** (nanocoai/nanoclaw; fork script-repo/nanoclaw) | MIT | Lightweight personal agent in containers; channels (WhatsApp etc.), scheduled tasks, per-group memory files, agent swarms | Container-per-task isolation; scheduled tasks as first-class jobs; memory as plain markdown files the agent edits; skills as markdown folders. Ideas only. |
| **OpenClaw** (openclaw/openclaw) | MIT | Always-on personal assistant across chat platforms, heartbeat check-ins, skills | The "heartbeat" check-in loop that wakes the agent on a schedule and lets it decide whether to speak; skills folder convention. Ideas only. |
| **LangGraph ambient agents**: executive-ai-assistant (EAIA) and agent-inbox (langchain-ai) | MIT | Email triage (ignore / notify / respond), drafts for review, a cron graph that polls Gmail; an inbox UI for human-in-the-loop interrupts with accept / edit / respond / ignore | The three-way triage outcome (ignore, notify, act), triage few-shot rules from user preferences, the accept/edit/reject interrupt card model, and reflection that turns user edits into new preferences. Ideas only; reimplemented. |
| **Letta / MemGPT** (letta-ai/letta) | Apache-2.0 | Stateful agents with core memory blocks always in context plus archival memory searched by tool | Two-tier memory: small "core" block in every prompt (persona, user facts, preferences) and a searchable archive with `memory_search` / `memory_write` tools. Ideas only. |
| **OpenHands** (OpenHands/OpenHands) | MIT (core) | Software agent with a sandboxed runtime (Docker), event stream, action/observation model | Docker runtime per task with a mounted workspace and no host credentials; event stream that the UI renders as a timeline. Ideas only. |
| **Open Interpreter** (OpenInterpreter/open-interpreter) | AGPL-3.0 | Local code-running agent | Ideas only (confirm before running code). **No code copied** (AGPL is not compatible with our Apache-2.0 license). |
| **Khoj** (khoj-ai/khoj) | AGPL-3.0 | Self-hosted "second brain" with scheduled automations and multi-model support | Scheduled automations that email results; per-role model choice. **Ideas only, no code** (AGPL). |
| **Leon** (leon-ai/leon) | MIT | Open personal assistant with voice in/out | Voice input with server-side transcription fallback. Ideas only. |
| **LangChain Deep Agents** (langchain-ai/deepagents) | MIT | Batteries-included agent harness (planning, files, subagents) | Used as an optional dependency (one of our harnesses). |
| **langchain-mcp-adapters** | MIT | MCP servers as LangChain tools | Used as an optional dependency inside the Deep Agents harness. |
| **script-repo/nai-deep-agents-1** (the person's own) | Apache-2.0 | FastAPI SSE + Svelte 5 UI, provider-agnostic models, scripted mock mode | Stack and patterns: one-process FastAPI + static Svelte build, mock-model approach, CI layout, Dockerfile. Same author; adapted freely. |

Summary: we copy **no third-party source code**; everything is reimplemented. We depend on several
permissively licensed libraries (listed with their licenses in `THIRD_PARTY_NOTICES.md`). Where an
idea came from a specific project, SPEC.md says so in §14.

## 5. Stack choice (summary; rationale in SPEC §2)

- **Backend:** Python 3.11+, FastAPI, Server-Sent Events, SQLAlchemy Core (SQLite default, Postgres
  optional), `httpx` for provider APIs (no SDK lock-in, easy to mock), `uv` for packaging.
- **Frontend:** Svelte 5 + Vite + TypeScript, compiled to static files served by the same process;
  a hand-written service worker and manifest make it an installable PWA.
- **Why:** one process and one port; tiny runtime overhead (no Node at runtime, no message broker);
  SSE is enough for one-way streaming and works through proxies; Svelte 5 output is small and fast
  on phones; Python is where the agent harnesses live.

## 6. Risks

| Risk | Mitigation |
|---|---|
| Optional harness SDKs change APIs often | Each adapter imports lazily, reports `available: false` with a reason, and has a fake-module test of its wiring |
| Triage misjudges urgency (as Duet's own notes say) | Rules first (VIP senders, deadlines, money), model second, a conservative default; every decision is visible in Activity with its reason; user feedback becomes a preference |
| Missed events between check-ins | Webhooks for push sources, a short poll interval for cheap triage, and a catch-up window on each check-in |
| Unsafe actions | Default-deny policy for send/pay/delete/post, deny on timeout, audit log, sandbox, secret redaction |
| Docker not available (CI on macOS/Windows, laptops) | Local restricted sandbox fallback, chosen automatically |

## 7. v0.2 plan

v0.1 runs a scripted day offline. v0.2 is about a real week: real apps, real phones, real models, and
numbers that say whether it works. Design in [SPEC §15](SPEC.md#15-v02-real-apps-real-phones-real-measurements).

| # | Milestone | Done when |
|---|---|---|
| V1 | Plan and spec | this section and SPEC §15 on `main` |
| V2 | Server key, vault, OAuth + PKCE | tokens encrypted at rest, refreshed, revoked; never in a tool result (tested) |
| V3 | Connectors | Gmail, Google Calendar, Slack, Microsoft 365: poll, push, normalize, gated actions; recorded-HTTP tests; app registration docs |
| V4 | Web Push | VAPID keys, subscriptions, aes128gcm sender, service worker actions, notify settings, fallback; tested against a fake push service |
| V5 | Evals | 150-event labeled triage set, brief scenarios, `tandem eval triage|brief`, metrics verified with mock and noisy models, REAL-MODELS.md pilot plan |
| V6 | Quality loop | GenAI-semconv OTLP traces, `harness/`, ambientq compose profile, mock traces → ambientq insights test |
| V7 | UI | Connections page, notification settings, Evals page; screenshots |
| V8 | Release | ruff, pytest, frontend tests and CI green; tag `v0.2.0` |

Streams run in parallel on their own worktrees and branches (`v02-connectors`, `v02-push`, `v02-evals`,
`v02-quality`), each with its own routes module and tables so merges stay small; the main session owns
the shared seams (settings, runtime wiring, UI) and the merges.

**What v0.2 still can't prove without credentials:** live OAuth consent screens, Pub/Sub and Graph push
delivery to a public URL, real model accuracy beyond one small free model (REAL-MODELS.md §3.1), and
iOS push on a device. REAL-MODELS.md and
CONNECTORS.md describe how to check each one in a one-week pilot.
