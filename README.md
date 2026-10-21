# Tandem

**A proactive, open-source assistant that works alongside you in one thread.** Tandem checks your
sources on a schedule, triages what came in, stays quiet until something needs you, writes you a
morning brief, and does real work in the background: it drafts, builds documents and spreadsheets,
and runs code in a sandbox. Anything that acts as you (sending, paying, posting) waits on an approval
card you can approve, edit or reject.

It runs on the models you choose (Anthropic, OpenAI, Gemini, or any OpenAI-compatible endpoint such
as Nutanix Enterprise AI, vLLM, Ollama, LM Studio or LiteLLM), with the agent harness you choose
(built-in, LangChain Deep Agents, Claude Agent SDK, OpenAI Agents SDK, or a coding CLI such as
Claude Code or Codex). With no API key at all it runs a complete offline demo.

> **Inspiration.** Tandem is inspired by **Claude Duet** from Anthropic: one proactive thread with
> check-ins, briefs and approvals. Tandem is an independent project with no affiliation with
> Anthropic. It contains no Anthropic code, branding or marketing copy, and it is not limited to
> Claude models. Ideas borrowed from other open-source projects are credited in
> [docs/SPEC.md §14](docs/SPEC.md#14-provenance-of-ideas) and
> [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

| Desktop | Mobile |
|---|---|
| ![Tandem on desktop, mock mode](docs/screenshots/desktop.png) | ![Tandem on a phone, mock mode](docs/screenshots/mobile.png) |

## Contents

- [What it does](#what-it-does)
- [Quick start](#quick-start) (Linux, macOS, Windows, Docker)
- [Configuration](#configuration)
- [Models](#models) · [Harnesses](#harnesses) · [Connectors](#connectors)
- [Approvals and security](#approvals-and-security)
- [Architecture](#architecture)
- [API](#api) · [Development](#development) · [Limits](#limits)

Design documents: [docs/PLAN.md](docs/PLAN.md) (plan, milestones, prior-art survey) and
[docs/SPEC.md](docs/SPEC.md) (full specification).

## What it does

- **Proactive core.** A scheduler runs check-ins (every 15 minutes by default) across your
  connectors; webhooks push events in at once. Each event is triaged into *ignore*, *brief*,
  *notify* or *act*: hard rules first (mute lists, VIPs, money, deadlines, security), then a cheap
  model. A notification policy pushes only what clears your urgency bar, coalesces bursts, holds
  everything but critical during quiet hours, and rate-limits itself. Notifications reach your phone
  or desktop by Web Push even with the app closed (iOS 16.4+ from the Home Screen), or ntfy/webhooks
  ([docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md)).
- **Morning brief.** At `brief_at` a strong model turns overnight events, today's calendar,
  pending approvals and running tasks into a short brief. Replies it drafts become approval cards,
  ready to approve, edit or reject.
- **One thread.** A responsive, installable PWA: chat; voice input (browser speech recognition, or
  a Whisper-compatible server); inline cards for approvals, drafts, task status, notifications and
  artifacts; a *Today* dashboard; an *Activity* timeline; and steer or stop on any running task.
- **Background work.** Tasks run on the harness you pick, in a per-task sandbox (Docker when
  available, else a restricted temp folder). They produce artifacts (docx, xlsx, pptx, md, html)
  you can preview in the browser and download.
- **Memory and skills.** Core memory (who you are, how you like things) goes into every prompt;
  an archive is searchable. When you edit a draft before approving it, Tandem learns the
  preference. Skills are folders of Markdown instructions (`SKILL.md`) loaded on demand.
- **Policy-driven approvals.** A YAML file decides which tools run, ask or are denied. Silence
  never approves: an unanswered card expires as a denial. Every call is audited.

## Quick start

You need [uv](https://docs.astral.sh/uv/) (it installs Python 3.11+ for you). The web UI is
built with Node.js 22+; skip that step and the server shows a short page saying how.

### Linux and macOS

```bash
git clone https://github.com/script-repo/new-duet.git tandem && cd tandem
uv sync
(cd frontend && npm ci && npm run build)   # builds the PWA into src/tandem/server/static
TANDEM_MOCK=1 uv run tandem serve          # offline demo with seeded data
```

Open <http://127.0.0.1:8000>. Mock mode loads a demo day for "Jordan" at Cedar Street Bikes:
an overnight inbox, a calendar, invoices and a CRM, released as the clock passes. Ask *"Build me a
one-page cash summary for this week and email it to Sam"* to watch a task run, ask for approval
and produce a spreadsheet.

To go live, drop `TANDEM_MOCK`, and either export a provider key (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY` or `GEMINI_API_KEY`; Tandem picks sensible cheap and strong models for it) or
write a `tandem.yaml` (see [Configuration](#configuration)).

### Windows (PowerShell)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
git clone https://github.com/script-repo/new-duet.git tandem; cd tandem
uv sync
cd frontend; npm ci; npm run build; cd ..
$env:TANDEM_MOCK = "1"; uv run tandem serve
```

On Windows the local sandbox has no resource limits (they are POSIX-only); install Docker
Desktop to get container isolation for tasks.

### Docker

```bash
cp .env.example .env        # optional: add provider keys; without any, the image runs mock mode
docker compose up -d        # builds the image (UI included) and serves http://localhost:8000
```

Data lives in the `tandem-data` volume. For Postgres instead of SQLite:

```bash
TANDEM_DATABASE_URL=postgresql+psycopg://tandem:tandem@postgres:5432/tandem \
  docker compose --profile postgres up -d
```

Compose publishes the port on `127.0.0.1` only. If you expose Tandem beyond your machine, set
`TANDEM_AUTH_TOKEN` and put it behind TLS; the UI then asks for the token once and keeps it in a
cookie.

### CLI

```text
tandem serve [--host H] [--port P] [--no-scheduler] [--forwarded-allow-ips IPS]
tandem checkin              run one check-in now and print the result
tandem brief                generate the morning brief now
tandem task "instructions"  run one task (approve its cards in the UI)
tandem config               print the effective configuration, without secrets
tandem harnesses            list harnesses and whether each is available
```

## Configuration

Two layers: **environment variables** (`TANDEM_*`, also read from `.env`) for deployment, and an
optional **`tandem.yaml`** for behavior. Secrets never go in `tandem.yaml`: it names the
environment variable that holds each one (`api_key_env`, `password_env`, `secret_env`).

### Environment

| Variable | Default | Meaning |
|---|---|---|
| `TANDEM_HOST` / `TANDEM_PORT` | `127.0.0.1` / `8000` | Where `serve` listens |
| `TANDEM_DATA_DIR` | `./data` | SQLite database, artifacts, sandboxes |
| `TANDEM_DATABASE_URL` | SQLite in the data dir | e.g. `postgresql+psycopg://user:pass@host/db` (extra `postgres`) |
| `TANDEM_CONFIG` | `./tandem.yaml` if present | Path to the YAML config |
| `TANDEM_MOCK` | `false` | Force mock models and demo connectors |
| `TANDEM_AUTH_TOKEN` | unset | Require this bearer token (or login cookie) for the API and UI |
| `TANDEM_TIMEZONE` | `schedule.timezone` | Overrides the schedule's timezone |
| `TANDEM_CORS_ORIGINS` | empty | Comma-separated origins, for a UI served elsewhere |
| `TANDEM_LOG_LEVEL` | `info` | Uvicorn log level |
| `TANDEM_AUTOSTART` | `true` | Run the scheduler inside `serve` |
| `TANDEM_PUBLIC_URL` | `http://{host}:{port}` | How browsers and providers reach Tandem (OAuth redirect, push callbacks) |
| `GOOGLE_CLIENT_ID`, `SLACK_CLIENT_ID`, `MS_CLIENT_ID` (+ `_SECRET`) | | OAuth apps for the connectors ([docs/CONNECTORS.md](docs/CONNECTORS.md)) |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, … | | Provider keys, read only by the server |

### tandem.yaml

A full annotated example is in [examples/tandem.example.yaml](examples/tandem.example.yaml).

| Key | Default | Meaning |
|---|---|---|
| `user_name`, `persona` | | Your name, and extra instructions added to every prompt |
| `models.<name>` | from env keys, else mock | A model profile: `provider`, `model`, `base_url`, `api_key_env`, `temperature`, `max_tokens`, `timeout_s` |
| `roles` | `triage→cheap`, others `→strong` | Which profile serves `triage`, `brief`, `chat`, `worker` |
| `harness.default` | `builtin` | `builtin`, `deepagents`, `claude-sdk`, `openai-agents`, `cli` |
| `harness.max_steps` / `max_concurrent` | `24` / `2` | Step budget per task; parallel tasks |
| `harness.cli_command` / `cli_timeout_s` | `auto` / `1800` | `claude`, `codex` or first found; timeout |
| `harness_options.<harness>` | `{}` | Per-harness extras, e.g. `deepagents: {native_model: true, mcp_servers: {...}}` |
| `schedule.checkin_every` / `brief_at` / `timezone` | `15m` / `07:30` / `UTC` | Check-in interval, brief time, timezone |
| `notify.min_urgency` | `high` | Lowest urgency that interrupts you (`low`…`critical`) |
| `notify.quiet_hours` / `max_per_hour` | `21:00-07:00` / `4` | Only critical gets through when quiet; rate limit |
| `notify.channels` | `ui` + `webpush` | Add `{type: ntfy, url}` or `{type: webhook, url}`, optionally `when: fallback` (only when Web Push didn't deliver); listing channels without `webpush` turns Web Push off |
| `notify.push_enabled` / `vapid_subject` | `true` / `mailto:tandem@localhost` | Web Push on/off; the VAPID contact (`TANDEM_VAPID_SUBJECT`, key in `TANDEM_VAPID_PRIVATE_KEY` or generated) |
| `connectors` | demo + webhook in mock mode | List of connectors (below) |
| `sandbox.mode` | `auto` | `docker`, `local`, or `auto` (Docker if reachable) |
| `sandbox.image` / `network` / `memory` / `cpus` / `exec_timeout_s` | `python:3.13-slim` / `false` / `1g` / `1` / `120` | Docker sandbox limits |
| `memory.core_chars` / `thread_window` | `2000` / `30` | Core memory budget; chat messages kept before summarizing |
| `voice.provider` | `none` | `openai_compatible` (Whisper API: `base_url`, `model`, `api_key_env`) or `mock` |
| `policy` | `examples/policy.yaml` | Approval policy file |
| `skills` | `[]` | Extra skill folders, besides `examples/skills` |
| `demo_autoplay` | on in mock mode | Run a check-in and brief at startup |

## Models

Each **profile** is one provider and model; each **role** picks a profile, so frequent, cheap
work (triage) and occasional, hard work (briefs, chat, tasks) can use different models.

| `provider` | Talks to | Notes |
|---|---|---|
| `anthropic` | Messages API | `api_key_env: ANTHROPIC_API_KEY` |
| `openai` | Chat Completions | `api_key_env: OPENAI_API_KEY` |
| `gemini` | Gemini `generateContent` | `api_key_env: GEMINI_API_KEY` |
| `openai_compatible` | any `/v1/chat/completions` | set `base_url`: Nutanix Enterprise AI, vLLM, LM Studio, LiteLLM, … |
| `ollama` | Ollama's OpenAI endpoint | `base_url` defaults to `http://localhost:11434/v1` |
| `mock` | nothing | Deterministic, offline; drives the demo and the tests |

```yaml
models:
  cheap:  {provider: openai_compatible, base_url: https://nai.example.com/api/v1, model: llama-3-1-8b-instruct, api_key_env: NAI_API_KEY}
  strong: {provider: anthropic, model: claude-opus-5-5, api_key_env: ANTHROPIC_API_KEY}
roles: {triage: cheap, brief: strong, chat: strong, worker: strong}
```

Clients retry rate limits and server errors (honoring `Retry-After`), and JSON replies get one
automatic repair attempt.

## Harnesses

A **harness** is the agent loop that runs a background task. Every harness gets the same gated
tools, sandbox and system prompt, so policy, approvals and audit apply whichever you choose. Pick
the default in `tandem.yaml`, or per task from the composer.

| Harness | Install | Notes |
|---|---|---|
| `builtin` | included | Plain tool loop on any provider; steer is read every step |
| `deepagents` | `uv sync --extra deepagents` | LangChain Deep Agents; any Tandem provider through a LangChain bridge, or the native LangChain model (`native_model: true`); optional MCP servers |
| `claude-sdk` | `uv sync --extra claude-sdk` + Claude Code installed | Claude Agent SDK; Tandem tools served as an in-process MCP server; the SDK's own tools pass the gate (`claude_<tool>`) and run in the sandbox folder |
| `openai-agents` | `uv sync --extra openai-agents` | OpenAI Agents SDK; non-OpenAI providers (and mock mode) through an in-process Chat Completions bridge |
| `cli` | `claude` or `codex` on `PATH` | Runs the coding CLI in the task's sandbox folder after one approval (`cli_run`); files it writes become artifacts |

`tandem harnesses` shows which are available here.

### Adding a harness

1. Subclass `tandem.harness.base.Harness`; set `name`, `display_name`, `description`.
2. `availability()` imports your framework lazily and returns `(False, "why")` when it is missing.
3. `run(task, ctx)` turns `ctx.tools` into your framework's tools, each calling
   `await ctx.tools.invoke(name, args)` (never call connectors directly: the gate lives there).
   Use `ctx.model` or `ctx.profile` for the model, `ctx.sandbox.root` as the working folder,
   `await ctx.log(kind, text)` for the activity timeline, and `ctx.drain_steer()` between turns.
   Return `HarnessResult(summary=..., ok=...)`.
4. Register it: decorate with `@register_harness` in a module imported by
   `tandem/harness/__init__.py`, or ship it as a separate package with an entry point:

   ```toml
   [project.entry-points."tandem.harnesses"]
   myharness = "my_package.harness:MyHarness"
   ```
5. Add a test with a fake module of your framework, as `tests/test_harnesses.py` does.

## Connectors

| `type` | Brings in | Tools | Options |
|---|---|---|---|
| `fake` | the seeded demo timeline | `email_*`, `calendar_*`, `payments_*`, `crm_lookup` | `data` (a scenario YAML; default `examples/demo/cedar-street.yaml`) |
| `imap` | new mail (IMAP), sends via SMTP | `<name>_search`, `<name>_read`, `<name>_send` | `host`, `username`, `password_env`, `smtp_host`, … |
| `ics` | events from an ICS feed or file | `<name>_list` | `url` or `path`, `timezone` |
| `caldav` | events from CalDAV (extra `caldav`) | `<name>_list`, `<name>_create` | `url`, `username`, `password_env`, `calendar` |
| `webhook` | `POST /api/webhooks/<name>`, HMAC-checked when `secret_env` is set | none | `secret_env` |
| `filesystem` | new and changed files in a folder | `<name>_list`, `<name>_read`, `<name>_write` | `root`, `watch` |
| `mcp` | any MCP server's tools, over stdio or streamable HTTP (extra `mcp`) | `<name>_<tool>`, tagged from the tool's name and annotations for policy | `command`/`args` or `url`, `env_from`, `headers_from`, `ask`, `poll_tool` |
| `gmail` | new inbox mail (history; Pub/Sub push) | `<name>_search`, `_read`, `_send`, `_reply` | `pubsub_topic`, `push_token_env` |
| `google_calendar` | new, changed and cancelled invites; Today's events | `<name>_list`, `_create` | `calendar_id` |
| `slack` | DMs, group DMs, listed channels, mentions (Events API push) | `<name>_search`, `_history`, `_post` | `channels`, `signing_secret_env` |
| `m365` | Outlook mail and calendar (Graph delta; change notifications) | `<name>_search`, `_read`, `_send`, `_reply`, `_calendar_list`, `_calendar_create` | `tenant` |

The four OAuth connectors connect from the Connections screen: register an app with the provider,
set its client id and secret (e.g. `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`) and `TANDEM_PUBLIC_URL`,
press Connect. Tokens are encrypted at rest and never reach a model or the UI. They poll without a public
URL and use push with an `https` one. Setup per provider: [docs/CONNECTORS.md](docs/CONNECTORS.md).

MCP is the first choice for anything else: point a connector at an MCP server and its tools are
gated like the built-in ones. Webhook senders sign the raw body with HMAC-SHA256 in
`X-Tandem-Signature: sha256=<hex>`.

## Approvals and security

- **Policy file** ([examples/policy.yaml](examples/policy.yaml)): first matching rule wins; match
  on tool name (globs), tags (`send`, `pay`, `delete`, `exec`, `external_post`, `write`), argument
  conditions (`gt`, `lt`, `in`, `regex`, …) and sandbox kind; act with `allow`, `ask` or `deny`.
  `on_timeout` must be `deny`, and the loader refuses anything else.
- **Cards**: approve, edit (only the fields the tool marks editable) or reject with a note.
  Decisions, results and denials go to an audit log (`/api/audit`).
- **Credentials stay server-side.** Keys are resolved from the environment by the server when a
  client or connector needs them. They never enter prompts, and tool results, logs and audit
  entries pass through a redactor that masks known secret values and common token patterns.
  Sandboxed commands get a scrubbed environment.
- **Untrusted content** (mail bodies, web pages, files) is wrapped and marked as data, never as
  instructions, and only the gate can act on it.
- **Sandbox**: Docker per task (`--network none`, read-only root, all capabilities dropped,
  non-root user, CPU and memory limits) when Docker is reachable; otherwise a temp folder per task
  with path confinement, a scrubbed environment and POSIX resource limits. The default policy
  asks before running commands in the local sandbox.

## Architecture

```mermaid
flowchart LR
  subgraph Sources
    C1[Connectors<br/>Gmail · Calendar · Slack · M365 · IMAP/SMTP · ICS/CalDAV · filesystem · MCP]
    WH[Webhooks]
  end
  subgraph Tandem process
    SCH[Scheduler<br/>check-ins · brief · expiry]
    TRI[Triage<br/>rules + cheap model]
    NOT[Notification policy]
    BRF[Morning brief<br/>strong model]
    CHAT[Chat agent<br/>one thread]
    TM[Task manager]
    H[Harness adapters<br/>builtin · deepagents · claude-sdk · openai-agents · cli]
    GATE[Tool gate<br/>policy · approvals · audit · redaction]
    SBX[Sandbox<br/>Docker or local]
    MEM[(Memory · skills)]
    ART[(Artifacts)]
    DB[(SQLite / Postgres)]
    BUS((Event bus))
    API[FastAPI REST + SSE]
  end
  UI[PWA: Thread · Today · Activity · Library · Settings]
  M[Models<br/>Anthropic · OpenAI · Gemini · OpenAI-compatible · mock]

  C1 --> SCH
  WH --> API --> TRI
  SCH --> TRI --> NOT --> BUS
  SCH --> BRF --> BUS
  TRI -->|act| TM
  CHAT -->|start_task| TM --> H --> GATE --> SBX
  GATE --> C1
  GATE --> ART
  H --> M
  TRI --> M
  BRF --> M
  CHAT --> M
  MEM --- CHAT & BRF & TRI & H
  BUS --> API --> UI
  UI -->|messages · decisions · steer/stop| API
  DB --- GATE & TM & CHAT & BRF
```

**Why this stack.** Python, because every agent framework worth wrapping (Deep Agents, the Claude
Agent SDK, the OpenAI Agents SDK, MCP) ships a Python SDK first. FastAPI with Server-Sent Events,
because the UI needs one-way live updates (new messages, card state, task logs) and SSE is plain
HTTP: it passes proxies, reconnects by itself and needs no extra server. Svelte 5, because its
compiled runes give a small, fast PWA (about 45 kB of gzipped JavaScript) that suits a phone. The
same stack as [nai-deep-agents-1](https://github.com/script-repo/nai-deep-agents-1). SQLite by
default for a zero-setup single user; SQLAlchemy Core keeps Postgres one URL away. The details are
in [docs/SPEC.md §2](docs/SPEC.md#2-stack-and-why).

## API

REST under `/api`, with live updates on `GET /api/stream` (SSE). The main routes:

| Route | Purpose |
|---|---|
| `GET/POST /api/thread` | Read the thread (with the cards it references); post a message |
| `GET /api/today` | The dashboard: brief, needs-you, in progress, upcoming, stats |
| `GET /api/approvals`, `POST /api/approvals/{id}/decision` | List cards; `approve`, `edit` (with `args`) or `reject` (with `note`) |
| `GET/POST /api/tasks`, `POST /api/tasks/{id}/steer`, `/stop` | Tasks and their logs; steer or stop one |
| `GET /api/artifacts/{id}`, `/preview`, `/download` | Artifact metadata, a sandboxed HTML preview, the file |
| `POST /api/checkin/run`, `POST /api/brief/run` | Run a check-in or the brief now |
| `POST /api/webhooks/{name}` | Push an event into a webhook connector |
| `GET /api/connections`, `POST /api/connections/{name}/authorize`, `DELETE /api/connections/{name}` | Connections: list, start an OAuth sign-in, disconnect |
| `GET/POST/DELETE /api/memory`, `GET /api/skills` | Memory and skills |
| `GET /api/activity`, `/api/events`, `/api/audit`, `/api/outbox`, `/api/config` | Timeline, triaged events, audit, demo outbox, effective config |
| `POST /api/voice/transcribe` | Server-side speech to text (when `voice.provider` is set) |
| `GET /api/push/vapid`, `GET/POST/DELETE /api/push/subscriptions`, `POST /api/push/test` | Web Push key, devices, test |
| `GET/PUT /api/settings/notify` | Urgency bar, quiet hours, hourly cap, Web Push on/off, live |

The full list, entity shapes and event types are in [docs/SPEC.md §10](docs/SPEC.md#10-api).

## Development

```bash
uv sync                                   # dev tools included
uv run ruff check . && uv run ruff format --check .
uv run pytest -q                          # unit + integration tests, all in mock mode
uv run pytest -q tests/test_scripted_day.py   # the end-to-end acceptance day
uv sync --extra deepagents --extra openai-agents && uv run pytest tests/test_harnesses.py
TANDEM_TEST_DATABASE_URL=postgresql+psycopg://… uv run pytest -q   # the same suite on Postgres

cd frontend
npm ci && npm run check && npm test && npm run build
npm run dev                               # Vite dev server; proxies /api to :8000
```

The **scripted day** (`tests/test_scripted_day.py`) is the acceptance test: a morning check-in
and brief with drafts for review, an urgent email that pushes a notification while bulk mail
stays silent, a sandboxed task that builds a spreadsheet, an approval that is edited, approved and
executed (and the edit learned as a preference), and a draft that expires as a denial.

CI (`.github/workflows/ci.yml`) runs lint; tests on Linux (Python 3.11–3.13), macOS and Windows;
the harness tests with the real Deep Agents and OpenAI Agents SDKs; the suite on Postgres; the
frontend checks, tests and build; and a Docker build with a smoke test.

## Limits

Tandem is v0.1. Known gaps:

- The Claude Agent SDK harness is tested against a fake SDK module; it needs Claude Code
  installed to run for real.
- Web Push needs HTTPS (localhost excepted) and, on iOS/iPadOS 16.4+, the app added to the Home
  Screen; the browser must be running on desktop.
- The local sandbox confines paths and limits resources, but does not isolate the network; use
  Docker for untrusted work.
- Memory search is term overlap, not embeddings.
- Single user per instance.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
