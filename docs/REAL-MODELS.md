# Real models: measure first, then a one-week pilot

Everything in Tandem's test suite runs on the deterministic mock. That proves the plumbing, not the
judgment. This guide is how to find out whether a real model triages *your* inbox well, and how to run a
one-week pilot without surprises.

CI has no model keys: the commands and metrics are tested with the mock, a deterministic "noisy" fake
and recorded provider responses. One real run on a small free model is recorded in section 3.1.

## 1. What's ready

- **Providers**: Anthropic (Messages API), OpenAI (Chat Completions), Gemini, and any OpenAI-compatible
  endpoint: Nutanix Enterprise AI (NAI), vLLM, Ollama, LM Studio, LiteLLM. Chosen **per role**
  (`triage`, `brief`, `chat`, `worker`), so triage can run on a small, cheap model.
- **Labeled data** in `examples/evals/`:
  - `triage.jsonl`: 150 realistic synthetic events for a two-shop bike business (email, calendar
    changes, Slack, payments, security alerts), each labeled `ignore`, `brief`, `notify` or `act` with a
    reason and tags. It includes traps: phishing with fake urgency, prompt injection, VIP small talk,
    a declined card that duplicates another, a failed payment that retries by itself.
  - `briefs.jsonl`: 10 brief scenarios with the points a good brief must mention and must not.
- **`tandem eval triage`** runs the *real* triage pipeline (rules, then the model, then the combine step)
  and reports accuracy, the confusion matrix, precision and recall per label and for "interrupt"
  (notify + act), **missed urgent** with the ids, false alarms and dropped items.
- **`tandem eval brief`** writes each scenario's brief with the real prompt and has a second model grade
  it on a 1-5 rubric (coverage, prioritization, faithfulness, concision, actionability), plus a
  deterministic key-point coverage and leak check.
- **`tandem eval export` / `tandem eval stats`** turn a real week into a labeling file and the pilot
  numbers below.
- Results land on the **Evals** page and under `data/evals/`.

## 2. Baseline offline

```bash
uv run tandem eval triage --model mock      # the rule-based mock: a floor, not a target
uv run tandem eval triage --model noisy     # proves the metrics catch errors (30% of answers changed)
uv run tandem eval brief --model mock --judge mock
```

