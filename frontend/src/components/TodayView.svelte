<script lang="ts">
  import Icon from './Icon.svelte';
  import StatTiles from './StatTiles.svelte';
  import ApprovalCard from './cards/ApprovalCard.svelte';
  import BriefCard from './cards/BriefCard.svelte';
  import TaskCard from './cards/TaskCard.svelte';
  import { clockTime, duration, greetingFor, relativeTime } from '../lib/format';
  import { app } from '../lib/store.svelte';
  import { urgencyTone } from '../lib/ui';

  const today = $derived(app.data.today);
  // live entities win over the copies in the Today payload
  const needs = $derived((today?.needs_you ?? []).map((a) => app.data.approvals[a.id] ?? a).filter((a) => a.state === 'pending'));
  const inProgress = $derived((today?.in_progress ?? []).map((t) => app.data.tasks[t.id] ?? t));
  const done = $derived((today?.done ?? []).map((t) => app.data.tasks[t.id] ?? t));
  let busy = $state<'brief' | 'checkin' | null>(null);

  async function run(which: 'brief' | 'checkin') {
    busy = which;
    await (which === 'brief' ? app.runBrief() : app.runCheckin());
    busy = null;
  }

  const dateLabel = $derived(
    today ? new Date(today.date + 'T12:00:00').toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' }) : '',
  );
</script>

<div class="page">
  <div class="page-head">
    <div>
      <h1>{greetingFor(new Date(app.now).getHours())}</h1>
      <p class="muted sub">{dateLabel}</p>
    </div>
    <div class="buttons">
      <button class="btn" onclick={() => run('checkin')} disabled={busy !== null}>
        <Icon name="refresh" size={15} /> {busy === 'checkin' ? 'Checking…' : 'Check in now'}
      </button>
      <button class="btn primary" onclick={() => run('brief')} disabled={busy !== null}>
        <Icon name="sun" size={15} /> {busy === 'brief' ? 'Writing…' : 'Run brief now'}
      </button>
    </div>
  </div>

  {#if !today}
    <p class="empty">Loading today…</p>
  {:else}
    <StatTiles stats={today.stats} />

    <div class="grid">
      <div class="col">
        <section aria-labelledby="t-brief">
          <h2 class="section-title" id="t-brief">Brief</h2>
          {#if today.brief}<BriefCard brief={today.brief} />{:else}<p class="empty">No brief yet today. It arrives on schedule, or run it now.</p>{/if}
        </section>

        <section aria-labelledby="t-needs">
          <h2 class="section-title" id="t-needs">Needs you {#if needs.length}<span class="badge accent">{needs.length}</span>{/if}</h2>
          {#if needs.length}
            <div class="stack">{#each needs as a (a.id)}<ApprovalCard approval={a} />{/each}</div>
          {:else}
            <p class="empty">Nothing waiting on you.</p>
          {/if}
        </section>

        <section aria-labelledby="t-progress">
          <h2 class="section-title" id="t-progress">In progress</h2>
          {#if inProgress.length}
            <div class="stack">{#each inProgress as t (t.id)}<TaskCard task={t} />{/each}</div>
          {:else}
            <p class="empty">No tasks running.</p>
          {/if}
        </section>
      </div>

      <div class="col">
        <section aria-labelledby="t-upcoming">
          <h2 class="section-title" id="t-upcoming">Coming up</h2>
          {#if today.upcoming.length}
            <ul class="list card">
              {#each today.upcoming as c, i (c.id ?? i)}
                <li>
                  <span class="when">{c.all_day ? 'All day' : clockTime(c.start)}</span>
                  <span class="what">
                    {c.title ?? c.summary ?? 'Event'}
                    {#if c.location}<span class="faint"> · {c.location}</span>{/if}
                  </span>
                </li>
              {/each}
            </ul>
          {:else}
            <p class="empty">Your calendar is clear.</p>
          {/if}
        </section>

        <section aria-labelledby="t-done">
          <h2 class="section-title" id="t-done">Done today</h2>
          {#if done.length}
            <ul class="list card">
              {#each done as t (t.id)}
                <li>
                  <span class="when"><Icon name="check-circle" size={15} /></span>
                  <span class="what">{t.title}<span class="faint"> · {duration(t.started_at, t.finished_at)}</span></span>
                </li>
              {/each}
            </ul>
          {:else}
            <p class="empty">Nothing finished yet.</p>
          {/if}
        </section>

        {#if today.notifications.length}
          <section aria-labelledby="t-notes">
            <h2 class="section-title" id="t-notes">Flagged</h2>
            <ul class="list card">
              {#each today.notifications as e (e.id)}
                <li>
                  <span class="when"><span class="badge {urgencyTone(e.urgency)}">{e.urgency}</span></span>
                  <span class="what">
                    {e.title}
                    <span class="faint small">{e.sender ?? e.source} · {relativeTime(e.occurred_at, app.now)}</span>
                    {#if e.triage?.reason}<span class="faint small">{e.triage.reason}</span>{/if}
                  </span>
                </li>
              {/each}
            </ul>
          </section>
        {/if}
      </div>
    </div>
  {/if}
</div>

<style>
  .sub {
    margin: 2px 0 0;
  }
  .buttons {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .grid {
    display: grid;
    grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
    gap: 24px;
    margin-top: 24px;
  }
  .col {
    display: grid;
    gap: 24px;
    align-content: start;
  }
  .stack {
    display: grid;
    gap: 12px;
  }
  .list {
    list-style: none;
    margin: 0;
    padding: 4px 0;
  }
  .list li {
    display: flex;
    gap: 12px;
    padding: 9px 14px;
    align-items: flex-start;
  }
  .list li + li {
    border-top: 1px solid var(--border);
  }
  .when {
    flex: none;
    min-width: 64px;
    font-size: 13px;
    font-weight: 650;
    color: var(--text-2);
    font-variant-numeric: tabular-nums;
    display: inline-flex;
    align-items: center;
  }
  .list li .when :global(svg) {
    color: var(--ok);
  }
  .what {
    min-width: 0;
  }
  .small {
    display: block;
    font-size: 12.5px;
  }
  @media (max-width: 760px) {
    .grid {
      grid-template-columns: 1fr;
    }
  }
</style>
