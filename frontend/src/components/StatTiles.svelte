<script lang="ts">
  import type { TodayStats } from '../lib/types';
  let { stats, compact = false }: { stats: TodayStats; compact?: boolean } = $props();
  const tiles = $derived([
    { label: 'Events triaged', value: stats.events_today },
    { label: 'Ignored', value: stats.ignored },
    { label: 'Notified', value: stats.notified },
    { label: 'Tasks done', value: stats.tasks_done },
  ]);
</script>

<ul class="tiles" class:compact aria-label="Today in numbers">
  {#each tiles as t (t.label)}
    <li class="tile">
      <span class="value">{t.value}</span>
      <span class="label">{t.label}</span>
    </li>
  {/each}
</ul>

<style>
  .tiles {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
  }
  .tile {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .value {
    font-family: var(--serif);
    font-size: 28px;
    font-weight: 600;
    line-height: 1.1;
    font-variant-numeric: tabular-nums;
  }
  .label {
    font-size: 12.5px;
    color: var(--text-2);
  }
  .compact {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
  .compact .tile {
    padding: 8px 10px;
  }
  .compact .value {
    font-size: 21px;
  }
  @media (max-width: 560px) {
    .tiles:not(.compact) {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
</style>
