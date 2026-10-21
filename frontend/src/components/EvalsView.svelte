<script lang="ts">
  import ConfusionMatrix from './ConfusionMatrix.svelte';
  import Icon from './Icon.svelte';
  import { api } from '../lib/api';
  import {
    compactNumber, confusionMatrix, headlineTiles, historyOf, LABELS, modelOptions, pct, score, sparkPath, splitErrors,
    tagRows, TARGETS,
  } from '../lib/evals';
  import { dateTime, relativeTime } from '../lib/format';
  import { app, errorMessage } from '../lib/store.svelte';
  import type { EvalsResponse, JudgeCriterion, TriageError } from '../lib/types';

  const REAL_MODELS_URL = 'https://github.com/script-repo/new-duet/blob/main/docs/REAL-MODELS.md';
  const JUDGE: JudgeCriterion[] = ['coverage', 'prioritization', 'faithfulness', 'concision', 'actionability'];
  const LABEL_TONE: Record<string, string> = { ignore: '', brief: 'info', notify: 'warn', act: 'accent' };
  const OTHER_PAGE = 12;

  let data = $state<EvalsResponse | null>(null);
  let loadError = $state<string | null>(null);
  let model = $state('triage');
  let starting = $state(false);
  let startedAt = $state<number | null>(null);
  let runError = $state<string | null>(null);
  let showAllOther = $state(false);

  const triage = $derived(data?.triage ?? null);
  const brief = $derived(data?.brief ?? null);
  const running = $derived(starting || !!data?.running?.includes('triage'));
  const options = $derived(modelOptions(app.config));
  const matrix = $derived(triage ? confusionMatrix(triage.metrics) : null);
  const tiles = $derived(triage ? headlineTiles(triage) : []);
  const errs = $derived(triage ? splitErrors(triage.errors) : { missed: [], other: [] });
  const tags = $derived(triage ? tagRows(triage.metrics.by_tag) : []);
  const triageHistory = $derived(historyOf(data?.history ?? [], 'triage'));
  const spark = $derived(sparkPath(triageHistory.map((h) => h.headline), 160, 36, 0, 1, 3));
  const sparkLast = $derived(triageHistory.length ? triageHistory[triageHistory.length - 1] : null);
  const elapsed = $derived(startedAt ? Math.max(0, Math.round((app.now - startedAt) / 1000)) : 0);
  const otherShown = $derived(showAllOther ? errs.other : errs.other.slice(0, OTHER_PAGE));
  const labelsOf = $derived(triage?.metrics.labels?.length ? triage.metrics.labels : LABELS);

  async function load() {
    try {
      data = await api.evals();
      loadError = null;
      if (!data.running?.includes('triage') && !starting) startedAt = null;
    } catch (e) {
      loadError = errorMessage(e);
    }
  }

  $effect(() => {
    if (!app.config) app.loadConfig().catch(() => undefined);
    void load();
    return app.listen((ev) => {
      if (ev.type !== 'eval.updated') return;
      starting = false;
      startedAt = null;
      if (ev.data.error) {
        runError = ev.data.error;
        app.toast({ title: 'Eval failed', body: ev.data.error, urgency: 'normal', kind: 'error' });
      } else {
        runError = null;
        app.toast({ title: 'Eval finished', body: `New ${ev.data.kind} results are in.`, urgency: 'normal', kind: 'info' });
      }
      void load();
    });
  });

  // a run started elsewhere (CLI, another tab) shows as running: poll until it's done, in case the stream drops
  $effect(() => {
    if (!running) return;
    startedAt ??= Date.now();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  });

  async function run(e: SubmitEvent) {
    e.preventDefault();
    runError = null;
    starting = true;
    startedAt = Date.now();
    try {
      await api.runTriageEval(model);
      await load();
    } catch (err) {
      runError = errorMessage(err);
      starting = false;
      startedAt = null;
    }
  }
</script>

