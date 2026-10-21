<script lang="ts">
  import ArtifactCard from './cards/ArtifactCard.svelte';
  import { sortedArtifacts } from '../lib/reducer';
  import { app, errorMessage } from '../lib/store.svelte';

  let loading = $state(true);
  let error = $state<string | null>(null);
  let kind = $state('all');

  const all = $derived(sortedArtifacts(app.data));
  const kinds = $derived(['all', ...new Set(all.map((a) => a.kind))]);
  const shown = $derived(kind === 'all' ? all : all.filter((a) => a.kind === kind));

  $effect(() => {
    app.loadArtifacts()
      .catch((e) => (error = errorMessage(e)))
      .finally(() => (loading = false));
  });
</script>

<div class="page">
  <div class="page-head">
    <h1>Library</h1>
    {#if kinds.length > 2}
      <div class="filters" role="group" aria-label="Filter by kind">
        {#each kinds as k (k)}
          <button class="chip" aria-pressed={kind === k} onclick={() => (kind = k)}>{k === 'all' ? 'All' : k.toUpperCase()}</button>
        {/each}
      </div>
    {/if}
  </div>
  {#if error}
    <p class="empty" role="alert">{error}</p>
  {:else if loading && !all.length}
    <p class="empty">Loading…</p>
  {:else if !shown.length}
    <p class="empty">Files the agent makes (sheets, decks, docs, pages) land here.</p>
  {:else}
    <div class="grid">
      {#each shown as a (a.id)}<ArtifactCard artifact={a} />{/each}
    </div>
  {/if}
</div>

<style>
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 12px;
  }
  .filters {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
</style>