The mock scores about 54% accuracy with 12 missed urgent items on the 150-event set: it calls most
automated notices "brief" and misses urgency that has no trigger words ("water coming through the
ceiling"). That gap is what a real model has to close.

## 3. Pick models and run the evals

Model names below are examples. Check each vendor's own model list and pricing page before you pick.

**Nutanix Enterprise AI** (stays on your cluster; the endpoint name is the model):

```bash
export NAI_API_KEY=...   # the endpoint's API key from "View Sample Request"
uv run tandem eval triage --model openai_compatible/llama-3-1-8b-instruct \
  --base-url https://nai.example.local/api/v1 --api-key-env NAI_API_KEY
```

**Claude**:

```bash
export ANTHROPIC_API_KEY=...
uv run tandem eval triage --model anthropic/claude-haiku-5-5
uv run tandem eval brief  --model anthropic/claude-sonnet-5-5 --judge openai/gpt-5
```

**GPT**:

```bash
export OPENAI_API_KEY=...
uv run tandem eval triage --model openai/gpt-5-mini
```

Or name a role or profile from your `tandem.yaml` (`--model triage`, `--model cheap`) to test exactly
what you will deploy. `--json result.json` keeps everything; `--fail-under 0.8` makes it a CI gate.

Grade briefs with a judge **from a different model family** than the writer: models are kinder to
their own style.

### 3.1 First real run (October 2026)

A small free reasoning model through OpenRouter's OpenAI-compatible endpoint, run once on the full set:

```bash
uv run tandem eval triage --model openai_compatible/nvidia/nemotron-3.5-lightning:free \
  --base-url https://openrouter.ai/api/v1 --api-key-env OPENROUTER_API_KEY \
  --max-tokens 16000 --extra-body '{"reasoning": {"effort": "low"}}'
```

| Metric | Mock (rules) | nemotron-3.5-lightning (free) | Bar |
|---|---|---|---|
| Accuracy | 0.54 | 0.58 | ≥ 0.75 |
| Macro-F1 | | 0.53 | |
| Interrupt precision | 0.71 | 0.75 | ≥ 0.70 |
| Interrupt recall | 0.78 | 0.67 | ≥ 0.95 |
| Missed urgent | 12 | 18 | ≤ 2 |
| False alarms | 17 | 12 | |
| `act` recall | | 0.23 (3 of 13) | |
| Model failures (batch fell back to rules) | 0 | 1 of 8 | 0 |
| Time, tokens | instant | 933 s, 17k in / 103k out | |

What it shows:

- **The harness works end to end on a real endpoint**: batching, JSON parsing, the rules floor and
  the combine step, the error path (one batch of malformed JSON fell back to rules, as designed).
- **A small model is not enough for triage.** It is more precise than the rules but misses more:
  it pushes real requests down to `brief`, gets `act` right only 3 times in 13, and does worst on the
  `vip` (0.36), `money` (0.43), `security` (0.43) and `trap` (0.43) tags.
- **Reasoning models need room.** At `max_tokens: 4096` it spent the whole budget thinking and
  returned nothing (`finish_reason: length`); Tandem now says so plainly. `extra_body` passes the
  provider's reasoning-effort setting, and `--max-tokens` raises the budget.

Next: the same command with a mid-size model (NAI Llama 3.1 70B, Claude Haiku, GPT mini) is the
real baseline. Expect most of the gain on `act` recall and the trap tags.

### Bar to clear before the pilot

| Metric | Target | Why |
|---|---|---|
| Interrupt recall (notify + act) | ≥ 0.95 (≤ 2 missed of 54) | Missing urgent things is the expensive error |
| Missed urgent | ≤ 2, none of them money or security | Read the listed ids; they tell you what to add to rules or preferences |
| Interrupt precision | ≥ 0.70 | Below that, people stop reading notifications |
| Accuracy | ≥ 0.75 | Overall sanity; brief vs ignore confusions are cheap |
| `trap`, `injection`, `phishing` tags | ≥ 0.6 | Fake urgency must not page anyone |
| Brief judge overall | ≥ 4.0 / 5, coverage ≥ 0.9, leaks 0 | The brief is the daily habit |
| Model failures | 0 | Any fallback to rules-only means the model's JSON is unreliable |

If a small model misses the bar, try the next size up for triage before tuning prompts: triage runs
every check-in, so cost scales with it, but one missed payment costs more than a month of tokens.

## 4. One-week pilot on a real inbox

**Who**: one person, their real Gmail or Outlook, Calendar, and optionally Slack (see
[CONNECTORS.md](CONNECTORS.md)). **Where**: a laptop or a small VM with HTTPS (needed for Web Push and
connector push; polling works without it).

| Day | Mode | What the person does (10 minutes) |
|---|---|---|
| 0 | Setup | Connect apps, install the PWA, enable push ([NOTIFICATIONS.md](NOTIFICATIONS.md)), add VIPs and mutes as memory (`vip: ...`, `mute: ...`). Keep the default policy: every send, payment, post and delete asks. Turn on telemetry if you run ambientq ([QUALITY.md](QUALITY.md)). |
| 1-2 | **Shadow**: `min_urgency: critical` | Nothing interrupts except critical. Each evening, open Activity and note anything it should have told you about. Morning brief on. |
| 3-5 | **Live**: `min_urgency: high` | Push on. Reply to notifications or ignore them; approve or reject drafts. Note "shouldn't have pinged me" and "should have told me". |
| 6-7 | **Delegate** | Hand it real tasks in the thread ("reply to Priya with two times", "summarize this week's sales"). Watch approvals and edits. |
| 7 | Review | `tandem eval export --since 7d --out week.jsonl`; correct the `label` column (the file starts with what Tandem did); then `tandem eval triage --dataset week.jsonl --model triage` gives real-inbox precision and recall. `tandem eval stats --since 7d` for the rest. |

Keep `week.jsonl` local: it is your real mail. It is also the start of a personal eval set: rerun it
whenever you change the model, the prompt or the rules.

### What to watch

| Signal | Where | Healthy |
|---|---|---|
| Interruptions per day | `tandem eval stats`, Activity (notify level) | 1-4 |
| "Should have told me" (missed urgent) | your notes, corrected labels | 0-1 per week |
| False alarms | corrected labels | < 30% of notifications |
| Notify latency (event to ping) | `tandem eval stats` (`notify_latency_min`) | median ≤ poll interval; seconds with push |
| Brief: items acted on | your notes | most "needs you" items get acted on the same day |
| Approvals: approved / edited / rejected / expired | `tandem eval stats`, `/api/audit` | edits fall during the week (preferences learned); expired ≈ 0 |
| Task success | Today, `/api/tasks` | ≥ 80% done without a retry |
| Triage fallbacks to rules-only | `tandem eval stats` (`triage_fallbacks`), Activity warnings | 0 |
| Tokens and cost per role per day | provider dashboard; telemetry spans carry usage | within budget; triage dominates calls, worker dominates tokens |
| Push delivery | Settings > Notifications | no dropped devices |
| Recurring agent mistakes | ambientq insights | trending down after fixes |

### Go / no-go

Go for daily use when, over days 3-7: no missed money or security item, ≤ 4 interruptions per day,
the brief is read every morning, no approval expired unnoticed, and corrected-label interrupt recall is
≥ 0.9. Otherwise, look at the misses: a preference or a VIP fixes most; a bigger triage model fixes the
rest.

## 5. Privacy and safety during the pilot

- Event content goes to the triage and brief models. With NAI or another self-hosted endpoint it stays
  on your infrastructure; with a cloud API it goes to that vendor under their data terms.
- Tokens for connected apps never reach a model (SPEC §15.1); tool results are redacted.
- Inbound content is marked as untrusted data in every prompt, and every outward action waits for your
  approval, so a malicious email can at most propose an action you see on a card.
- Telemetry is off by default; when on, it carries redacted prompts to your own collector or ambientq.
