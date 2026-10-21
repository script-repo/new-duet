<script lang="ts">
  import Icon from './Icon.svelte';
  import StatTiles from './StatTiles.svelte';
  import { clockTime } from '../lib/format';
  import { app } from '../lib/store.svelte';

  const today = $derived(app.data.today);
  const needs = $derived((today?.needs_you ?? []).map((a) => app.data.approvals[a.id] ?? a).filter((a) => a.state === 'pending'));
  const running = $derived((today?.in_progress ?? []).map((t) => app.data.tasks[t.id] ?? t));
</script>

<aside class="summary" aria-label="Today at a glance">
  <div class="head">
    <h2>Today</h2>
    <button class="btn sm ghost" onclick={() => app.navigate('today')}>Open <Icon name="chevron" size={14} /></button>
  </div>
  {#if today}
    {#if today.brief}<p class="greeting">{today.brief.greeting}</p>{/if}
    <StatTiles stats={today.stats} compact />

    <h3 class="section-title">Needs you</h3>
    {#if needs.length}
      <ul>
        {#each needs as a (a.id)}<li><span class="dot accent"></span>{a.title}</li>{/each}
      </ul>
    {:else}<p class="faint small">Nothing waiting.</p>{/if}

    <h3 class="section-title">In progress</h3>
    {#if running.length}
      <ul>
        {#each running as t (t.id)}<li><span class="dot info"></span>{t.title}<span class="faint"> · {t.state}</span></li>{/each}
      </ul>
    {:else}<p class="faint small">No tasks running.</p>{/if}

    <h3 class="section-title">Coming up</h3>
    {#if today.upcoming.length}
      <ul>
        {#each today.upcoming.slice(0, 5) as c, i (c.id ?? i)}
          <li><span class="time">{c.all_day ? 'All day' : clockTime(c.start)}</span>{c.title ?? c.summary}</li>
        {/each}
      </ul>
    {:else}<p class="faint small">Calendar clear.</p>{/if}
  {:else}
    <p class="faint small">Loading…</p>
  {/if}
</aside>

<style>
  .summary {
    padding: 18px 18px 24px;
    overflow-y: auto;
    height: 100%;
  }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
  }
  h2 {
    font-family: var(--serif);
    font-size: 21px;
    font-weight: 600;
  }
  .greeting {
    font-family: var(--serif);
    font-size: 15.5px;
    margin: 0 0 14px;
    color: var(--text-2);
  }
  .section-title {
    margin: 20px 0 8px;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 7px;
    font-size: 14px;
  }
  li {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    flex: none;
    transform: translateY(-1px);
  }
  .dot.accent {
    background: var(--accent);
  }
  .dot.info {
    background: var(--info);
  }
  .time {
    font-size: 12.5px;
    font-weight: 650;
    color: var(--text-2);
    min-width: 58px;
    font-variant-numeric: tabular-nums;
  }
  .small {
    font-size: 13.5px;
    margin: 0;
  }
</style>