<div class="page">
  <div class="page-head">
    <div>
      <h1>Evals</h1>
      <p class="muted sub">How well triage sorts what reaches you, measured on a labelled dataset.</p>
    </div>
    <form class="run" onsubmit={run} aria-label="Run a triage eval">
      <label class="sr-only" for="eval-model">Model</label>
      <select id="eval-model" class="select" bind:value={model} disabled={running}>
        {#each options as o (o.value)}<option value={o.value}>{o.label}</option>{/each}
      </select>
      <button class="btn primary" type="submit" disabled={running}>
        <Icon name={running ? 'clock' : 'play'} size={14} /> {running ? `Running… ${elapsed}s` : 'Run eval'}
      </button>
    </form>
  </div>

  <div class="progress-slot" aria-live="polite">
    {#if running}
      <div class="progress" role="progressbar" aria-label="Triage eval running" aria-valuetext="{elapsed} seconds"><span></span></div>
    {/if}
    {#if runError}<p class="run-error" role="alert"><Icon name="alert" size={14} /> {runError}</p>{/if}
  </div>

  {#if loadError && !data}
    <div class="empty" role="alert">
      <p>Couldn’t load evals: {loadError}</p>
      <button class="btn sm" onclick={load}>Try again</button>
    </div>
  {:else if !data}
    <div class="tiles" aria-busy="true" aria-label="Loading evals">
      {#each [0, 1, 2, 3, 4] as i (i)}<div class="tile skeleton"></div>{/each}
    </div>
  {:else if !triage && !brief}
    <div class="empty intro">
      <Icon name="flask" size={28} />
      <p><strong>No eval results yet.</strong></p>
      <p>Run one here with a configured model, or from a shell for any model:</p>
      <pre><code>uv run tandem eval triage --model anthropic/claude-haiku-5-5
uv run tandem eval brief --model triage --judge brief</code></pre>
      <p><a href={REAL_MODELS_URL} target="_blank" rel="noopener">Measuring real models <Icon name="external" size={12} /></a></p>
    </div>
  {:else}
    {#if triage && matrix}
      <section aria-labelledby="e-triage">
        <div class="section-head">
          <h2 id="e-triage" class="section-title">Triage</h2>
          <p class="meta">
            <span class="mono">{triage.provider}/{triage.model_name}</span>
            <span>({triage.model})</span> · {triage.dataset} · {triage.metrics.n} items ·
            <time datetime={triage.ran_at} title={dateTime(triage.ran_at)}>{relativeTime(triage.ran_at, app.now)}</time> ·
            {triage.seconds}s · {compactNumber(triage.usage?.input_tokens)} in / {compactNumber(triage.usage?.output_tokens)} out tokens
            {#if triage.model_failures}<span class="badge danger">{triage.model_failures} model failures</span>{/if}
          </p>
        </div>

        <ul class="tiles" aria-label="Headline numbers">
          {#each tiles as t (t.key)}
            <li class="tile {t.tone}">
              <span class="label">{t.label}</span>
              <span class="value">{t.value}</span>
              <span class="detail">{t.detail}</span>
            </li>
          {/each}
        </ul>

        <div class="grid">
          <div class="card pad">
            <h3>Confusion matrix</h3>
            <ConfusionMatrix {matrix} />
          </div>

          <div class="card pad">
            <h3>Per label</h3>
            <div class="legend" aria-hidden="true">
              <span><i class="sw p"></i>Precision</span>
              <span><i class="sw r"></i>Recall</span>
            </div>
            <table class="bars">
              <caption class="sr-only">Precision and recall per label</caption>
              <thead class="sr-only"><tr><th>Label</th><th aria-hidden="true"></th><th>Precision</th><th>Recall</th><th>F1</th><th>Support</th></tr></thead>
              <tbody>
                {#each labelsOf as l (l)}
                  {@const s = triage.metrics.per_label[l]}
                  {#if s}
                    <tr>
                      <th scope="row">
                        <span class="lname">{l}</span>
                        <span class="faint">n={s.support} · F1 {s.f1.toFixed(2)}</span>
                      </th>
                      <td aria-hidden="true">
                        <span class="bar-row" title="Precision {pct(s.precision)}"><span class="track"><span class="fill p" style:width="{s.precision * 100}%"></span></span><span class="v">{pct(s.precision)}</span></span>
                        <span class="bar-row" title="Recall {pct(s.recall)}"><span class="track"><span class="fill r" style:width="{s.recall * 100}%"></span></span><span class="v">{pct(s.recall)}</span></span>
                      </td>
                      <td class="sr-only">{pct(s.precision)}</td>
                      <td class="sr-only">{pct(s.recall)}</td>
                      <td class="sr-only">{s.f1.toFixed(2)}</td>
                      <td class="sr-only">{s.support}</td>
                    </tr>
                  {/if}
                {/each}
              </tbody>
            </table>
            <p class="faint small">Macro F1 {triage.metrics.macro_f1.toFixed(2)} · interrupt F1 {triage.metrics.interrupt.f1.toFixed(2)}</p>
          </div>
        </div>

        <div class="grid">
          <div class="card pad">
            <h3>Missed urgent <span class="badge {errs.missed.length ? 'danger' : 'ok'}">{triage.metrics.missed_urgent}</span></h3>
            {#if errs.missed.length}
              <ul class="errors">
                {#each errs.missed as e (e.id)}{@render errorItem(e, true)}{/each}
              </ul>
              {#if triage.metrics.missed_urgent > errs.missed.length}
                <p class="faint small">Showing {errs.missed.length}; all ids: <span class="mono">{triage.metrics.missed_urgent_ids.join(', ')}</span></p>
              {/if}
            {:else}
              <p class="ok-text"><Icon name="check-circle" size={15} /> Nothing urgent was held back.</p>
            {/if}
          </div>

          <div class="card pad">
            <h3>By tag</h3>
            <ul class="tags">
              {#each tags as t (t.tag)}
                <li class:warn={t.belowTarget}>
                  <span class="tname">{t.tag}{#if t.trap}<span class="sr-only"> (trap tag)</span>{/if}</span>
                  <span class="track sm"><span class="fill p" style:width="{t.accuracy * 100}%"></span>{#if t.trap}<span class="target" style:left="{TARGETS.tagAccuracy * 100}%" title="target {pct(TARGETS.tagAccuracy)}"></span>{/if}</span>
                  <span class="v">{pct(t.accuracy)}</span>
                  <span class="faint n">n={t.n}</span>
                </li>
              {/each}
            </ul>
            <p class="faint small">Worst first. Tags <span class="mono">trap</span>, <span class="mono">phishing</span> and <span class="mono">injection</span> should reach {pct(TARGETS.tagAccuracy)} (marker).</p>
          </div>
        </div>

        {#if errs.other.length}
          <div class="card pad">
            <h3>Other errors <span class="badge">{errs.other.length}{triage.errors.length >= 60 ? '+' : ''}</span></h3>
            <ul class="errors cols">
              {#each otherShown as e (e.id)}{@render errorItem(e, false)}{/each}
            </ul>
            {#if errs.other.length > OTHER_PAGE}
              <button class="btn sm ghost more" onclick={() => (showAllOther = !showAllOther)} aria-expanded={showAllOther}>
                {showAllOther ? 'Show fewer' : `Show all ${errs.other.length}`}
              </button>
            {/if}
          </div>
        {/if}
      </section>
    {/if}

    {#if brief}
      <section aria-labelledby="e-brief">
        <div class="section-head">
          <h2 id="e-brief" class="section-title">Brief</h2>
          <p class="meta">
            <span class="mono">{brief.model}</span> judged by <span class="mono">{brief.judge}</span> · {brief.dataset} ·
            {brief.metrics.n} scenarios · <time datetime={brief.ran_at} title={dateTime(brief.ran_at)}>{relativeTime(brief.ran_at, app.now)}</time>
            {#if brief.metrics.errors}<span class="badge danger">{brief.metrics.errors} failed</span>{/if}
          </p>
        </div>
        <div class="grid brief">
          <div class="card pad">
            <ul class="tiles three" aria-label="Brief headline numbers">
              <li class="tile {brief.metrics.judge_overall != null && brief.metrics.judge_overall >= 4 ? 'good' : 'neutral'}">
                <span class="label">Judge overall</span><span class="value">{score(brief.metrics.judge_overall)}<small>/5</small></span><span class="detail">target ≥ 4.0</span>
              </li>
              <li class="tile {brief.metrics.coverage != null && brief.metrics.coverage >= 0.9 ? 'good' : 'neutral'}">
                <span class="label">Coverage</span><span class="value">{pct(brief.metrics.coverage)}</span><span class="detail">must-mention points · target ≥ 90%</span>
              </li>
              <li class="tile {brief.metrics.leaks ? 'bad' : 'good'}">
                <span class="label">Leaks</span><span class="value">{brief.metrics.leaks}</span><span class="detail">must-not-mention · target 0</span>
              </li>
            </ul>
            <h3 class="sub-h">Judge means</h3>
            <table class="bars judge">
              <caption class="sr-only">Judge mean score per criterion, out of 5</caption>
              <tbody>
                {#each JUDGE as k (k)}
                  {@const v = brief.metrics.judge_means[k]}
                  <tr>
                    <th scope="row"><span class="lname">{k}</span></th>
                    <td><span class="bar-row"><span class="track"><span class="fill p" style:width="{((v ?? 0) / 5) * 100}%"></span></span><span class="v">{score(v)}</span></span></td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
          <div class="card pad">
            <h3>Scenarios</h3>
            <ul class="scenarios">
              {#each brief.scenarios as sc (sc.id)}
                {@const vals = Object.values(sc.scores ?? {}) as number[]}
                {@const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null}
                <li>
                  <div class="sc-head">
                    <span class="mono faint">{sc.id}</span>
                    <strong class="grow">{sc.title ?? sc.id}</strong>
                    {#if sc.error}<span class="badge danger">failed</span>{:else}
                      <span class="badge {sc.missing?.length ? 'warn' : ''}" title="must-mention points covered">{sc.coverage}</span>
                      <span class="badge {avg != null && avg >= 4 ? 'ok' : avg != null && avg < 3.5 ? 'warn' : ''}" title="judge mean">{score(avg)}</span>
                    {/if}
                  </div>
                  {#if sc.error}<p class="small danger-text">{sc.error}</p>{:else}
                    {#if sc.notes}<p class="small muted">{sc.notes}</p>{/if}
                    {#if sc.missing?.length}<p class="small"><span class="faint">Missing:</span> {sc.missing.join(', ')}</p>{/if}
                    {#if sc.leaks?.length}<p class="small danger-text">Leaked: {sc.leaks.join(', ')}</p>{/if}
                  {/if}
                </li>
              {/each}
            </ul>
          </div>
        </div>
      </section>
    {/if}

    {#if data.history.length}
      <section class="card pad history" aria-labelledby="e-history">
        <div class="hist-head">
          <h3 id="e-history">History</h3>
          {#if triageHistory.length > 1}
            <figure class="spark">
              <svg viewBox="0 0 160 36" width="160" height="36" role="img" aria-label="Triage accuracy over the last {triageHistory.length} runs, latest {pct(sparkLast?.headline)}">
                <line x1="0" x2="160" y1={36 - 3 - TARGETS.accuracy * 30} y2={36 - 3 - TARGETS.accuracy * 30} class="target-line" />
                <path d={spark} />
              </svg>
              <figcaption class="faint small">accuracy, {triageHistory.length} runs · dashed: target</figcaption>
            </figure>
          {/if}
        </div>
        <div class="table-scroll">
          <table class="data">
            <thead><tr><th>When</th><th>Kind</th><th>Model</th><th class="num">Headline</th><th class="num">Missed urgent</th></tr></thead>
            <tbody>
              {#each data.history.slice(0, 10) as h, i (i)}
                <tr>
                  <td class="nowrap">{h.ran_at ? dateTime(h.ran_at) : '–'}</td>
                  <td>{h.kind}</td>
                  <td class="mono">{h.model}</td>
                  <td class="num">{h.kind === 'triage' ? pct(h.headline) : `${score(h.headline)} / 5`}</td>
                  <td class="num">{#if h.missed_urgent == null}<span class="faint">–</span>{:else}<span class:danger-text={h.missed_urgent > 0}>{h.missed_urgent}</span>{/if}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </section>
    {/if}
  {/if}
</div>

{#snippet errorItem(e: TriageError, urgent: boolean)}
  {@const p = { gold: e.gold, pred: e.pred }}
  <li class:urgent>
    <div class="err-head">
      <strong class="grow">{e.title}</strong>
      <span class="pair" aria-label="gold {p.gold}, predicted {p.pred}">
        <span class="badge {LABEL_TONE[p.gold]}">{p.gold}</span><span aria-hidden="true">→</span><span class="badge {urgent ? 'danger' : LABEL_TONE[p.pred]}">{p.pred}</span>
      </span>
    </div>
    <dl>
      <div><dt>Tandem</dt><dd>{e.reason || '–'} <span class="faint">({e.decided_by}{e.urgency ? `, ${e.urgency}` : ''})</span></dd></div>
      <div><dt>Expected</dt><dd>{e.gold_reason || '–'}</dd></div>
    </dl>
    <p class="faint small"><span class="mono">{e.id}</span> · {e.kind}{#if e.tags.length} · {e.tags.join(', ')}{/if}</p>
  </li>
{/snippet}

<style>
  .page {
    max-width: 1180px;
    --series-1: #2a78d6;
    --series-2: #eb6834;
  }
  @media (prefers-color-scheme: dark) {
    .page {
      --series-1: #3987e5;
      --series-2: #d95926;
    }
  }
  .sub {
    margin: 2px 0 0;
  }
  .page-head {
    margin-bottom: 10px;
  }
  .run {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .run .select {
    width: auto;
    max-width: 320px;
  }
  .progress-slot {
    min-height: 14px;
    margin-bottom: 12px;
  }
  .progress {
    height: 4px;
    border-radius: 999px;
    background: var(--surface-2);
    overflow: hidden;
  }
  .progress span {
    display: block;
    width: 35%;
    height: 100%;
    border-radius: inherit;
    background: var(--accent);
    animation: indeterminate 1.3s ease-in-out infinite;
  }
  @keyframes indeterminate {
    from {
      transform: translateX(-100%);
    }
    to {
      transform: translateX(290%);
    }
  }
  .run-error {
    margin: 8px 0 0;
    color: var(--danger);
    font-size: 13.5px;
    display: flex;
    gap: 6px;
    align-items: center;
  }
  section {
    margin-bottom: 26px;
  }
  .section-head {
    display: flex;
    align-items: baseline;
    gap: 6px 14px;
    flex-wrap: wrap;
    margin-bottom: 10px;
  }
  .section-head .section-title {
    margin: 0;
  }
  .meta {
    margin: 0;
    font-size: 12.5px;
    color: var(--text-3);
    display: flex;
    flex-wrap: wrap;
    gap: 0 5px;
    align-items: baseline;
  }
  .tiles {
    list-style: none;
    margin: 0 0 14px;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 10px;
  }
  .tiles.three {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .tile {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-height: 104px;
  }
  .card .tile {
    background: var(--surface-2);
    border-color: transparent;
    min-height: 0;
  }
  .tile.skeleton {
    background: var(--surface-2);
  }
  .tile .label {
    font-size: 12.5px;
    color: var(--text-2);
    font-weight: 550;
  }
  .tile .value {
    font-family: var(--serif);
    font-size: 30px;
    font-weight: 600;
    line-height: 1.1;
    font-variant-numeric: tabular-nums;
  }
  .tile .value small {
    font-size: 15px;
    color: var(--text-3);
    font-weight: 500;
  }
  .tile .detail {
    font-size: 12px;
    color: var(--text-3);
  }
  .tile.bad {
    border-color: color-mix(in srgb, var(--danger) 45%, var(--border));
    background: var(--danger-soft);
  }
  .tile.bad .value,
  .tile.bad .label {
    color: var(--danger);
  }
  .tile.good .label::after {
    content: ' ✓';
    color: var(--ok);
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px;
    margin-bottom: 14px;
  }
  .grid.brief {
    grid-template-columns: minmax(0, 5fr) minmax(0, 6fr);
  }
  .pad {
    padding: 16px 18px;
  }
  h3 {
    font-size: 15px;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .sub-h {
    margin: 16px 0 8px;
  }
  p {
    margin: 0;
  }
  .small {
    font-size: 12.5px;
  }
  .danger-text {
    color: var(--danger);
  }
  .ok-text {
    color: var(--ok);
    display: flex;
    gap: 6px;
    align-items: center;
  }

  /* ---- bars ---- */
  .legend {
    display: flex;
    gap: 14px;
    font-size: 12.5px;
    color: var(--text-2);
    margin: -4px 0 10px;
  }
  .legend span {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .sw {
    width: 10px;
    height: 10px;
    border-radius: 3px;
  }
  .sw.p,
  .fill.p {
    background: var(--series-1);
  }
  .sw.r,
  .fill.r {
    background: var(--series-2);
  }
  table.bars {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 10px;
  }
  .bars th {
    text-align: left;
    font-weight: 500;
    width: 34%;
    padding: 7px 10px 7px 0;
    vertical-align: middle;
  }
  .bars.judge th {
    width: 38%;
  }
  .bars td {
    padding: 7px 0;
  }
  .bars tr + tr th,
  .bars tr + tr td {
    border-top: 1px solid var(--border);
  }
  .lname {
    display: block;
    text-transform: capitalize;
    font-weight: 600;
    font-size: 13.5px;
  }
  .bars th .faint {
    font-size: 11.5px;
  }
  .bar-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .bar-row + .bar-row {
    margin-top: 4px;
  }
  .track {
    position: relative;
    flex: 1;
    height: 8px;
    border-radius: 4px;
    background: var(--surface-2);
  }
  .fill {
    display: block;
    height: 100%;
    border-radius: 4px;
    min-width: 2px;
  }
  .v {
    width: 40px;
    text-align: right;
    font-size: 12.5px;
    font-variant-numeric: tabular-nums;
    color: var(--text);
  }

  /* ---- tags ---- */
  .tags {
    list-style: none;
    margin: 0 0 10px;
    padding: 0;
    display: grid;
    gap: 3px;
    max-height: 360px;
    overflow-y: auto;
  }
  .tags li {
    display: grid;
    grid-template-columns: 92px 1fr 40px 38px;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    padding: 2px 4px;
    border-radius: 6px;
  }
  .tags li.warn {
    background: var(--warn-soft);
  }
  .tname {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .track.sm {
    height: 6px;
  }
  .target {
    position: absolute;
    top: -3px;
    width: 2px;
    height: 12px;
    background: var(--text);
    border-radius: 1px;
  }
  .tags .n {
    font-size: 11.5px;
    text-align: right;
  }

  /* ---- error lists ---- */
  .errors {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 10px;
  }
  .errors.cols {
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr));
  }
  .errors li {
    padding: 10px 12px;
    border-radius: var(--radius-sm);
    background: var(--surface-2);
    border-left: 3px solid var(--border-strong);
    display: grid;
    gap: 6px;
  }
  .errors li.urgent {
    border-left-color: var(--danger);
  }
  .err-head {
    display: flex;
    gap: 10px;
    align-items: flex-start;
  }
  .err-head strong {
    font-size: 14px;
    font-weight: 600;
    overflow-wrap: anywhere;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .pair {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--text-3);
    flex: none;
  }
  dl {
    margin: 0;
    display: grid;
    gap: 3px;
    font-size: 13px;
  }
  dl div {
    display: grid;
    grid-template-columns: 66px 1fr;
    gap: 8px;
  }
  dt {
    color: var(--text-3);
    font-weight: 600;
  }
  dd {
    margin: 0;
  }
  .more {
    margin-top: 10px;
  }

  /* ---- brief ---- */
  .scenarios {
    list-style: none;
    margin: 0;
    padding: 0;
    max-height: 520px;
    overflow-y: auto;
  }
  .scenarios li {
    padding: 9px 0;
    display: grid;
    gap: 3px;
  }
  .scenarios li + li {
    border-top: 1px solid var(--border);
  }
  .sc-head {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .sc-head strong {
    font-size: 14px;
    font-weight: 600;
  }

  /* ---- history ---- */
  .hist-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 8px;
  }
  .hist-head h3 {
    margin: 0;
  }
  .spark {
    margin: 0;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .spark path {
    fill: none;
    stroke: var(--series-1);
    stroke-width: 2;
    stroke-linejoin: round;
    stroke-linecap: round;
  }
  .target-line {
    stroke: var(--text-3);
    stroke-width: 1;
    stroke-dasharray: 3 3;
  }
  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .nowrap {
    white-space: nowrap;
  }

  .intro {
    display: grid;
    justify-items: center;
    gap: 8px;
    padding: 32px 18px;
  }
  .intro pre {
    margin: 4px 0;
    max-width: 100%;
    overflow-x: auto;
    text-align: left;
    background: var(--surface-2);
    color: var(--text);
    padding: 10px 14px;
    border-radius: var(--radius-sm);
    font-size: 12.5px;
  }
  .intro a {
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }

  @media (max-width: 1100px) {
    .tiles {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
  }
  @media (max-width: 899px) {
    .grid,
    .grid.brief {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  @media (max-width: 560px) {
    .tiles {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .tiles.three {
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 6px;
    }
    .tiles.three .tile {
      padding: 10px;
    }
    .tiles.three .value {
      font-size: 24px;
    }
    .tile {
      min-height: 0;
    }
    .run,
    .run .select {
      width: 100%;
      max-width: none;
    }
    .run .btn {
      width: 100%;
    }
  }
</style>
