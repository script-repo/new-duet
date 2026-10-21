<script lang="ts">
  import Icon from './Icon.svelte';
  import NotificationSettings from './NotificationSettings.svelte';
  import { api } from '../lib/api';
  import { dateTime, formatValue, relativeTime } from '../lib/format';
  import { app, errorMessage } from '../lib/store.svelte';
  import type { AuditRow, MemoryEntry, Skill } from '../lib/types';
  import { describeMatch, outboxSections, type OutboxSection } from '../lib/ui';

  const cfg = $derived(app.config);
  let memory = $state<MemoryEntry[] | null>(null);
  let skills = $state<Skill[] | null>(null);
  let audit = $state<AuditRow[] | null>(null);
  let outbox = $state<OutboxSection[] | null>(null);
  let errors = $state<Record<string, string>>({});
  let busy = $state<string | null>(null);

  let memKind = $state('preference');
  let memContent = $state('');
  let memKey = $state('');
  let saving = $state(false);

  const MEMORY_KINDS = ['core', 'preference', 'fact', 'note'];

  function load<T>(name: string, p: Promise<T>, set: (v: T) => void) {
    p.then(set).catch((e) => (errors = { ...errors, [name]: errorMessage(e) }));
  }

  $effect(() => {
    if (!app.config) app.loadConfig().catch((e) => (errors = { ...errors, config: errorMessage(e) }));
    load('memory', api.memory(), (v) => (memory = v));
    load('skills', api.skills(), (v) => (skills = v));
    load('audit', api.audit(50), (v) => (audit = v));
    load('outbox', api.outbox(), (v) => (outbox = outboxSections(v)));
  });

  async function addMemory(e: SubmitEvent) {
    e.preventDefault();
    const content = memContent.trim();
    if (!content) return;
    saving = true;
    try {
      const m = await api.addMemory(memKey.trim() ? { kind: memKind, content, key: memKey.trim() } : { kind: memKind, content });
      memory = [m, ...(memory ?? []).filter((x) => x.id !== m.id && !(m.key && x.key === m.key))];
      memContent = '';
      memKey = '';
    } catch (err) {
      app.fail(err, 'Could not save memory');
    } finally {
      saving = false;
    }
  }

  async function forget(m: MemoryEntry) {
    try {
      await api.forget(m.id);
      memory = (memory ?? []).filter((x) => x.id !== m.id);
    } catch (err) {
      app.fail(err, 'Could not forget');
    }
  }

  async function run(which: 'brief' | 'checkin') {
    busy = which;
    await (which === 'brief' ? app.runBrief() : app.runCheckin());
    busy = null;
  }

  function connectorStatus(s: unknown): { text: string; tone: string } {
    const text = typeof s === 'string' ? s : s && typeof s === 'object' && 'state' in s ? String((s as { state: unknown }).state) : formatValue(s);
    const tone = /ok|ready|connected|running|active/i.test(text) ? 'ok' : /error|fail|down/i.test(text) ? 'danger' : 'warn';
    return { text, tone };
  }

  const decisionTone: Record<string, string> = { allow: 'ok', approved: 'ok', edited: 'accent', rejected: 'danger', denied: 'danger', expired: 'warn' };
  const actionTone: Record<string, string> = { allow: 'ok', ask: 'accent', deny: 'danger' };
  const groupedMemory = $derived(
    MEMORY_KINDS.concat(['thread_summary'])
      .map((k) => ({ kind: k, items: (memory ?? []).filter((m) => m.kind === k) }))
      .concat([{ kind: 'other', items: (memory ?? []).filter((m) => !MEMORY_KINDS.includes(m.kind) && m.kind !== 'thread_summary') }])
      .filter((g) => g.items.length),
  );
</script>

