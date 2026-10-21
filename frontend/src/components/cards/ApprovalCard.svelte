<script lang="ts">
  import Icon from '../Icon.svelte';
  import { approveBody, argRows, initialDrafts, rejectBody, type Drafts } from '../../lib/approval';
  import { countdown, humanize, relativeTime } from '../../lib/format';
  import { app } from '../../lib/store.svelte';
  import type { Approval } from '../../lib/types';

  let { approval }: { approval: Approval } = $props();

  let mode = $state<'view' | 'edit' | 'reject'>('view');
  let drafts = $state<Drafts>({});
  let note = $state('');
  let busy = $state(false);
  let error = $state<string | null>(null);

  const rows = $derived(argRows(approval));
  const pending = $derived(approval.state === 'pending');
  const left = $derived(countdown(approval.expires_at, app.now));
  const stateBadge: Record<string, string> = {
    approved: 'ok', rejected: 'danger', expired: 'warn', cancelled: '', pending: 'accent',
  };
  const uid = $derived(`ap-${approval.id}`);

  function startEdit() {
    drafts = initialDrafts(approval);
    error = null;
    mode = 'edit';
  }

  async function approve() {
    const body = approveBody(approval, mode === 'edit' ? drafts : null);
    if ('error' in body) {
      error = body.error;
      return;
    }
    busy = true;
    error = null;
    if (await app.decide(approval, body)) mode = 'view';
    busy = false;
  }

  async function reject() {
    busy = true;
    if (await app.decide(approval, rejectBody(note))) mode = 'view';
    busy = false;
  }
</script>

