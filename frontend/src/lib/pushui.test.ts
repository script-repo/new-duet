import { describe, expect, it } from 'vitest';
import { pushHint, pushState, pushToggleEnabled, type PushFacts } from './pushui';
import { splitHash } from './ui';

const base: PushFacts = { supported: true, iosNeedsInstall: false, permission: 'default', subscribed: false, serverEnabled: true };

describe('pushState', () => {
  it('walks the cases in priority order', () => {
    expect(pushState({ ...base, iosNeedsInstall: true, supported: false })).toBe('ios-install');
    expect(pushState({ ...base, supported: false })).toBe('unsupported');
    expect(pushState({ ...base, serverEnabled: false })).toBe('server-off');
    expect(pushState({ ...base, permission: 'denied' })).toBe('denied');
    expect(pushState({ ...base, permission: 'granted', subscribed: true })).toBe('on');
    expect(pushState({ ...base, permission: 'granted', subscribed: false })).toBe('off');
    expect(pushState({ ...base, permission: 'default', subscribed: true })).toBe('off');
  });
  it('enables the toggle only when it can act', () => {
    expect(pushToggleEnabled('on')).toBe(true);
    expect(pushToggleEnabled('denied')).toBe(false);
    expect(pushHint('ios-install')).toMatch(/Add to Home Screen/);
  });
});

describe('splitHash', () => {
  it('splits route and query', () => {
    expect(splitHash('#/connections?connected=gmail').path).toBe('connections');
    expect(splitHash('#/connections?connected=gmail').params.get('connected')).toBe('gmail');
    expect(splitHash('#/evals').path).toBe('evals');
    expect(splitHash('').path).toBe('');
  });
});
