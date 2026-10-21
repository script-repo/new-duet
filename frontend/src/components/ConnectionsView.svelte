<script lang="ts">
  import Icon from './Icon.svelte';
  import ProviderMark from './ProviderMark.svelte';
  import { api } from '../lib/api';
  import {
    deliveryLabel, DOCS_URL, envHint, oauthResult, otherConnections, providerCards, STATE_TONE, type ProviderCard,
  } from '../lib/connections';
  import { relativeTime } from '../lib/format';
  import { app, errorMessage } from '../lib/store.svelte';
  import type { Connection, ConnectionsResponse } from '../lib/types';
  import { splitHash } from '../lib/ui';

  let data = $state<ConnectionsResponse | null>(null);
  let loadError = $state<string | null>(null);
  let busy = $state<string | null>(null);
  let confirming = $state<string | null>(null);
  let cardErrors = $state<Record<string, string>>({});
  let copied = $state(false);

  const cards = $derived(data ? providerCards(data) : []);
  const others = $derived(data ? otherConnections(data) : []);

  async function load() {
    try {
      data = await api.connections();
      loadError = null;
    } catch (e) {
      loadError = errorMessage(e);
    }
  }

  let refetchTimer: ReturnType<typeof setTimeout> | undefined;
  function refetchSoon(ms = 600) {
    clearTimeout(refetchTimer);
    refetchTimer = setTimeout(load, ms);
  }

  /** The OAuth callback lands on `#/connections?connected=…` or `?error=…`: say so once, then clean the URL. */
  function handleCallback() {
    const result = oauthResult(splitHash(location.hash).params);
    if (!result) return;
    history.replaceState(null, '', `${location.pathname}${location.search}#/connections`);
    if (result.kind === 'connected') {
      app.toast({ title: 'Connected', body: `${result.name} is connected. Its account and push status appear in a moment.`, urgency: 'normal', kind: 'info' });
      refetchSoon(1500);
    } else {
      app.toast({ title: result.name ? `Could not connect ${result.name}` : 'Could not connect', body: result.message, urgency: 'normal', kind: 'error' });
    }
  }

  $effect(() => {
    handleCallback();
    void load();
    const onHash = () => handleCallback();
    window.addEventListener('hashchange', onHash);
    // account and push mode fill in a second after connecting; activity tells us something changed
    const stop = app.listen((ev) => {
      if (ev.type === 'activity.created') refetchSoon();
    });
    return () => {
      window.removeEventListener('hashchange', onHash);
      stop();
      clearTimeout(refetchTimer);
    };
  });

  async function connect(c: ProviderCard) {
    busy = c.name;
    cardErrors = { ...cardErrors, [c.name]: '' };
    try {
      const { url } = await api.authorizeConnection(c.name);
      location.href = url;
    } catch (e) {
      cardErrors = { ...cardErrors, [c.name]: errorMessage(e) };
      busy = null;
    }
  }

  async function disconnect(c: ProviderCard) {
    busy = c.name;
    try {
      const r = await api.disconnect(c.name);
      confirming = null;
      app.toast({
        title: `${c.display_name} disconnected`,
        body: r.revoked === false ? 'Tandem deleted its copy of the token.' : 'Access was revoked and the token deleted.',
        urgency: 'normal',
        kind: 'info',
      });
      await load();
    } catch (e) {
      cardErrors = { ...cardErrors, [c.name]: errorMessage(e) };
    } finally {
      busy = null;
    }
  }

  function askDisconnect(c: ProviderCard) {
    confirming = c.name;
    queueMicrotask(() => document.getElementById(`confirm-${c.name}`)?.focus());
  }

  function cancelDisconnect(c: ProviderCard) {
    confirming = null;
    queueMicrotask(() => document.getElementById(`disc-${c.name}`)?.focus());
  }

  async function copyRedirect() {
    if (!data) return;
    try {
      await navigator.clipboard.writeText(data.redirect_uri);
      copied = true;
      setTimeout(() => (copied = false), 1800);
    } catch {
      const el = document.getElementById('redirect-uri') as HTMLInputElement | null;
      el?.select();
    }
  }

  function otherTone(c: Connection): string {
    return c.status === 'ok' ? 'ok' : c.status === 'error' ? 'danger' : '';
  }
</script>