<div class="page">
  <div class="page-head">
    <div class="title">
      <h1>Settings</h1>
      {#if cfg}<span class="badge {cfg.mode === 'mock' ? 'warn' : 'ok'}" title="Server mode">{cfg.mode} mode</span>{/if}
      {#if app.data.serverVersion}<span class="faint small">v{app.data.serverVersion}</span>{/if}
    </div>
    <div class="buttons">
      <button class="btn" onclick={() => run('checkin')} disabled={busy !== null}><Icon name="refresh" size={15} /> Check in now</button>
      <button class="btn" onclick={() => run('brief')} disabled={busy !== null}><Icon name="sun" size={15} /> Run brief now</button>
    </div>
  </div>

  <nav class="more-links" aria-label="More">
    <a href="#/connections"><Icon name="link" size={17} /><span>Connections</span><Icon name="chevron" size={16} /></a>
    <a href="#/evals"><Icon name="flask" size={17} /><span>Evals</span><Icon name="chevron" size={16} /></a>
  </nav>

  <div class="sections">
    <NotificationSettings />

    {#if errors.config}<p class="empty" role="alert">{errors.config}</p>{/if}
    {#if cfg}
      <section class="card pad" aria-labelledby="s-models">
        <h2 id="s-models">Models</h2>
        <div class="table-scroll">
          <table class="data">
            <thead><tr><th>Role</th><th>Provider</th><th>Model</th><th>Key</th></tr></thead>
            <tbody>
              {#each Object.entries(cfg.models) as [role, m] (role)}
                <tr>
                  <td><strong>{role}</strong></td>
                  <td>{m.provider}</td>
                  <td class="mono">{m.model}</td>
                  <td>{#if m.provider === 'mock'}<span class="faint">n/a</span>{:else if m.has_api_key}<span class="badge ok">set</span>{:else}<span class="badge danger">missing</span>{/if}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <p class="faint small">Read-only here; change models in <span class="mono">tandem.yaml</span>. Timezone: {cfg.timezone}.</p>
      </section>

      <section class="card pad" aria-labelledby="s-harness">
        <h2 id="s-harness">Harnesses</h2>
        <ul class="rows">
          {#each cfg.harnesses as h (h.name)}
            <li>
              <div class="grow">
                <strong>{h.display_name}</strong> <span class="mono faint">{h.name}</span>
                {#if h.default}<span class="badge accent">default</span>{/if}
                {#if h.reason}<div class="faint small">{h.reason}</div>{/if}
              </div>
              <span class="badge {h.available ? 'ok' : ''}">{h.available ? 'available' : 'unavailable'}</span>
            </li>
          {/each}
        </ul>
      </section>

      <section class="card pad" aria-labelledby="s-conn">
        <div class="h-row"><h2 id="s-conn">Connectors</h2><a class="small" href="#/connections">Manage connections</a></div>
        {#if cfg.connectors.length}
          <ul class="rows">
            {#each cfg.connectors as c (c.name)}
              {@const st = connectorStatus(c.status)}
              <li>
                <span class="ico"><Icon name="plug" size={16} /></span>
                <div class="grow">
                  <strong>{c.name}</strong> <span class="faint">{c.type}</span>
                  {#if c.tools?.length}<div class="faint small mono tools">{c.tools.join(', ')}</div>{/if}
                </div>
                <span class="badge {st.tone}">{st.text}</span>
              </li>
            {/each}
          </ul>
        {:else}<p class="faint">No connectors configured.</p>{/if}
      </section>

      <section class="card pad" aria-labelledby="s-policy">
        <h2 id="s-policy">Approval policy</h2>
        <p class="muted small">
          Unmatched tools: <span class="badge {actionTone[cfg.policy.default] ?? ''}">{cfg.policy.default}</span>
          · Pending approvals expire after {Math.round(cfg.policy.timeout_s / 60)} min and are denied.
        </p>
        <ol class="rules">
          {#each cfg.policy.rules as r, i (i)}
            <li>
              <span class="badge {actionTone[r.action] ?? ''}">{r.action}</span>
              <div class="grow">
                <span class="mono">{describeMatch(r.match)}</span>
                {#if r.reason}<div class="faint small">{r.reason}</div>{/if}
              </div>
            </li>
          {/each}
        </ol>
      </section>
    {/if}

    <section class="card pad" aria-labelledby="s-memory">
      <h2 id="s-memory">Memory</h2>
      <form class="mem-form" onsubmit={addMemory}>
        <label class="sr-only" for="mem-kind">Kind</label>
        <select id="mem-kind" class="select" bind:value={memKind}>
          {#each MEMORY_KINDS as k (k)}<option value={k}>{k}</option>{/each}
        </select>
        <label class="sr-only" for="mem-content">Something to remember</label>
        <input id="mem-content" class="input" placeholder="Something to remember, e.g. “vip: sam@harbor.example”" bind:value={memContent} />
        <label class="sr-only" for="mem-key">Key (optional; the same key replaces)</label>
        <input id="mem-key" class="input key" placeholder="key (optional)" bind:value={memKey} />
        <button class="btn primary" type="submit" disabled={saving || !memContent.trim()}><Icon name="plus" size={15} /> Add</button>
      </form>
      {#if errors.memory}<p class="faint" role="alert">{errors.memory}</p>
      {:else if memory === null}<p class="faint">Loading…</p>
      {:else if !memory.length}<p class="faint">Nothing remembered yet.</p>
      {:else}
        {#each groupedMemory as g (g.kind)}
          <h3 class="section-title">{g.kind.replace('_', ' ')}</h3>
          <ul class="rows">
            {#each g.items as m (m.id)}
              <li>
                <div class="grow">
                  {m.content}
                  <div class="faint small">{#if m.key}<span class="mono">{m.key}</span> · {/if}{m.source ?? ''} {relativeTime(m.updated_at, app.now)}</div>
                </div>
                <button class="icon-btn" onclick={() => forget(m)} aria-label="Forget “{m.content.slice(0, 40)}”" title="Forget"><Icon name="trash" size={16} /></button>
              </li>
            {/each}
          </ul>
        {/each}
      {/if}
    </section>

    <section class="card pad" aria-labelledby="s-skills">
      <h2 id="s-skills">Skills</h2>
      {#if errors.skills}<p class="faint" role="alert">{errors.skills}</p>
      {:else if skills === null}<p class="faint">Loading…</p>
      {:else if !skills.length}<p class="faint">No skills installed.</p>
      {:else}
        <ul class="rows">
          {#each skills as s (s.name)}<li><div class="grow"><strong class="mono">{s.name}</strong><div class="faint small">{s.description}</div></div></li>{/each}
        </ul>
      {/if}
    </section>

    <section class="card pad" aria-labelledby="s-outbox">
      <h2 id="s-outbox">Outbox</h2>
      <p class="muted small">What the demo connectors “sent” (mock mode): nothing here left the machine.</p>
      {#if errors.outbox}<p class="faint" role="alert">{errors.outbox}</p>
      {:else if outbox === null}<p class="faint">Loading…</p>
      {:else if !outbox.some((s) => s.items.length)}<p class="faint">Empty.</p>
      {:else}
        {#each outbox as sec (sec.name)}
          {#if sec.items.length}
            <h3 class="section-title">{sec.name}</h3>
            <ul class="outbox">
              {#each sec.items as item, i (i)}
                <li>
                  <dl>
                    {#each Object.entries(item) as [k, v] (k)}
                      <div><dt>{k}</dt><dd>{formatValue(v)}</dd></div>
                    {/each}
                  </dl>
                </li>
              {/each}
            </ul>
          {/if}
        {/each}
      {/if}
    </section>

    <section class="card pad" aria-labelledby="s-audit">
      <h2 id="s-audit">Audit log</h2>
      {#if errors.audit}<p class="faint" role="alert">{errors.audit}</p>
      {:else if audit === null}<p class="faint">Loading…</p>
      {:else if !audit.length}<p class="faint">No tool calls yet.</p>
      {:else}
        <div class="table-scroll">
          <table class="data">
            <thead><tr><th>When</th><th>Tool</th><th>Decision</th><th>Actor</th><th>OK</th><th>Detail</th></tr></thead>
            <tbody>
              {#each audit as r (r.id)}
                <tr>
                  <td class="nowrap">{dateTime(r.ts)}</td>
                  <td class="mono">{r.tool}</td>
                  <td><span class="badge {decisionTone[r.decision] ?? ''}">{r.decision}</span></td>
                  <td>{r.actor}</td>
                  <td>{r.ok == null ? '–' : r.ok ? '✓' : '✗'}</td>
                  <td class="detail">{r.detail ?? ''}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </section>
  </div>
</div>

<style>
  .more-links {
    display: none;
  }
  @media (max-width: 899px) {
    .more-links {
      display: grid;
      margin: -8px 0 16px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .more-links a {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 12px 14px;
      min-height: 48px;
      color: var(--text);
      text-decoration: none;
      font-weight: 550;
    }
    .more-links a + a {
      border-top: 1px solid var(--border);
    }
    .more-links a :global(svg:first-child) {
      color: var(--accent);
    }
    .more-links a span {
      flex: 1;
    }
    .more-links a :global(svg:last-child) {
      color: var(--text-3);
    }
  }
  .h-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 10px;
  }
  .title {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .buttons {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .sections {
    display: grid;
    gap: 16px;
  }
  .pad {
    padding: 18px 20px;
  }
  h2 {
    font-size: 16px;
    margin-bottom: 10px;
  }
  .section-title {
    margin: 16px 0 6px;
  }
  .small {
    font-size: 13px;
  }
  p {
    margin: 0 0 10px;
  }
  .rows,
  .rules,
  .outbox {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .rows li,
  .rules li {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 9px 0;
  }
  .rows li + li,
  .rules li + li {
    border-top: 1px solid var(--border);
  }
  .rules li {
    align-items: flex-start;
  }
  .rules .badge {
    min-width: 46px;
    justify-content: center;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .ico {
    color: var(--text-3);
    display: grid;
  }
  .tools {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .mem-form {
    display: flex;
    gap: 8px;
    margin-bottom: 8px;
    flex-wrap: wrap;
  }
  .mem-form .select {
    width: auto;
  }
  .mem-form .input {
    flex: 1 1 260px;
  }
  .mem-form .input.key {
    flex: 0 1 150px;
  }
  .outbox {
    display: grid;
    gap: 8px;
  }
  .outbox li {
    background: var(--surface-2);
    border-radius: var(--radius-sm);
    padding: 10px 12px;
  }
  .outbox dl {
    margin: 0;
    display: grid;
    gap: 3px;
  }
  .outbox dl div {
    display: grid;
    grid-template-columns: 90px 1fr;
    gap: 10px;
    font-size: 13.5px;
  }
  .outbox dt {
    color: var(--text-3);
    font-weight: 600;
  }
  .outbox dd {
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .nowrap {
    white-space: nowrap;
  }
  .detail {
    color: var(--text-2);
    max-width: 320px;
    word-break: break-word;
  }
</style>
