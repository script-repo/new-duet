<script lang="ts">
  import ActivityView from './components/ActivityView.svelte';
  import Icon from './components/Icon.svelte';
  import LibraryView from './components/LibraryView.svelte';
  import Logo from './components/Logo.svelte';
  import PreviewModal from './components/PreviewModal.svelte';
  import SettingsView from './components/SettingsView.svelte';
  import SignIn from './components/SignIn.svelte';
  import ThreadView from './components/ThreadView.svelte';
  import Toasts from './components/Toasts.svelte';
  import TodaySummary from './components/TodaySummary.svelte';
  import TodayView from './components/TodayView.svelte';
  import { pendingApprovals } from './lib/reducer';
  import { app, type Route } from './lib/store.svelte';

  const NAV: { route: Route; label: string; icon: string }[] = [
    { route: 'thread', label: 'Thread', icon: 'thread' },
    { route: 'today', label: 'Today', icon: 'today' },
    { route: 'activity', label: 'Activity', icon: 'activity' },
    { route: 'library', label: 'Library', icon: 'library' },
    { route: 'settings', label: 'Settings', icon: 'settings' },
  ];
  const TABS = NAV.filter((n) => n.route !== 'settings');

  const needs = $derived(pendingApprovals(app.data).length);
  const titles: Record<Route, string> = { thread: 'Tandem', today: 'Today', activity: 'Activity', library: 'Library', settings: 'Settings' };

  $effect(() => {
    document.title = needs ? `(${needs}) Tandem` : app.route === 'thread' ? 'Tandem' : `${titles[app.route]} · Tandem`;
  });

  app.start();
</script>

