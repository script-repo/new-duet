<script lang="ts">
  import Icon from './Icon.svelte';
  import TaskControls from './TaskControls.svelte';
  import { clockTime, duration, relativeTime, shortDate } from '../lib/format';
  import { activeTasks } from '../lib/reducer';
  import { app, errorMessage } from '../lib/store.svelte';
  import type { ActivityLevel } from '../lib/types';
  import { levelTone, stateTone } from '../lib/ui';

  const LEVELS: (ActivityLevel | 'all')[] = ['all', 'info', 'notify', 'warn', 'error'];
  let level = $state<ActivityLevel | 'all'>('all');
  let loading = $state(true);
  let error = $state<string | null>(null);
  let more = $state(true);

  const items = $derived(level === 'all' ? app.data.activity : app.data.activity.filter((a) => a.level === level));
  const running = $derived(activeTasks(app.data));

  $effect(() => {
    void Promise.all([app.loadActivity(), app.loadTasks()])
      .then(([n]) => (more = n >= 100))
      .catch((e) => (error = errorMessage(e)))
      .finally(() => (loading = false));
  });

  // fetch logs for each running task once; the stream keeps them current
  const fetched = new Set<string>();
  $effect(() => {
    for (const t of running) {
      if (fetched.has(t.id)) continue;
      fetched.add(t.id);
      app.loadTask(t.id).catch(() => {});
    }
  });

  async function older() {
    const last = app.data.activity[app.data.activity.length - 1];
    if (!last) return;
    loading = true;
    try {
      more = (await app.loadActivity(last.id)) >= 100;
    } catch (e) {
      app.fail(e, 'Could not load older activity');
    } finally {
      loading = false;
    }
  }
</script>

<div class="page">
  <div class="page-head">
    <h1>Activity</h1>
  </div>

  {#if running.length}
    <section aria-labelledby="a-running" class="running">
      <h2 class="section-title" id="a-running">Running now</h2>
      {#each running as t (t.id)}
        {@const logs = app.data.logs[t.id] ?? []}
        <article class="card live" aria-label="Running task {t.title}">
          <header>
            <h3>{t.title}</h3>
            <span class="badge {stateTone(t.state)}">{t.state}</span>
            <span class="faint small">{t.harness} · {t.steps} steps · {duration(t.started_at, null, app.now)}</span>
          </header>
          <ol class="logs" aria-live="polite" aria-label="Live log">
            {#each logs.slice(-12) as l (l.id)}
              <li class="k-{l.kind}"><time datetime={l.ts}>{clockTime(l.ts)}</time><span class="kind">{l.kind}</span><span class="text">{l.text}</span></li>
            {:else}
              <li class="faint">Waiting for the first step…</li>
            {/each}
          </ol>
          <TaskControls task={t} steerable={t.harness !== 'cli'} />
        </article>
      {/each}
    </section>
  {/if}

  <div class="filters" role="group" aria-label="Filter by level">
    {#each LEVELS as l (l)}
      <button class="chip" aria-pressed={level === l} onclick={() => (level = l)}>{l === 'all' ? 'Everything' : l}</button>
    {/each}
  </div>

  {#if error}
    <p class="empty" role="alert">{error}</p>
  {:else if !items.length && !loading}
    <p class="empty">Nothing here yet.</p>
  {:else}
    <ol class="timeline">
      {#each items as a, i (a.id)}
        {#if i === 0 || shortDate(a.ts) !== shortDate(items[i - 1].ts)}
          <li class="date">{shortDate(a.ts)}</li>
        {/if}
        <li class="entry lvl-{a.level}">
          <span class="dot" aria-hidden="true"></span>
          <div class="content">
            <div class="line">
              <span class="title">{a.title}</span>
              {#if a.level !== 'info'}<span class="badge {levelTone(a.level)}">{a.level}</span>{/if}
              <span class="faint kind">{a.kind}</span>
            </div>
            {#if a.detail}<p class="detail">{a.detail}</p>{/if}
          </div>
          <time class="faint" datetime={a.ts} title={a.ts}>{relativeTime(a.ts, app.now)}</time>
        </li>
      {/each}
    </ol>
    {#if more && app.data.activity.length}
      <div class="more"><button class="btn sm" onclick={older} disabled={loading}><Icon name="clock" size={14} /> Older</button></div>
    {/if}
  {/if}
</div>

<style>
  .running {
    display: grid;
    gap: 12px;
    margin-bottom: 26px;
  }
  .live {
    padding: 14px 16px;
    border-left: 4px solid var(--info);
  }
  .live header {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }
  .live h3 {
    font-size: 15px;
  }
  .small {
    font-size: 12.5px;
  }
  .logs {
    list-style: none;
    margin: 10px 0 0;
    padding: 8px 10px;
    background: var(--surface-2);
    border-radius: var(--radius-sm);
    font-family: var(--mono);
    font-size: 12.5px;
    max-height: 240px;
    overflow-y: auto;
    display: grid;
    gap: 3px;
  }
  .logs li {
    display: grid;
    grid-template-columns: auto 56px 1fr;
    gap: 8px;
  }
  .logs time {
    color: var(--text-3);
  }
  .logs .kind {
    color: var(--text-3);
  }
  .logs .text {
    white-space: pre-wrap;
    word-break: break-word;
  }
  .k-error .text {
    color: var(--danger);
  }
  .k-steer .text {
    color: var(--accent);
  }
  .k-tool .kind {
    color: var(--info);
  }
  .filters {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-bottom: 16px;
  }
  .timeline {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .date {
    font-size: 12px;
    font-weight: 700;
    color: var(--text-3);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin: 16px 0 6px;
  }
  .entry {
    position: relative;
    display: flex;
    gap: 12px;
    padding: 9px 0 9px 22px;
    border-left: 2px solid var(--border);
    margin-left: 5px;
  }
  .dot {
    position: absolute;
    left: -6px;
    top: 15px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--surface);
    border: 2px solid var(--border-strong);
  }
  .lvl-notify .dot {
    border-color: var(--accent);
    background: var(--accent);
  }
  .lvl-warn .dot {
    border-color: var(--warn);
    background: var(--warn);
  }
  .lvl-error .dot {
    border-color: var(--danger);
    background: var(--danger);
  }
  .content {
    flex: 1;
    min-width: 0;
  }
  .line {
    display: flex;
    gap: 8px;
    align-items: baseline;
    flex-wrap: wrap;
  }
  .title {
    font-weight: 600;
  }
  .kind {
    font-size: 12px;
  }
  .detail {
    margin: 2px 0 0;
    font-size: 13.5px;
    color: var(--text-2);
    white-space: pre-wrap;
    word-break: break-word;
  }
  .entry time {
    font-size: 12px;
    white-space: nowrap;
    flex: none;
  }
  .more {
    display: flex;
    justify-content: center;
    margin-top: 14px;
  }
</style>
