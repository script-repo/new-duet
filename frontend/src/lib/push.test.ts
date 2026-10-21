import { describe, expect, it } from 'vitest';
import { iosNeedsInstall, isIOS, isStandalone, pushSupported, urlBase64ToUint8Array } from './push';

const nav = (userAgent: string, platform = '', maxTouchPoints = 0) => ({ userAgent, platform, maxTouchPoints });
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1';
const MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15';
const ANDROID = 'Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/140.0 Mobile Safari/537.36';

describe('urlBase64ToUint8Array', () => {
  it('decodes base64url without padding', () => {
    expect([...urlBase64ToUint8Array('AQID')]).toEqual([1, 2, 3]);
    expect([...urlBase64ToUint8Array('-_8')]).toEqual([0xfb, 0xff]);
    expect([...urlBase64ToUint8Array('AQ')]).toEqual([1]);
  });

  it('decodes a 65-byte VAPID public key (RFC 8291 example)', () => {
    const key = urlBase64ToUint8Array(
      'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8',
    );
    expect(key.length).toBe(65);
    expect(key[0]).toBe(4);
  });
});

describe('platform detection', () => {
  it('detects iPhone and iPadOS (a Mac with touch), not Mac or Android', () => {
    expect(isIOS(nav(IPHONE, 'iPhone'))).toBe(true);
    expect(isIOS(nav(MAC, 'MacIntel', 5))).toBe(true);
    expect(isIOS(nav(MAC, 'MacIntel', 0))).toBe(false);
    expect(isIOS(nav(ANDROID, 'Linux armv8l', 5))).toBe(false);
  });

  it('detects standalone from display-mode or navigator.standalone', () => {
    expect(isStandalone({ matchMedia: () => ({ matches: true }) })).toBe(true);
    expect(isStandalone({ matchMedia: () => ({ matches: false }), navigator: { standalone: true } })).toBe(true);
    expect(isStandalone({ matchMedia: () => ({ matches: false }) })).toBe(false);
    expect(isStandalone({})).toBe(false);
  });

  it('asks iOS users to add to the Home Screen first', () => {
    const tab = { matchMedia: () => ({ matches: false }) };
    const app = { matchMedia: () => ({ matches: true }) };
    expect(iosNeedsInstall(nav(IPHONE, 'iPhone'), tab)).toBe(true);
    expect(iosNeedsInstall(nav(IPHONE, 'iPhone'), app)).toBe(false);
    expect(iosNeedsInstall(nav(ANDROID), tab)).toBe(false);
  });

  it('reports no push support outside a browser', () => {
    expect(pushSupported()).toBe(false);
  });
});
