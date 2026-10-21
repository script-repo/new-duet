<script lang="ts">
  import Icon from './Icon.svelte';
  import { api } from '../lib/api';
  import { relativeTime } from '../lib/format';
  import { permission, requestPermission } from '../lib/notify';
  import { currentSubscription, currentSubscriptionId, iosNeedsInstall, pushSupported, subscribe, unsubscribe } from '../lib/push';
  import { pushHint, pushState, pushToggleEnabled } from '../lib/pushui';
  import { describeQuietHours, formatQuietHours, parseQuietHours } from '../lib/quiet';
  import { app, errorMessage } from '../lib/store.svelte';
  import type { NotifySettings, PushConfig, PushSubscriptionInfo } from '../lib/types';

  const URGENCIES: { value: NotifySettings['min_urgency']; label: string; hint: string }[] = [
    { value: 'low', label: 'Low', hint: 'Everything that isn’t ignored' },
    { value: 'normal', label: 'Normal', hint: 'Anything that needs a reply soon' },
    { value: 'high', label: 'High', hint: 'Time-sensitive things only' },
    { value: 'critical', label: 'Critical', hint: 'Only what can’t wait an hour' },
  ];

  let settings = $state<NotifySettings | null>(null);
  let devices = $state<PushSubscriptionInfo[] | null>(null);
  let loadError = $state<string | null>(null);
  let errors = $state<Record<string, string>>({});
  let savedField = $state<string | null>(null);
  let perm = $state(permission());
  let subscribed = $state(false);
  let thisId = $state<string | null>(currentSubscriptionId());
  let pushBusy = $state(false);
  let testing = $state(false);
  let testResult = $state<{ text: string; ok: boolean } | null>(null);

  // quiet hours are edited locally and saved when both times are valid
  let qStart = $state('21:00');
  let qEnd = $state('07:00');
  let maxPerHour = $state('');

  const pushCfg = $derived((app.config?.notify as { push?: PushConfig } | undefined)?.push);
  let serverEnabled = $state(true);
  const pstate = $derived(
    pushState({ supported: pushSupported(), iosNeedsInstall: iosNeedsInstall(), permission: perm, subscribed, serverEnabled }),
  );
  const quietOn = $derived(!!settings?.quiet_hours);
  const quietText = $derived(quietOn ? describeQuietHours(qStart, qEnd) : '');

  $effect(() => {
    if (pushCfg) serverEnabled = pushCfg.enabled;
  });

  async function load() {
    try {
      const [s, d, v] = await Promise.all([
        api.notifySettings(),
        api.pushSubscriptions(),
        api.vapid().catch(() => ({ public_key: '', enabled: !!pushCfg?.enabled })),
      ]);
      applySettings(s);
      devices = d;
      serverEnabled = v.enabled;
      loadError = null;
      await refreshThisDevice(d);
    } catch (e) {
      loadError = errorMessage(e);
    }
  }

  async function refreshThisDevice(list: PushSubscriptionInfo[]) {
    const sub = await currentSubscription().catch(() => null);
    thisId = currentSubscriptionId();
    // subscribed here only if the browser has one and the server still lists it
    subscribed = !!sub && !!thisId && list.some((x) => x.id === thisId);
  }

  function applySettings(s: NotifySettings) {
    settings = s;
    const q = parseQuietHours(s.quiet_hours);
    if (q) {
      qStart = q.start;
      qEnd = q.end;
    }
    maxPerHour = String(s.max_per_hour);
  }

  $effect(() => {
    void load();
  });

  /** Optimistic: show the change at once, put it back if the server says no. */
  async function save(field: keyof NotifySettings, changes: Partial<NotifySettings>) {
    if (!settings) return;
    const before = settings;
    settings = { ...settings, ...changes };
    errors = { ...errors, [field]: '' };
    try {
      applySettings(await api.saveNotifySettings(changes));
      savedField = field;
      setTimeout(() => {
        if (savedField === field) savedField = null;
      }, 1600);
    } catch (e) {
      applySettings(before);
      errors = { ...errors, [field]: errorMessage(e) };
    }
  }

  function toggleQuiet(on: boolean) {
    if (on) {
      const q = formatQuietHours(qStart, qEnd) ?? '21:00-07:00';
      void save('quiet_hours', { quiet_hours: q });
    } else {
      void save('quiet_hours', { quiet_hours: null });
    }
  }

  function saveQuietTimes() {
    const q = formatQuietHours(qStart, qEnd);
    if (!q) {
      errors = { ...errors, quiet_hours: qStart === qEnd ? 'Start and end must differ.' : 'Use two times like 21:00 and 07:00.' };
      return;
    }
    if (q !== settings?.quiet_hours) void save('quiet_hours', { quiet_hours: q });
  }

  function saveMax() {
    const n = Number(maxPerHour);
    if (!Number.isInteger(n) || n < 1) {
      errors = { ...errors, max_per_hour: 'A whole number, 1 or more.' };
      return;
    }
    if (n !== settings?.max_per_hour) void save('max_per_hour', { max_per_hour: n });
  }

  async function togglePush() {
    pushBusy = true;
    errors = { ...errors, push: '' };
    try {
      if (subscribed) {
        await unsubscribe();
        subscribed = false;
      } else {
        const r = await subscribe();
        perm = permission();
        if (r.ok) {
          subscribed = true;
          if (settings && !settings.push_enabled) void save('push_enabled', { push_enabled: true });
        } else if (r.reason === 'ios-install' || r.reason === 'denied' || r.reason === 'unsupported') {
          /* the hint below explains it */
        } else if (r.reason === 'disabled') {
          serverEnabled = false;
        } else {
          errors = { ...errors, push: r.message ?? 'Could not turn on push.' };
        }
      }
      devices = await api.pushSubscriptions();
      thisId = currentSubscriptionId();
      void app.loadConfig().catch(() => undefined);
    } catch (e) {
      errors = { ...errors, push: errorMessage(e) };
    } finally {
      pushBusy = false;
    }
  }

  async function removeDevice(d: PushSubscriptionInfo) {
    try {
      if (d.id === thisId) {
        await unsubscribe();
        subscribed = false;
      } else {
        await api.removePushSubscription({ id: d.id });
      }
      devices = (devices ?? []).filter((x) => x.id !== d.id);
    } catch (e) {
      errors = { ...errors, devices: errorMessage(e) };
    }
  }

  async function sendTest() {
    testing = true;
    testResult = null;
    errors = { ...errors, test: '' };
    try {
      const r = await api.testPush();
      const after = await api.pushSubscriptions();
      const dropped = Math.max(0, r.subscriptions - after.length);
      const plural = (n: number) => `${n} device${n === 1 ? '' : 's'}`;
      let text = r.delivered ? `Sent to ${r.delivered} of ${plural(r.subscriptions)}.` : `Not delivered to any of ${plural(r.subscriptions)}.`;
      if (dropped) text += ` ${plural(dropped)} removed: the push service no longer accepts ${dropped === 1 ? 'it' : 'them'}. Turn push on again there.`;
      else if (r.delivered < r.subscriptions) text += ' See the error under each device.';
      testResult = { text, ok: r.delivered === r.subscriptions };
      devices = after;
    } catch (e) {
      errors = { ...errors, test: errorMessage(e) };
    } finally {
      testing = false;
    }
  }

  async function allowTabAlerts() {
    perm = await requestPermission();
  }
