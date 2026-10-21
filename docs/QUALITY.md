# Quality loop: Tandem traces graded by ambientq

Evals ([REAL-MODELS.md](REAL-MODELS.md)) measure Tandem on a fixed dataset. The quality loop watches
what it actually does every day: Tandem emits OpenTelemetry traces, and
[ambientq](https://github.com/script-repo/nai-recon/tree/ambientq) grades them against a description
of Tandem's own prompts, tools and policy, then groups the problems it finds into insights you can act
on (a prompt line to change, a tool description to fix, a rule to add).

Everything here is **off by default** and stays on your infrastructure.

## 1. What Tandem emits

With telemetry on, Tandem posts OTLP/HTTP JSON traces that follow the
[OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/):

| Span | When | Key attributes |
|---|---|---|
| `invoke_agent tandem` (root, one trace each) | each triage batch, morning brief, task and chat reply | `gen_ai.agent.name`, `gen_ai.conversation.id`, `tandem.run.kind` (`triage`, `brief`, `task`, `chat`) |
| `chat {model}` | each model call | `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.*_tokens`, `gen_ai.system_instructions`, `gen_ai.input.messages`, `gen_ai.output.messages` |
| `execute_tool {name}` | each tool call, refused ones included | `gen_ai.tool.name`, `gen_ai.tool.call.id`, arguments, result, `tandem.approval.decision`, `error.type` |

Prompts and tool I/O pass Tandem's redactor first (API keys, OAuth tokens and bearer headers are
masked), and each attribute is capped at 32k characters. `capture_content: false` sends metadata only
(no prompts), which keeps cost and latency dashboards but gives ambientq nothing to grade.

Export never slows the agent: spans are batched every 2 seconds in the background, the queue is
bounded, and a collector that is down only costs a log line and the dropped spans.

## 2. Turn it on

In `tandem.yaml`:

```yaml
telemetry:
  enabled: true
  endpoint: http://localhost:8080/v1/traces   # ambientq, or any OTLP/HTTP collector
  headers_env: AMBIENTQ_HEADERS               # optional: env var with "authorization=Bearer ..."
  capture_content: true
  team: personal                              # optional ambientq.team resource attribute
```

Or with no config edit: `TANDEM_TELEMETRY=1` plus the standard `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`
(or `OTEL_EXPORTER_OTLP_ENDPOINT`, to which `/v1/traces` is added) and `OTEL_EXPORTER_OTLP_HEADERS`.
`TANDEM_TELEMETRY=0` forces it off. Any OTLP/HTTP backend that accepts JSON works: an OpenTelemetry
Collector, Jaeger, Grafana Tempo, Langfuse, Phoenix.

## 3. The harness ambientq grades against

`harness.yaml` at the repo root tells ambientq what "correct" means for Tandem:

| File | What it holds | Source |
|---|---|---|
| `src/tandem/prompts/*.md` | the persona, rules, triage, brief, chat and worker prompts | the files the engine loads, so a fix ambientq proposes lands in the real prompt |
| `harness/tools.yaml` | every tool (built-in and first-party connectors) as an OpenAI function, with Tandem's tags and policy verdict | generated |
| `harness/guardrails.yaml` | the redaction patterns as forbidden output, the approval policy as rules | generated |
| `harness/agent.yaml` | `max_turns` and other limits | generated |

The generated files come from `uv run tandem harness export`. A test fails when they are stale, so add
a tool or change the policy, run the export, and commit the result.

## 4. Run ambientq beside Tandem (Docker Compose)

```bash
TANDEM_TELEMETRY=1 docker compose --profile quality up -d --build
```

This adds two services to the default Tandem one:

- **ambientq**: built from the `ambientq` branch of `script-repo/nai-recon`. It receives traces on
  `http://ambientq:8080/v1/traces` (Tandem's default endpoint in the compose file) and serves its
  dashboard on http://localhost:8080. It reads `deploy/ambientq.yaml` and mounts this checkout
  read-only as the harness.
- **ambientq-eval**: grades new conversations every 10 minutes (`AMBIENTQ_EVERY_S` to change).
  Without a judge model it runs ambientq's **deterministic checks**, which need no LLM: unknown tools,
  arguments that break a tool's schema, tool calls with no result, tool errors the answer glosses over,
  forbidden patterns in output, too many steps, and runs that end without an answer.

To add the **LLM judge** (it grades each sampled conversation against a rubric built from the
prompts: did triage interrupt for the right things, did the brief stay faithful, did the agent respect
approvals), point it at any OpenAI-compatible endpoint, ideally a different model family than the one
Tandem runs on:

```bash
export AMBIENTQ_LLM_BASE_URL=https://nai.example.local/api/v1   # NAI, vLLM, OpenAI...
export AMBIENTQ_LLM_MODEL=llama-3-1-70b-judge
export AMBIENTQ_LLM_API_KEY=...
TANDEM_TELEMETRY=1 docker compose --profile quality up -d
```

Without Docker: `pip install "ambientq @ git+https://github.com/script-repo/nai-recon.git@ambientq"`,
then `AMBIENTQ_HARNESS_PATH=. AMBIENTQ_STORE_URL=sqlite:///ambientq.db ambientq -c deploy/ambientq.yaml serve`
and, from time to time, `ambientq -c deploy/ambientq.yaml evaluate --checks-only` with the same
variables.

## 5. Reading the results

`ambientq insights` (or the dashboard) lists recurring problems, newest first, each with the
conversations behind it and the harness lines it traces back to. Useful loops:

- **A new insight after a prompt or model change** is a regression: ambientq ties it to the harness
  version (a hash of the files above).
- **`tool_results` insights** usually mean a tool's error text is unclear, or the chat prompt needs a
  sharper line about reporting failures.
- **`tool_arguments` insights** mean a tool description or schema invites wrong arguments; fix the
  description in code, re-export the harness.
- `ambientq investigate <insight>` locates the lines and drafts a patch; `ambientq propose` replays it
  against recorded conversations. Nothing is applied without your approval.

## 6. Tested

- `tests/test_telemetry.py`: span structure, OTLP JSON shape, redaction, content off, the disabled
  no-op, export failures, the bounded queue, environment configuration, and a full mock day (triage,
  chat, model and tool spans), all against a fake collector.
- `tests/test_quality_ambientq.py`: a mock Tandem day is exported, decoded by ambientq's own OTLP
  decoder, stored and evaluated against `harness.yaml`. A clean day yields no findings; a planted bad
  turn (an unknown tool, schema-breaking arguments, a failed tool the answer calls "Done!") comes back
  as three findings and insights. It runs when ambientq is installed (`uv sync --extra quality`) and
  skips otherwise; CI's `quality` job installs it from the git branch.

## 7. Known limits

- ambientq grades a conversation once per harness version. A task that is **waiting on an approval**
  when an evaluation runs is graded as unfinished ("ends with a tool call") and is not re-graded when it
  completes. Treat `completion` findings on tasks with a pending approval as noise for now; the fix
  belongs in ambientq (skip conversations whose `invoke_agent` root span has not arrived yet).
- The compose profile builds ambientq from its branch on first start; that needs network access to
  GitHub. It has been checked for syntax here but not run under Docker in CI.
- Triage batches are graded as conversations too, but the deterministic checks only see their JSON
  answer; judging triage *decisions* needs the LLM judge or the labeled evals.