<div class="page">
  <div class="page-head">
    <div>
      <h1>Connections</h1>
      <p class="muted sub">The apps Tandem reads and acts in. Tokens stay encrypted on this server.</p>
    </div>
    <button class="btn" onclick={load}><Icon name="refresh" size={15} /> Refresh</button>
  </div>

  {#if loadError && !data}
    <div class="empty" role="alert">
      <p>Couldn’t load connections: {loadError}</p>
      <button class="btn sm" onclick={load}>Try again</button>
    </div>
  {:else if !data}
    <div class="cards" aria-busy="true" aria-label="Loading connections">
      {#each [0, 1, 2, 3] as i (i)}<div class="card provider skeleton"></div>{/each}
    </div>
  {:else}
    <ul class="cards" aria-label="Apps">
      {#each cards as c (c.type + c.name)}
        {@const d = c.state === 'connected' || c.state === 'reconnect' ? deliveryLabel(c.connection) : null}
        <li class="card provider" class:connected={c.state === 'connected'}>
          <div class="head">
            <ProviderMark type={c.type} provider={c.provider} name={c.display_name} />
            <div class="grow">
              <h2>{c.display_name}</h2>
              <p class="status"><span class="badge {STATE_TONE[c.state]}">
                {#if c.state === 'connected'}<Icon name="check" size={12} />{:else if c.state === 'reconnect'}<Icon name="alert" size={12} />{/if}
                {c.state === 'connected' ? 'Connected' : c.status}
              </span>
              {#if c.state === 'connected' && c.connection?.account}<span class="account" title={c.connection.account}>{c.connection.account}</span>{/if}
              </p>
            </div>
          </div>

          {#if c.state === 'unconfigured'}
            <p class="hint">
              {envHint(c.env)} on the server, then restart Tandem.
              <a href={DOCS_URL} target="_blank" rel="noopener">Setup guide <Icon name="external" size={12} /></a>
            </p>
          {:else}
            {#if c.state === 'reconnect' && c.connection?.error}
              <p class="hint danger-text">{c.connection.error}</p>
            {/if}
            {#if d}
              <p class="delivery"><span class="badge {d.tone}" title={d.detail}>{d.label}</span> <span class="faint small">{d.detail}</span></p>
            {/if}
            {#if c.connection && c.state === 'connected'}
              <div class="faint small meta">
                {c.connection.tools.length} tools
                {#if c.connection.last_poll}· checked {relativeTime(c.connection.last_poll, app.now)}{/if}
                {#if c.connection.scopes.length}
                  · <details class="scopes"><summary>{c.connection.scopes.length} scopes</summary>
                    <ul>{#each c.connection.scopes as s (s)}<li class="mono">{s}</li>{/each}</ul>
                  </details>
                {/if}
              </div>
            {:else if c.state === 'disconnected'}
              <p class="faint small meta">Not linked yet. Tandem asks for read and send access; sending always waits for your approval.</p>
            {/if}
          {/if}

          {#if cardErrors[c.name]}<p class="hint danger-text" role="alert">{cardErrors[c.name]}</p>{/if}

          <div class="actions">
            {#if confirming === c.name}
              <span class="confirm-text">Disconnect {c.display_name}? Tandem stops reading it and deletes its token.</span>
              <button class="btn sm" onclick={() => cancelDisconnect(c)} disabled={busy === c.name}>Cancel</button>
              <button id="confirm-{c.name}" class="btn sm danger" onclick={() => disconnect(c)} disabled={busy === c.name}>
                {busy === c.name ? 'Disconnecting…' : 'Disconnect'}
              </button>
            {:else if c.state === 'connected'}
              <button id="disc-{c.name}" class="btn sm ghost" onclick={() => askDisconnect(c)}>Disconnect</button>
            {:else if c.state === 'reconnect'}
              <button id="disc-{c.name}" class="btn sm ghost" onclick={() => askDisconnect(c)}>Disconnect</button>
              <button class="btn sm primary" onclick={() => connect(c)} disabled={busy === c.name}>{busy === c.name ? 'Opening…' : 'Reconnect'}</button>
            {:else}
              <button class="btn sm primary" onclick={() => connect(c)} disabled={c.state === 'unconfigured' || busy === c.name}>
                {busy === c.name ? 'Opening…' : 'Connect'}
              </button>
            {/if}
          </div>
        </li>
      {/each}
    </ul>

    <section class="card pad setup" aria-labelledby="c-redirect">
      <h2 id="c-redirect">Redirect URI</h2>
      <p class="muted small">Register this as the OAuth redirect (callback) URL in each provider’s app settings.</p>
      <div class="copy-row">
        <label class="sr-only" for="redirect-uri">Redirect URI</label>
        <input id="redirect-uri" class="input mono" readonly value={data.redirect_uri} onfocus={(e) => e.currentTarget.select()} />
        <button class="btn" onclick={copyRedirect} aria-live="polite"><Icon name={copied ? 'check' : 'copy'} size={15} /> {copied ? 'Copied' : 'Copy'}</button>
      </div>
      {#if !data.push_capable}
        <p class="faint small push-note">
          <Icon name="clock" size={13} /> No public HTTPS address, so apps are polled on each check-in. Set
          <span class="mono">TANDEM_PUBLIC_URL</span> to an HTTPS URL for instant push.
        </p>
      {/if}
    </section>

    {#if others.length}
      <section class="card pad" aria-labelledby="c-other">
        <h2 id="c-other">Other connectors</h2>
        <ul class="rows">
          {#each others as c (c.name)}
            {@const d = deliveryLabel(c)}
            <li>
              <ProviderMark type={c.type} name={c.name} size={30} />
              <div class="grow">
                <strong>{c.name}</strong> <span class="faint small">{c.type}</span>
                {#if c.error}<div class="small danger-text">{c.error}</div>{/if}
              </div>
              <span class="faint small nowrap" title={c.tools.join(', ')}>{c.tools.length} {c.tools.length === 1 ? 'tool' : 'tools'}</span>
              {#if d}<span class="badge {d.tone} hide-sm" title={d.detail}>{d.label}</span>{/if}
              <span class="badge {otherTone(c)}">{c.status}</span>
            </li>
          {/each}
        </ul>
      </section>
    {/if}
  {/if}
</div>

<style>
  .sub {
    margin: 2px 0 0;
  }
  .cards {
    list-style: none;
    margin: 0 0 16px;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr));
    gap: 14px;
  }
  .provider {
    padding: 16px 16px 14px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-height: 196px;
  }
  .provider.connected {
    border-color: color-mix(in srgb, var(--ok) 35%, var(--border));
  }
  .skeleton {
    background: linear-gradient(90deg, var(--surface) 0%, var(--surface-2) 50%, var(--surface) 100%);
    background-size: 200% 100%;
    animation: shimmer 1.4s linear infinite;
  }
  @keyframes shimmer {
    to {
      background-position: -200% 0;
    }
  }
  .head {
    display: flex;
    gap: 12px;
    align-items: center;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  h2 {
    font-size: 16px;
  }
  .status {
    margin: 3px 0 0;
    display: flex;
    gap: 8px;
    align-items: center;
    min-width: 0;
  }
  .account {
    font-size: 13px;
    color: var(--text-2);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  p {
    margin: 0;
  }
  .hint {
    font-size: 13.5px;
    color: var(--text-2);
  }
  .hint a {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    white-space: nowrap;
  }
  .danger-text {
    color: var(--danger);
  }
  .delivery {
    display: flex;
    gap: 8px;
    align-items: baseline;
    flex-wrap: wrap;
  }
  .small {
    font-size: 13px;
  }
  .meta {
    line-height: 1.6;
  }
  .scopes {
    display: inline;
  }
  .scopes summary {
    display: inline;
    cursor: pointer;
    text-decoration: underline dotted;
  }
  .scopes ul {
    margin: 6px 0 0;
    padding: 8px 10px 8px 26px;
    background: var(--surface-2);
    border-radius: var(--radius-sm);
    word-break: break-all;
  }
  .actions {
    margin-top: auto;
    display: flex;
    gap: 8px;
    justify-content: flex-end;
    align-items: center;
    flex-wrap: wrap;
  }
  .confirm-text {
    font-size: 13px;
    color: var(--text-2);
    flex: 1 1 180px;
  }
  .pad {
    padding: 18px 20px;
    margin-bottom: 16px;
  }
  .pad h2 {
    margin-bottom: 6px;
  }
  .copy-row {
    display: flex;
    gap: 8px;
    margin-top: 10px;
  }
  .copy-row .input {
    flex: 1;
    min-width: 0;
  }
  .push-note {
    margin-top: 10px;
    display: flex;
    gap: 6px;
    align-items: baseline;
    flex-wrap: wrap;
  }
  .rows {
    list-style: none;
    margin: 6px 0 0;
    padding: 0;
  }
  .rows li {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 9px 0;
  }
  .rows li + li {
    border-top: 1px solid var(--border);
  }
  .nowrap {
    white-space: nowrap;
  }
  @media (max-width: 480px) {
    .hide-sm {
      display: none;
    }
  }
</style>