</script>

<section class="card pad" aria-labelledby="s-notify">
  <h2 id="s-notify">Notifications</h2>
  <p class="muted intro">Tandem stays quiet until something needs you. Choose where it can reach you and what is worth it.</p>

  {#if loadError}
    <p class="field-error" role="alert">{loadError} <button class="btn sm" onclick={load}>Try again</button></p>
  {/if}

  <div class="row">
    <div class="grow">
      <label class="row-title" for="push-toggle"><Icon name="phone" size={15} /> Push to this device</label>
      <p class="hint" id="push-hint">{pushHint(pstate)}</p>
      {#if errors.push}<p class="field-error" role="alert">{errors.push}</p>{/if}
    </div>
    <input
      id="push-toggle"
      class="switch"
      type="checkbox"
      role="switch"
      checked={pstate === 'on'}
      disabled={!pushToggleEnabled(pstate) || pushBusy}
      aria-describedby="push-hint"
      onchange={(e) => {
        e.currentTarget.checked = pstate === 'on';
        void togglePush();
      }}
    />
  </div>

  {#if pstate === 'unsupported' || pstate === 'server-off'}
    <div class="row">
      <div class="grow">
        <span class="row-title">Alerts while Tandem is open in a background tab</span>
        <p class="hint">Without push, the open app can still show system notifications.</p>
      </div>
      {#if perm === 'granted'}<span class="badge ok"><Icon name="check" size={12} /> Allowed</span>
      {:else if perm === 'denied'}<span class="badge danger">Blocked</span>
      {:else if perm !== 'unsupported'}<button class="btn sm" onclick={allowTabAlerts}><Icon name="bell" size={14} /> Allow</button>{/if}
    </div>
  {/if}

  <div class="block">
    <div class="block-head">
      <h3 class="section-title">Devices {#if devices?.length}<span class="badge">{devices.length}</span>{/if}</h3>
      <div class="block-actions">
        {#if settings}
          <label class="inline-switch">
            <input class="switch sm" type="checkbox" role="switch" checked={settings.push_enabled} onchange={(e) => save('push_enabled', { push_enabled: e.currentTarget.checked })} />
            Deliver push
          </label>
        {/if}
        <button class="btn sm" onclick={sendTest} disabled={testing || !devices?.length}><Icon name="send" size={13} /> {testing ? 'Sending…' : 'Send test'}</button>
      </div>
    </div>
    <div aria-live="polite">
      {#if testResult}<p class="hint {testResult.ok ? 'ok-text' : 'warn-text'}">{testResult.text}</p>{/if}
      {#if errors.test}<p class="field-error" role="alert">{errors.test}</p>{/if}
      {#if errors.push_enabled}<p class="field-error" role="alert">{errors.push_enabled}</p>{/if}
      {#if settings && !settings.push_enabled && devices?.length}<p class="hint warn-text">Push is paused for every device; the app and other channels still get notifications.</p>{/if}
    </div>
    {#if devices === null}
      <p class="faint small">{loadError ? '' : 'Loading…'}</p>
    {:else if !devices.length}
      <p class="faint small">No devices yet. Turn on push above on each phone or computer you want to hear on.</p>
    {:else}
      <ul class="devices">
        {#each devices as d (d.id)}
          <li>
            <span class="ico"><Icon name="phone" size={16} /></span>
            <div class="grow">
              <strong>{d.label || d.host || 'Device'}</strong>
              {#if d.id === thisId}<span class="badge accent this">this device</span>{/if}
              <div class="faint small">
                {d.host ?? ''} · added {relativeTime(d.created_at, app.now)}{#if d.last_ok_at} · last delivered {relativeTime(d.last_ok_at, app.now)}{/if}
              </div>
              {#if d.last_error}<div class="small danger-text">{d.last_error}{d.failures ? ` (${d.failures} failed)` : ''}</div>{/if}
            </div>
            <button class="icon-btn" onclick={() => removeDevice(d)} aria-label="Remove {d.label || d.host || 'device'}{d.id === thisId ? ' (this device)' : ''}" title="Remove"><Icon name="trash" size={16} /></button>
          </li>
        {/each}
      </ul>
    {/if}
    {#if errors.devices}<p class="field-error" role="alert">{errors.devices}</p>{/if}
  </div>

  {#if settings}
    <div class="block">
      <h3 class="section-title">What reaches you</h3>

      <fieldset class="field">
        <legend class="row-title">Minimum urgency {#if savedField === 'min_urgency'}<span class="saved" role="status">Saved</span>{/if}</legend>
        <div class="segmented">
          {#each URGENCIES as u (u.value)}
            <label class:on={settings.min_urgency === u.value}>
              <input type="radio" name="min-urgency" value={u.value} checked={settings.min_urgency === u.value} onchange={() => save('min_urgency', { min_urgency: u.value })} />
              {u.label}
            </label>
          {/each}
        </div>
        <p class="hint">{URGENCIES.find((u) => u.value === settings?.min_urgency)?.hint}. Less urgent things wait for the brief.</p>
        {#if errors.min_urgency}<p class="field-error" role="alert">{errors.min_urgency}</p>{/if}
      </fieldset>

      <div class="field">
        <div class="row tight">
          <div class="grow">
            <label class="row-title" for="quiet-toggle"><Icon name="moon" size={15} /> Quiet hours {#if savedField === 'quiet_hours'}<span class="saved" role="status">Saved</span>{/if}</label>
            <p class="hint" id="quiet-hint">{quietOn ? `${quietText}. Only critical breaks through; the rest waits for morning.` : 'Off: notifications arrive at any hour.'}</p>
          </div>
          <input id="quiet-toggle" class="switch" type="checkbox" role="switch" checked={quietOn} aria-describedby="quiet-hint" onchange={(e) => toggleQuiet(e.currentTarget.checked)} />
        </div>
        <div class="times" class:off={!quietOn}>
          <label>From <input class="input" type="time" bind:value={qStart} disabled={!quietOn} onchange={saveQuietTimes} /></label>
          <label>to <input class="input" type="time" bind:value={qEnd} disabled={!quietOn} onchange={saveQuietTimes} /></label>
          <span class="faint small">{app.config?.timezone ?? ''}</span>
        </div>
        {#if errors.quiet_hours}<p class="field-error" role="alert">{errors.quiet_hours}</p>{/if}
      </div>

      <div class="field">
        <div class="row tight">
          <div class="grow">
            <label class="row-title" for="max-hour">At most per hour {#if savedField === 'max_per_hour'}<span class="saved" role="status">Saved</span>{/if}</label>
            <p class="hint" id="max-hint">Past this, notifications fold into the next brief. Critical ones always go through.</p>
          </div>
          <input id="max-hour" class="input num" type="number" min="1" max="60" step="1" inputmode="numeric" bind:value={maxPerHour} aria-describedby="max-hint" onchange={saveMax} />
        </div>
        {#if errors.max_per_hour}<p class="field-error" role="alert">{errors.max_per_hour}</p>{/if}
      </div>
    </div>
  {/if}
</section>

<style>
  .pad {
    padding: 18px 20px;
  }
  h2 {
    font-size: 16px;
    margin-bottom: 4px;
  }
  p {
    margin: 0;
  }
  .intro {
    margin-bottom: 8px;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 12px 0;
    border-top: 1px solid var(--border);
  }
  .row.tight {
    border-top: none;
    padding: 0;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .row-title {
    font-weight: 600;
    display: inline-flex;
    align-items: center;
    gap: 7px;
  }
  .hint {
    font-size: 13px;
    color: var(--text-2);
    margin-top: 2px;
  }
  .small {
    font-size: 12.5px;
  }
  .field-error {
    color: var(--danger);
    font-size: 13px;
    margin-top: 4px;
  }
  .danger-text {
    color: var(--danger);
  }
  .ok-text {
    color: var(--ok);
  }
  .warn-text {
    color: var(--warn);
  }
  .saved {
    font-size: 12px;
    font-weight: 500;
    color: var(--ok);
  }
  .block {
    border-top: 1px solid var(--border);
    padding-top: 14px;
    margin-top: 2px;
  }
  .block .section-title {
    margin-bottom: 8px;
  }
  .block-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .block-head .section-title {
    margin: 0;
  }
  .block-actions {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .inline-switch {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-size: 13px;
    color: var(--text-2);
  }
  .devices {
    list-style: none;
    margin: 8px 0 4px;
    padding: 0;
  }
  .devices li {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 0;
  }
  .devices li + li {
    border-top: 1px solid var(--border);
  }
  .this {
    margin-left: 6px;
  }
  .ico {
    color: var(--text-3);
    display: grid;
  }
  .field {
    border: none;
    margin: 0;
    padding: 0 0 16px;
    min-width: 0;
  }
  legend {
    padding: 0;
    margin-bottom: 8px;
  }
  .segmented {
    display: inline-grid;
    grid-auto-flow: column;
    grid-auto-columns: 1fr;
    background: var(--surface-2);
    border-radius: 999px;
    padding: 3px;
    gap: 2px;
    max-width: 100%;
  }
  .segmented label {
    position: relative;
    text-align: center;
    padding: 5px 14px;
    border-radius: 999px;
    font-size: 13.5px;
    font-weight: 550;
    color: var(--text-2);
    cursor: pointer;
  }
  .segmented label.on {
    background: var(--text);
    color: var(--bg);
  }
  .segmented input {
    position: absolute;
    opacity: 0;
    inset: 0;
    margin: 0;
    cursor: pointer;
  }
  .segmented label:has(input:focus-visible) {
    box-shadow: var(--focus);
  }
  .times {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 10px;
    flex-wrap: wrap;
  }
  .times label {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 13.5px;
    color: var(--text-2);
  }
  .times .input {
    width: 124px;
  }
  .times.off {
    opacity: 0.55;
  }
  .input.num {
    width: 80px;
    text-align: right;
  }

  /* switch: a checkbox with role=switch */
  .switch {
    appearance: none;
    flex: none;
    position: relative;
    width: 44px;
    height: 26px;
    margin: 0;
    border-radius: 999px;
    background: var(--border-strong);
    cursor: pointer;
    transition: background 0.15s;
  }
  .switch::after {
    content: '';
    position: absolute;
    top: 3px;
    left: 3px;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background: #fff;
    box-shadow: 0 1px 2px rgb(0 0 0 / 0.25);
    transition: transform 0.15s;
  }
  .switch:checked {
    background: var(--accent);
  }
  .switch:checked::after {
    transform: translateX(18px);
  }
  .switch:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
  .switch.sm {
    width: 34px;
    height: 20px;
  }
  .switch.sm::after {
    width: 14px;
    height: 14px;
  }
  .switch.sm:checked::after {
    transform: translateX(14px);
  }
  @media (forced-colors: active) {
    .switch {
      border: 1px solid CanvasText;
    }
    .switch:checked::after {
      background: Highlight;
    }
  }
  @media (max-width: 480px) {
    .segmented {
      display: grid;
      width: 100%;
    }
    .segmented label {
      padding: 6px 4px;
    }
  }
</style>
