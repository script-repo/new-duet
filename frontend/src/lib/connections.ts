// Connections page (SPEC §15.3): one card per OAuth provider, from the server's `available` and `connections`.

import type { AvailableConnector, Connection, ConnectionsResponse } from './types';

/** Provider cards in a stable order; anything else the server offers follows. */
export const PROVIDER_ORDER = ['gmail', 'google_calendar', 'slack', 'm365'];

export type CardState = 'connected' | 'reconnect' | 'disconnected' | 'unconfigured';

export interface ProviderCard {
  type: string;
  provider: string;
  display_name: string;
  /** the instance to authorize / disconnect (`gmail`, `outlook`, ...) */
  name: string;
  connection: Connection | null;
  available: AvailableConnector | null;
  state: CardState;
  status: string;
  env: string[];
}

export const DOCS_URL = 'https://github.com/script-repo/new-duet/blob/main/docs/CONNECTORS.md';

/** Default env var names per provider, for connections the server doesn't list in `available`. */
const PROVIDER_ENV: Record<string, string[]> = {
  google: ['GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET'],
  slack: ['SLACK_CLIENT_ID', 'SLACK_CLIENT_SECRET'],
  microsoft: ['MS_CLIENT_ID', 'MS_CLIENT_SECRET'],
};

export function cardState(c: Connection | null, clientConfigured: boolean): CardState {
  if (!clientConfigured) return 'unconfigured';
  if (!c) return 'disconnected';
  if (c.status === 'error') return 'reconnect';
  if (c.connected) return 'connected';
  return 'disconnected';
}

export function statusText(state: CardState, c: Connection | null): string {
  switch (state) {
    case 'connected':
      return c?.account ? `Connected as ${c.account}` : 'Connected';
    case 'reconnect':
      return 'Needs reconnect';
    case 'unconfigured':
      return 'Not set up';
    default:
      return 'Not connected';
  }
}

export const STATE_TONE: Record<CardState, string> = { connected: 'ok', reconnect: 'danger', disconnected: '', unconfigured: 'warn' };

function rank(type: string): number {
  const i = PROVIDER_ORDER.indexOf(type);
  return i < 0 ? PROVIDER_ORDER.length : i;
}

/** One card per OAuth connector type: the offered types, plus OAuth instances of types not offered. */
export function providerCards(r: ConnectionsResponse): ProviderCard[] {
  const cards: ProviderCard[] = [];
  const seen = new Set<string>();
  const oauth = r.connections.filter((c) => c.oauth);
  for (const a of r.available) {
    const c = oauth.find((x) => x.type === a.type && x.name === a.default_name) ?? oauth.find((x) => x.type === a.type) ?? null;
    if (c) seen.add(c.name);
    const clientConfigured = c ? c.client_configured : a.client_configured;
    const state = cardState(c, clientConfigured);
    cards.push({
      type: a.type,
      provider: a.provider,
      display_name: a.display_name,
      name: c?.name ?? a.default_name,
      connection: c,
      available: a,
      state,
      status: statusText(state, c),
      env: a.env,
    });
  }
  for (const c of oauth) {
    if (seen.has(c.name)) continue;
    const state = cardState(c, c.client_configured);
    cards.push({
      type: c.type,
      provider: c.provider ?? '',
      display_name: c.display_name || c.name,
      name: c.name,
      connection: c,
      available: null,
      state,
      status: statusText(state, c),
      env: PROVIDER_ENV[c.provider ?? ''] ?? [],
    });
  }
  return cards.sort((a, b) => rank(a.type) - rank(b.type));
}

/** The connectors that aren't OAuth providers (demo, webhook, imap, ics, mcp...). */
export function otherConnections(r: ConnectionsResponse): Connection[] {
  return r.connections.filter((c) => !c.oauth);
}

/** "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET" */
export function envHint(env: string[]): string {
  if (!env.length) return 'Set its client id and secret';
  const names = env.length === 1 ? env[0] : `${env.slice(0, -1).join(', ')} and ${env[env.length - 1]}`;
  return `Set ${names}`;
}

/** Push vs poll, for a badge: "Push" / "Polling", with the server's detail as the explanation. */
export function deliveryLabel(c: Connection | null): { label: string; tone: string; detail: string } | null {
  if (!c) return null;
  const detail = c.push?.detail ?? '';
  if (c.push?.mode === 'push') return { label: 'Push', tone: 'ok', detail: detail || 'Changes arrive as they happen' };
  return { label: 'Polling', tone: '', detail: detail || 'Checked on each check-in' };
}

export type OAuthResult = { kind: 'connected'; name: string } | { kind: 'error'; message: string; name: string | null };

/** What the OAuth callback put in the hash query (`?connected=gmail` or `?error=...&name=gmail`). */
export function oauthResult(params: URLSearchParams): OAuthResult | null {
  const err = params.get('error');
  if (err !== null) return { kind: 'error', message: err || 'Authorization failed', name: params.get('name') };
  const ok = params.get('connected');
  if (ok) return { kind: 'connected', name: ok };
  return null;
}
