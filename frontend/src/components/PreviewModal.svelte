<script lang="ts">
  import Icon from './Icon.svelte';
  import { artifactUrls } from '../lib/api';
  import { formatBytes } from '../lib/format';
  import { app } from '../lib/store.svelte';

  let dialog: HTMLDialogElement | undefined = $state();
  const a = $derived(app.preview);

  $effect(() => {
    if (!dialog) return;
    if (a && !dialog.open) dialog.showModal();
    else if (!a && dialog.open) dialog.close();
  });
</script>

<dialog bind:this={dialog} onclose={() => (app.preview = null)} aria-labelledby="preview-title">
  {#if a}
    <header>
      <div class="title">
        <h2 id="preview-title">{a.title}</h2>
        <span class="faint">{a.filename} · {formatBytes(a.size)}</span>
      </div>
      <a class="btn sm" href={artifactUrls(a.id).download} download={a.filename}><Icon name="download" size={14} /> Download</a>
      <button class="icon-btn" onclick={() => (app.preview = null)} aria-label="Close preview"><Icon name="x" /></button>
    </header>
    <!-- no allow-scripts, no allow-same-origin: the preview can't run code or reach the app -->
    <iframe title="Preview of {a.title}" src={artifactUrls(a.id).preview} sandbox="" referrerpolicy="no-referrer"></iframe>
  {/if}
</dialog>

<style>
  dialog {
    width: min(960px, calc(100vw - 24px));
    height: min(86vh, 1000px);
    padding: 0;
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--surface);
    color: var(--text);
    box-shadow: var(--shadow-lg);
    overflow: hidden;
  }
  dialog[open] {
    display: flex;
    flex-direction: column;
  }
  dialog::backdrop {
    background: var(--scrim);
    backdrop-filter: blur(2px);
  }
  header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 10px 10px 18px;
    border-bottom: 1px solid var(--border);
  }
  .title {
    flex: 1;
    min-width: 0;
  }
  h2 {
    font-size: 16px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .title span {
    font-size: 12.5px;
  }
  a.btn {
    text-decoration: none;
  }
  iframe {
    flex: 1;
    width: 100%;
    border: 0;
    background: #fff;
  }
</style>
