<script lang="ts">
  import Icon from '../Icon.svelte';
  import { renderMarkdown } from '../../lib/markdown';
  import type { Message } from '../../lib/types';

  let { message }: { message: Message } = $props();
  const urgency = $derived(message.card?.urgency ?? 'normal');
</script>

<article class="card note u-{urgency}" aria-label="{urgency} notification">
  <span class="stripe" aria-hidden="true"></span>
  <div class="body">
    <div class="label"><Icon name="bell" size={14} /> {urgency === 'normal' ? 'Heads up' : urgency}</div>
    <div class="md">{@html renderMarkdown(message.text)}</div>
  </div>
</article>

<style>
  .note {
    display: flex;
    overflow: hidden;
  }
  .stripe {
    width: 5px;
    flex: none;
    background: var(--info);
  }
  .u-high .stripe {
    background: var(--warn);
  }
  .u-critical .stripe {
    background: var(--danger);
  }
  .u-low .stripe {
    background: var(--border-strong);
  }
  .body {
    padding: 12px 16px;
    min-width: 0;
  }
  .label {
    display: flex;
    gap: 6px;
    align-items: center;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--info);
    margin-bottom: 4px;
  }
  .u-high .label {
    color: var(--warn);
  }
  .u-critical .label {
    color: var(--danger);
  }
</style>
