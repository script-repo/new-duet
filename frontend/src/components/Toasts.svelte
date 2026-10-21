<script lang="ts">
  import Icon from './Icon.svelte';
  import { app } from '../lib/store.svelte';
  import { urgencyTone } from '../lib/ui';
</script>

<div class="toasts" aria-live="assertive" aria-atomic="false">
  {#each app.toasts as t (t.id)}
    <div class="toast card {t.kind === 'error' ? 'danger' : t.kind === 'notify' ? urgencyTone(t.urgency) : 'info'}" role={t.kind === 'error' ? 'alert' : 'status'}>
      <span class="ico"><Icon name={t.kind === 'error' ? 'alert' : t.kind === 'notify' ? 'bell' : 'check-circle'} size={17} /></span>
      <div class="body">
        <strong>{t.title}</strong>
        {#if t.body}<p>{t.body}</p>{/if}
      </div>
      {#if t.kind === 'notify'}
        <button class="btn sm ghost" onclick={() => (app.navigate('thread'), app.dismiss(t.id))}>View</button>
      {/if}
      <button class="icon-btn close" onclick={() => app.dismiss(t.id)} aria-label="Dismiss"><Icon name="x" size={15} /></button>
    </div>
  {/each}
</div>

<style>
  .toasts {
    position: fixed;
    z-index: 50;
    top: 14px;
    right: 14px;
    display: grid;
    gap: 8px;
    width: min(380px, calc(100vw - 28px));
    pointer-events: none;
  }
  .toast {
    pointer-events: auto;
    display: flex;
    gap: 10px;
    align-items: flex-start;
    padding: 12px 8px 12px 14px;
    box-shadow: var(--shadow-lg);
    border-left: 4px solid var(--info);
    animation: slide 0.22s ease-out;
  }
  .toast.warn {
    border-left-color: var(--warn);
  }
  .toast.danger {
    border-left-color: var(--danger);
  }
  .ico {
    color: var(--info);
    display: grid;
    margin-top: 1px;
  }
  .warn .ico {
    color: var(--warn);
  }
  .danger .ico {
    color: var(--danger);
  }
  .body {
    flex: 1;
    min-width: 0;
  }
  p {
    margin: 2px 0 0;
    font-size: 13.5px;
    color: var(--text-2);
    overflow-wrap: anywhere;
  }
  .close {
    width: 30px;
    height: 30px;
  }
  @keyframes slide {
    from {
      transform: translateY(-8px);
      opacity: 0;
    }
  }
  @media (max-width: 899px) {
    .toasts {
      top: calc(var(--header-h) + 8px);
      left: 14px;
      right: 14px;
      width: auto;
    }
  }
</style>
