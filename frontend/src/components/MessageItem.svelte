<script lang="ts">
  import ApprovalCard from './cards/ApprovalCard.svelte';
  import ArtifactCard from './cards/ArtifactCard.svelte';
  import BriefCard from './cards/BriefCard.svelte';
  import NotificationCard from './cards/NotificationCard.svelte';
  import TaskCard from './cards/TaskCard.svelte';
  import Logo from './Logo.svelte';
  import { clockTime } from '../lib/format';
  import { renderMarkdown } from '../lib/markdown';
  import { app } from '../lib/store.svelte';
  import type { Message } from '../lib/types';

  let { message }: { message: Message } = $props();

  const card = $derived(message.card);
  const d = $derived(app.data);
  const showText = $derived(!!message.text && card?.type !== 'notification');
</script>

{#if message.role === 'system'}
  <div class="system">{message.text}</div>
{:else if message.role === 'user'}
  <div class="row user">
    <div class="bubble user-bubble">
      <div class="text">{message.text}</div>
    </div>
    <time class="time" datetime={message.created_at}>{clockTime(message.created_at)}</time>
  </div>
{:else}
  <div class="row agent">
    <span class="avatar"><Logo size={26} /></span>
    <div class="stack">
      {#if showText}
        <div class="bubble agent-bubble md">{@html renderMarkdown(message.text)}</div>
      {/if}
      {#if card}
        <div class="card-slot">
          {#if card.type === 'approval'}
            {#if d.approvals[card.ref]}<ApprovalCard approval={d.approvals[card.ref]} />{:else}<div class="missing">Approval {card.ref}</div>{/if}
          {:else if card.type === 'task'}
            {#if d.tasks[card.ref]}<TaskCard task={d.tasks[card.ref]} />{:else}<div class="missing">Task {card.ref}</div>{/if}
          {:else if card.type === 'artifact'}
            {#if d.artifacts[card.ref]}<ArtifactCard artifact={d.artifacts[card.ref]} />{:else}<div class="missing">File {card.ref}</div>{/if}
          {:else if card.type === 'brief'}
            {#if d.briefs[card.ref]}<BriefCard brief={d.briefs[card.ref]} />{:else}<div class="missing">Brief {card.ref}</div>{/if}
          {:else if card.type === 'notification'}
            <NotificationCard {message} />
          {/if}
        </div>
      {/if}
      <time class="time" datetime={message.created_at}>{clockTime(message.created_at)}</time>
    </div>
  </div>
{/if}

<style>
  .row {
    display: flex;
    gap: 10px;
    margin: 14px 0;
  }
  .row.user {
    flex-direction: column;
    align-items: flex-end;
  }
  .avatar {
    flex: none;
    margin-top: 2px;
  }
  .stack {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
    align-items: flex-start;
  }
  .bubble {
    max-width: min(640px, 100%);
    padding: 10px 14px;
    border-radius: 18px;
    word-wrap: break-word;
    overflow-wrap: anywhere;
  }
  .user-bubble {
    background: var(--user-bubble);
    color: var(--user-text);
    border-bottom-right-radius: 6px;
    max-width: min(560px, 88%);
  }
  .user-bubble .text {
    white-space: pre-wrap;
  }
  .agent-bubble {
    background: var(--surface);
    border: 1px solid var(--border);
    border-top-left-radius: 6px;
  }
  .card-slot {
    width: 100%;
    max-width: 640px;
  }
  .time {
    font-size: 11.5px;
    color: var(--text-3);
    margin-top: -2px;
  }
  .row.user .time {
    margin-top: 4px;
  }
  .system {
    text-align: center;
    font-size: 12.5px;
    color: var(--text-3);
    margin: 14px 0;
  }
  .missing {
    font-size: 13px;
    color: var(--text-3);
    padding: 10px 14px;
    border: 1px dashed var(--border-strong);
    border-radius: var(--radius);
  }
</style>
