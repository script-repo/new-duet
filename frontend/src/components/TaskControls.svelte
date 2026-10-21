<script lang="ts">
  import Icon from './Icon.svelte';
  import { app } from '../lib/store.svelte';
  import type { Task } from '../lib/types';

  let { task, steerable = true }: { task: Task; steerable?: boolean } = $props();
  let text = $state('');
  let busy = $state(false);
  const inputId = $derived(`steer-${task.id}`);

  async function steer(e: SubmitEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    busy = true;
    if (await app.steer(task, t)) text = '';
    busy = false;
  }

  async function stop() {
    busy = true;
    await app.stop(task);
    busy = false;
  }
</script>

<form class="controls" onsubmit={steer}>
  {#if steerable}
    <label for={inputId} class="sr-only">Steer “{task.title}”</label>
    <input id={inputId} class="input" placeholder="Steer: add a detail or change course…" bind:value={text} disabled={busy} />
    <button class="btn sm" type="submit" disabled={busy || !text.trim()}>Steer</button>
  {/if}
  <button class="btn sm danger" type="button" onclick={stop} disabled={busy}><Icon name="stop" size={13} /> Stop</button>
</form>

<style>
  .controls {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-top: 10px;
  }
  .input {
    flex: 1;
    min-width: 0;
    padding: 5px 10px;
    font-size: 14px;
  }
</style>
