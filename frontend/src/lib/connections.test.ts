import { describe, expect, it } from 'vitest';
import { cardState, deliveryLabel, envHint, oauthResult, otherConnections, providerCards, statusText } from './connections';
import type { Connection, ConnectionsResponse } from './types';

const conn = (over: Partial<Connection>): Connection => ({
  name: 'gmail', type: 'gmail', provider: 'google', display_name: 'Gmail', oauth: true, configured: false,
  client_configured: true, connected: false, account: null, scopes: [], expires_at: null, status: 'disconnected',
  error: null, last_poll: null, tools: ['gmail_search'], push: { mode: 'poll', detail: 'not connected' }, ...over,
});

const resp = (connections: Connection[]): ConnectionsResponse => ({
  connections,
  available: [
    { type: 'slack', provider: 'slack', display_name: 'Slack', default_name: 'slack', env: ['SLACK_CLIENT_ID', 'SLACK_CLIENT_SECRET'], client_configured: true, in_use: false, docs: '' },
    { type: 'm365', provider: 'microsoft', display_name: 'Microsoft 365', default_name: 'outlook', env: ['MS_CLIENT_ID', 'MS_CLIENT_SECRET'], client_configured: false, in_use: false, docs: '' },
    { type: 'gmail', provider: 'google', display_name: 'Gmail', default_name: 'gmail', env: ['GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET'], client_configured: true, in_use: true, docs: '' },
    { type: 'google_calendar', provider: 'google', display_name: 'Google Calendar', default_name: 'calendar', env: [], client_configured: true, in_use: true, docs: '' },
  ],
  redirect_uri: 'http://x/api/oauth/callback', public_url: 'http://x', push_capable: false,
});

describe('providerCards', () => {
  it('orders Gmail, Calendar, Slack, Microsoft 365 and joins the live instance', () => {
    const r = resp([
      conn({ connected: true, status: 'ok', account: 'sam@example.com' }),
      conn({ name: 'calendar', type: 'google_calendar', display_name: 'Google Calendar', status: 'error', error: 'refresh failed' }),
      conn({ name: 'demo', type: 'fake', provider: null, oauth: false, connected: true, status: 'ok' }),
    ]);
    const cards = providerCards(r);
    expect(cards.map((c) => c.type)).toEqual(['gmail', 'google_calendar', 'slack', 'm365']);
    expect(cards[0]).toMatchObject({ name: 'gmail', state: 'connected', status: 'Connected as sam@example.com' });
    expect(cards[1]).toMatchObject({ state: 'reconnect', status: 'Needs reconnect' });
    expect(cards[2]).toMatchObject({ name: 'slack', connection: null, state: 'disconnected', status: 'Not connected' });
    expect(cards[3]).toMatchObject({ name: 'outlook', state: 'unconfigured', status: 'Not set up' });
    expect(otherConnections(r).map((c) => c.name)).toEqual(['demo']);
  });

  it('keeps an OAuth instance whose type is not offered', () => {
    const cards = providerCards({ ...resp([conn({ name: 'work-mail', type: 'gmail2', provider: 'google' })]), available: [] });
    expect(cards).toHaveLength(1);
    expect(cards[0]).toMatchObject({ name: 'work-mail', env: ['GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET'] });
  });
});

describe('connection status', () => {
  it('maps state and text', () => {
    expect(cardState(null, false)).toBe('unconfigured');
    expect(cardState(conn({ connected: true, status: 'ok' }), true)).toBe('connected');
    expect(cardState(conn({ connected: true, status: 'error' }), true)).toBe('reconnect');
    expect(statusText('connected', conn({}))).toBe('Connected');
  });
  it('names env vars', () => {
    expect(envHint(['A', 'B'])).toBe('Set A and B');
    expect(envHint(['A', 'B', 'C'])).toBe('Set A, B and C');
    expect(envHint([])).toMatch(/client id/);
  });
  it('labels push vs poll', () => {
    expect(deliveryLabel(null)).toBeNull();
    expect(deliveryLabel(conn({ push: { mode: 'push', detail: 'watch until Nov 1' } }))).toEqual({ label: 'Push', tone: 'ok', detail: 'watch until Nov 1' });
    expect(deliveryLabel(conn({ push: { mode: 'poll', detail: '' } }))?.label).toBe('Polling');
  });
});

describe('oauthResult', () => {
  it('reads the callback params', () => {
    expect(oauthResult(new URLSearchParams('connected=gmail'))).toEqual({ kind: 'connected', name: 'gmail' });
    expect(oauthResult(new URLSearchParams('error=access_denied&name=slack'))).toEqual({ kind: 'error', message: 'access_denied', name: 'slack' });
    expect(oauthResult(new URLSearchParams('error='))).toEqual({ kind: 'error', message: 'Authorization failed', name: null });
    expect(oauthResult(new URLSearchParams(''))).toBeNull();
  });
});