{#if app.needsAuth}
  <SignIn />
{:else}
  <div class="shell" class:with-panel={app.route === 'thread'}>
    <nav class="rail" aria-label="Main">
      <div class="brand"><Logo size={30} /><span>Tandem</span></div>
      <ul>
        {#each NAV as n (n.route)}
          <li>
            <a href="#/{n.route}" class:active={app.route === n.route} aria-current={app.route === n.route ? 'page' : undefined}>
              <Icon name={n.icon} />
              <span>{n.label}</span>
              {#if n.route === 'today' && needs}<span class="count" aria-label="{needs} need you">{needs}</span>{/if}
            </a>
          </li>
        {/each}
      </ul>
      <div class="status" title={app.connected ? 'Live updates on' : 'Reconnecting…'}>
        <span class="led" class:on={app.connected}></span>{app.connected ? 'Live' : 'Offline'}
        {#if app.config}<span class="badge {app.config.mode === 'mock' ? 'warn' : ''}">{app.config.mode}</span>{/if}
      </div>
    </nav>

    <header class="topbar">
      <div class="brand"><Logo size={28} /><span>Tandem</span></div>
      <span class="led" class:on={app.connected} title={app.connected ? 'Live' : 'Offline'}></span>
      <a class="icon-btn" href="#/settings" aria-label="Settings" aria-current={app.route === 'settings' ? 'page' : undefined}><Icon name="settings" /></a>
    </header>

    <main class="main" id="main">
      {#if app.bootError && !app.booted}
        <div class="page">
          <div class="empty boot-error" role="alert">
            <p><strong>Can’t reach the Tandem server.</strong></p>
            <p>{app.bootError}</p>
            <button class="btn" onclick={() => app.boot()}><Icon name="refresh" size={15} /> Try again</button>
          </div>
        </div>
      {:else if app.route === 'thread'}
        <ThreadView />
      {:else if app.route === 'today'}
        <TodayView />
      {:else if app.route === 'activity'}
        <ActivityView />
      {:else if app.route === 'library'}
        <LibraryView />
      {:else}
        <SettingsView />
      {/if}
    </main>

    {#if app.route === 'thread'}
      <div class="panel"><TodaySummary /></div>
    {/if}

    <nav class="tabbar" aria-label="Main">
      {#each TABS as n (n.route)}
        <a href="#/{n.route}" class:active={app.route === n.route} aria-current={app.route === n.route ? 'page' : undefined}>
          <span class="tab-icon">
            <Icon name={n.icon} size={21} />
            {#if n.route === 'today' && needs}<span class="dot-count" aria-label="{needs} need you">{needs}</span>{/if}
          </span>
          <span>{n.label}</span>
        </a>
      {/each}
    </nav>
  </div>
  <Toasts />
  <PreviewModal />
{/if}

<style>
  .shell {
    height: 100%;
    display: grid;
    grid-template-columns: var(--rail-w) minmax(0, 1fr);
    grid-template-areas: 'rail main';
  }
  @media (min-width: 1280px) {
    .shell.with-panel {
      grid-template-columns: var(--rail-w) minmax(0, 1fr) var(--panel-w);
      grid-template-areas: 'rail main panel';
    }
  }
  .rail {
    grid-area: rail;
    display: flex;
    flex-direction: column;
    padding: 16px 12px;
    border-right: 1px solid var(--border);
    background: color-mix(in srgb, var(--surface-2) 55%, var(--bg));
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    font-family: var(--serif);
    font-size: 20px;
    font-weight: 600;
    padding: 4px 8px 18px;
  }
  .rail ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 2px;
  }
  .rail a {
    display: flex;
    align-items: center;
    gap: 11px;
    padding: 9px 12px;
    border-radius: 11px;
    color: var(--text-2);
    text-decoration: none;
    font-weight: 550;
  }
  .rail a:hover {
    background: var(--surface-2);
    color: var(--text);
  }
  .rail a.active {
    background: var(--surface);
    color: var(--text);
    box-shadow: var(--shadow);
  }
  .rail a.active :global(svg) {
    color: var(--accent);
  }
  .count {
    margin-left: auto;
    min-width: 20px;
    padding: 0 6px;
    border-radius: 999px;
    background: var(--accent);
    color: var(--accent-text);
    font-size: 12px;
    font-weight: 700;
    text-align: center;
  }
  .status {
    margin-top: auto;
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12.5px;
    color: var(--text-3);
    padding: 8px 10px;
  }
  .status .badge {
    margin-left: auto;
  }
  .led {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--text-3);
    flex: none;
  }
  .led.on {
    background: var(--ok);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 22%, transparent);
  }
  .main {
    grid-area: main;
    min-width: 0;
    min-height: 0;
    overflow-y: auto;
  }
  .panel {
    grid-area: panel;
    display: none;
    border-left: 1px solid var(--border);
    background: color-mix(in srgb, var(--surface-2) 40%, var(--bg));
    min-height: 0;
  }
  @media (min-width: 1280px) {
    .panel {
      display: block;
    }
  }
  .topbar,
  .tabbar {
    display: none;
  }
  .boot-error {
    display: grid;
    gap: 6px;
    justify-items: center;
    margin-top: 10vh;
  }
  .boot-error p {
    margin: 0;
  }

  /* ---- phones and small tablets ---------------------------------------------------------------- */
  @media (max-width: 899px) {
    .shell,
    .shell.with-panel {
      grid-template-columns: minmax(0, 1fr);
      grid-template-rows: var(--header-h) minmax(0, 1fr) auto;
      grid-template-areas: 'top' 'main' 'tabs';
    }
    .rail {
      display: none;
    }
    .topbar {
      grid-area: top;
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 0 8px 0 14px;
      padding-top: env(safe-area-inset-top);
      border-bottom: 1px solid var(--border);
      background: var(--bg);
    }
    .topbar .brand {
      padding: 0;
      font-size: 18px;
      flex: 1;
    }
    .topbar a[aria-current='page'] {
      color: var(--accent);
    }
    .tabbar {
      grid-area: tabs;
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      border-top: 1px solid var(--border);
      background: var(--surface);
      padding-bottom: env(safe-area-inset-bottom);
    }
    .tabbar a {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 2px;
      padding: 8px 0 7px;
      font-size: 11.5px;
      font-weight: 600;
      color: var(--text-3);
      text-decoration: none;
      min-height: var(--tabbar-h);
      justify-content: center;
    }
    .tabbar a.active {
      color: var(--accent);
    }
    .tab-icon {
      position: relative;
      display: grid;
    }
    .dot-count {
      position: absolute;
      top: -4px;
      right: -10px;
      min-width: 17px;
      height: 17px;
      padding: 0 4px;
      border-radius: 999px;
      background: var(--accent);
      color: var(--accent-text);
      font-size: 10.5px;
      font-weight: 700;
      line-height: 17px;
      text-align: center;
    }
  }
</style>
