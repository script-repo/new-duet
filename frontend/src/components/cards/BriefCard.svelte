<script lang="ts">
  import Icon from '../Icon.svelte';
  import { renderMarkdown } from '../../lib/markdown';
  import { shortDate } from '../../lib/format';
  import type { Brief } from '../../lib/types';

  let { brief, compact = false }: { brief: Brief; compact?: boolean } = $props();
  const needs = $derived(brief.items.filter((i) => i.needs_you).length);
</script>

<article class="card brief" class:compact aria-label="Brief for {brief.date}">
  <header>
    <span class="icon"><Icon name="sun" size={17} /></span>
    <span class="label">Brief · {shortDate(brief.date + 'T12:00:00')}</span>
    {#if needs}<span class="badge accent">{needs} need{needs === 1 ? 's' : ''} you</span>{/if}
  </header>
  <p class="greeting">{brief.greeting}</p>
  {#if brief.items.length}
    <ul>
      {#each brief.items as item, i (i)}
        <li class:needs={item.needs_you}>
          <div class="topic">
            {item.topic}
            {#if item.needs_you}<span class="badge accent">needs you</span>{/if}
          </div>
          <div class="md item-summary">{@html renderMarkdown(item.summary)}</div>
        </li>
      {/each}
    </ul>
  {/if}
</article>

<style>
  .brief {
    padding: 16px 18px;
    background: linear-gradient(180deg, color-mix(in srgb, var(--accent-soft) 55%, var(--surface)) 0%, var(--surface) 70%);
  }
  header {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .icon {
    color: var(--accent);
    display: grid;
  }
  .label {
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-3);
    flex: 1;
  }
  .greeting {
    font-family: var(--serif);
    font-size: 19px;
    line-height: 1.4;
    margin: 10px 0 6px;
  }
  .compact .greeting {
    font-size: 16px;
  }
  ul {
    list-style: none;
    margin: 8px 0 0;
    padding: 0;
    display: grid;
    gap: 2px;
  }
  li {
    padding: 8px 0 8px 12px;
    border-left: 3px solid var(--border);
  }
  li.needs {
    border-left-color: var(--accent);
  }
  .topic {
    font-weight: 650;
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }
  .item-summary {
    font-size: 14px;
    color: var(--text-2);
  }
</style>
