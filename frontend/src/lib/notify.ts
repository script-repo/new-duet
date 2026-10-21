// System notifications for `notify` events while the page is hidden.

import type { NotifyPayload } from './types';

export function notificationsSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window;
}

export function permission(): NotificationPermission | 'unsupported' {
  return notificationsSupported() ? Notification.permission : 'unsupported';
}

export async function requestPermission(): Promise<NotificationPermission | 'unsupported'> {
  if (!notificationsSupported()) return 'unsupported';
  return Notification.requestPermission();
}

export async function showSystemNotification(n: NotifyPayload): Promise<void> {
  if (permission() !== 'granted') return;
  const opts: NotificationOptions = {
    body: n.body,
    tag: n.message_id != null ? `tandem-${n.message_id}` : undefined,
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    data: { url: '/#/thread' },
  };
  try {
    // Android only shows notifications through the service worker
    const reg = 'serviceWorker' in navigator ? await navigator.serviceWorker.getRegistration() : undefined;
    if (reg) await reg.showNotification(n.title, opts);
    else new Notification(n.title, opts);
  } catch {
    /* blocked or unsupported: the in-app toast still shows */
  }
}
