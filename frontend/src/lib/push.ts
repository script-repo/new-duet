// Web Push (SPEC §15.4): subscribe this device so notifications reach it while the app is closed.
// The service worker (public/sw.js) shows them and handles Approve / Open.

import { api } from './api';
import type { PushSubscriptionInfo } from './types';

const ID_KEY = 'tandem-push-id'; // the server's id for this device's subscription

type Nav = Pick<Navigator, 'userAgent' | 'platform' | 'maxTouchPoints'>;

/** Service worker + PushManager + Notification: everything Web Push needs. */
export function pushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof navigator !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  );
}

/** iPhone, iPod or iPad (iPadOS reports itself as a Mac with touch). */
export function isIOS(nav: Nav | undefined = typeof navigator !== 'undefined' ? navigator : undefined): boolean {
  if (!nav) return false;
  if (/iPad|iPhone|iPod/.test(nav.userAgent)) return true;
  return nav.platform === 'MacIntel' && (nav.maxTouchPoints ?? 0) > 1;
}

type Win = { matchMedia?: (q: string) => { matches: boolean }; navigator?: { standalone?: boolean } };

/** Opened from the Home Screen / as an installed app rather than in a browser tab. */
export function isStandalone(win: Win | undefined = typeof window !== 'undefined' ? (window as unknown as Win) : undefined): boolean {
  if (!win) return false;
  if (win.navigator?.standalone === true) return true; // iOS Safari
  return !!win.matchMedia?.('(display-mode: standalone)').matches;
}

/** iOS/iPadOS deliver Web Push only to a PWA added to the Home Screen (16.4+). */
export function iosNeedsInstall(nav?: Nav, win?: Win): boolean {
  return isIOS(nav) && !isStandalone(win);
}

/** A VAPID public key (base64url) as the bytes `applicationServerKey` wants. */
export function urlBase64ToUint8Array(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = base64url.trim() + '='.repeat((4 - (base64url.trim().length % 4)) % 4);
  const raw = atob(padded.replace(/-/g, '+').replace(/_/g, '/'));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function registration(): Promise<ServiceWorkerRegistration> {
  const reg = await navigator.serviceWorker.getRegistration();
  if (reg) return navigator.serviceWorker.ready;
  // dev builds don't register the worker in main.ts; push needs one
  await navigator.serviceWorker.register('/sw.js');
  return navigator.serviceWorker.ready;
}

/** This device's browser subscription, if any. */
export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.getRegistration();
  return reg ? reg.pushManager.getSubscription() : null;
}

/** The server's id for this device (to mark "this device" in the list), if it subscribed here. */
export function currentSubscriptionId(): string | null {
  try {
    return localStorage.getItem(ID_KEY);
  } catch {
    return null;
  }
}

export type SubscribeResult =
  | { ok: true; subscription: PushSubscriptionInfo }
  | { ok: false; reason: 'unsupported' | 'ios-install' | 'denied' | 'disabled' | 'error'; message?: string };

/**
 * Ask permission and subscribe this device. Call it from a tap or click: iOS (and increasingly other
 * browsers) only show the permission prompt in response to a user gesture.
 */
export async function subscribe(): Promise<SubscribeResult> {
  if (iosNeedsInstall()) return { ok: false, reason: 'ios-install' };
  if (!pushSupported()) return { ok: false, reason: 'unsupported' };
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') return { ok: false, reason: 'denied' };
  try {
    const vapid = await api.vapid();
    if (!vapid.enabled) return { ok: false, reason: 'disabled' };
    const reg = await registration();
    const key = urlBase64ToUint8Array(vapid.public_key);
    let sub = await reg.pushManager.getSubscription();
    if (sub && !sameKey(sub, key)) {
      await sub.unsubscribe(); // the server's VAPID key changed: the old subscription is dead
      sub = null;
    }
    sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
    const info = await api.addPushSubscription(sub.toJSON());
    try {
      localStorage.setItem(ID_KEY, info.id);
    } catch {
      /* private mode */
    }
    return { ok: true, subscription: info };
  } catch (err) {
    return { ok: false, reason: 'error', message: err instanceof Error ? err.message : String(err) };
  }
}

/** Unsubscribe this device in the browser and on the server. Returns whether there was one. */
export async function unsubscribe(): Promise<boolean> {
  const sub = await currentSubscription();
  const id = currentSubscriptionId();
  try {
    localStorage.removeItem(ID_KEY);
  } catch {
    /* private mode */
  }
  if (!sub) {
    if (id) await api.removePushSubscription({ id }).catch(() => undefined);
    return false;
  }
  const endpoint = sub.endpoint;
  await sub.unsubscribe().catch(() => false);
  await api.removePushSubscription({ endpoint }).catch(() => undefined);
  return true;
}

function sameKey(sub: PushSubscription, key: Uint8Array): boolean {
  const current = sub.options?.applicationServerKey;
  if (!current) return true; // unknown: keep it
  const a = new Uint8Array(current);
  return a.length === key.length && a.every((b, i) => b === key[i]);
}