<article class="card approval" class:decided={!pending} aria-labelledby="{uid}-title">
  <header>
    <span class="icon"><Icon name="shield" size={17} /></span>
    <div class="head">
      <h3 id="{uid}-title">{approval.title || humanize(approval.tool)}</h3>
      <div class="meta">
        <span class="mono faint">{approval.tool}</span>
        {#each approval.tags as tag (tag)}<span class="badge">{tag}</span>{/each}
      </div>
    </div>
    {#if pending && left.text}
      <span class="badge countdown" class:warn={!left.urgent} class:danger={left.urgent} title="Expires and is denied when this runs out">
        <Icon name="clock" size={13} /><span class="sr-only">Time left </span>{left.text}
      </span>
    {:else if !pending}
      <span class="badge {stateBadge[approval.state] ?? ''}">{approval.state}</span>
    {/if}
  </header>

  {#if approval.summary}<p class="summary">{approval.summary}</p>{/if}
  {#if approval.reason}<p class="reason"><Icon name="alert" size={14} /> {approval.reason}</p>{/if}

  {#if pending || mode !== 'view'}
    <dl class="args">
      {#each rows as row (row.key)}
        <div class="arg" class:editable={row.editable && mode === 'edit'}>
          <dt>
            {#if row.editable && mode === 'edit'}
              <label for="{uid}-{row.key}">{row.key}</label>
            {:else}
              {row.key}
            {/if}
            {#if row.editable}<span class="faint editable-mark">editable</span>{/if}
          </dt>
          <dd>
            {#if row.editable && mode === 'edit'}
              {#if row.multiline || (drafts[row.key] ?? '').includes('\n')}
                <textarea id="{uid}-{row.key}" class="textarea" rows={Math.min(14, Math.max(3, (drafts[row.key] ?? '').split('\n').length + 1))}
                  bind:value={drafts[row.key]} disabled={busy}></textarea>
              {:else}
                <input id="{uid}-{row.key}" class="input" bind:value={drafts[row.key]} disabled={busy} />
              {/if}
            {:else}
              <pre class="value" class:short={!row.multiline}>{row.value}</pre>
            {/if}
          </dd>
        </div>
      {/each}
    </dl>
  {:else}
    <details class="decided-args">
      <summary>Arguments</summary>
      <dl class="args">
        {#each rows as row (row.key)}
          <div class="arg"><dt>{row.key}</dt><dd><pre class="value">{row.value}</pre></dd></div>
        {/each}
      </dl>
    </details>
    <p class="faint small">
      {#if approval.decided_at}{humanize(approval.state)} {relativeTime(approval.decided_at, app.now)}{/if}
      {#if approval.note} · “{approval.note}”{/if}
    </p>
  {/if}

  {#if error}<p class="error" role="alert">{error}</p>{/if}

  {#if pending}
    {#if mode === 'reject'}
      <div class="reject">
        <label for="{uid}-note" class="sr-only">Note for the agent (optional)</label>
        <input id="{uid}-note" class="input" placeholder="Tell the agent why (optional)" bind:value={note} disabled={busy}
          onkeydown={(e) => e.key === 'Enter' && reject()} />
        <div class="actions">
          <button class="btn ghost" onclick={() => (mode = 'view')} disabled={busy}>Cancel</button>
          <button class="btn danger" onclick={reject} disabled={busy}><Icon name="x" size={15} /> Reject</button>
        </div>
      </div>
    {:else}
      <div class="actions">
        <button class="btn ghost danger" onclick={() => ((mode = 'reject'), (note = ''))} disabled={busy}>Reject</button>
        {#if mode === 'edit'}
          <button class="btn" onclick={() => ((mode = 'view'), (error = null))} disabled={busy}>Discard edits</button>
        {:else if approval.editable.length}
          <button class="btn" onclick={startEdit} disabled={busy}><Icon name="pencil" size={14} /> Edit</button>
        {/if}
        <button class="btn primary" onclick={approve} disabled={busy}>
          <Icon name="check" size={15} /> {mode === 'edit' ? 'Approve edited' : 'Approve'}
        </button>
      </div>
    {/if}
  {/if}
</article>

<style>
  .approval {
    padding: 14px 16px;
    border-left: 4px solid var(--accent);
  }
  .approval.decided {
    border-left-color: var(--border-strong);
    box-shadow: none;
  }
  header {
    display: flex;
    align-items: flex-start;
    gap: 10px;
  }
  .icon {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    border-radius: 9px;
    background: var(--accent-soft);
    color: var(--accent);
    flex: none;
  }
  .head {
    flex: 1;
    min-width: 0;
  }
  h3 {
    font-size: 15px;
    font-weight: 650;
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
    font-size: 12px;
    margin-top: 2px;
  }
  .countdown {
    font-variant-numeric: tabular-nums;
  }
  .countdown.warn {
    background: var(--warn-soft);
    color: var(--warn);
  }
  .countdown.danger {
    background: var(--danger-soft);
    color: var(--danger);
  }
  .summary {
    margin: 10px 0 0;
  }
  .reason {
    margin: 6px 0 0;
    font-size: 13px;
    color: var(--text-2);
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .args {
    margin: 12px 0 0;
    display: grid;
    gap: 8px;
  }
  .arg {
    display: grid;
    gap: 3px;
  }
  dt {
    font-size: 12px;
    font-weight: 650;
    color: var(--text-2);
    display: flex;
    gap: 8px;
    align-items: baseline;
  }
  .editable-mark {
    font-weight: 500;
    font-size: 11px;
  }
  dd {
    margin: 0;
  }
  .value {
    margin: 0;
    font-family: var(--font);
    white-space: pre-wrap;
    word-break: break-word;
    background: var(--surface-2);
    border-radius: var(--radius-sm);
    padding: 8px 11px;
    max-height: 320px;
    overflow: auto;
    font-size: 14px;
  }
  .value.short {
    padding: 5px 11px;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 14px;
  }
  .reject {
    margin-top: 12px;
  }
  .reject .actions {
    margin-top: 8px;
  }
  .error {
    color: var(--danger);
    font-size: 13px;
    margin: 10px 0 0;
  }
  .small {
    font-size: 13px;
    margin: 8px 0 0;
  }
  .decided-args {
    margin-top: 8px;
    font-size: 13px;
  }
  .decided-args summary {
    cursor: pointer;
    color: var(--text-2);
  }
</style>
