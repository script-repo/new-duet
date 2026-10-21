// Tandem service worker: caches the app shell so the PWA opens offline. API calls are network-only.
const CACHE = 'tandem-shell-v2';
const SHELL = ['/', '/manifest.webmanifest', '/icon.svg', '/icon-192.png', '/icon-512.png', '/apple-touch-icon.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname === '/api') return; // network-only, never cached

  if (req.mode === 'navigate') {
    // network first so a new build shows at once; the cached shell when offline
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put('/', copy));
          }
          return res;
        })
        .catch(() => caches.match('/').then((r) => r || Response.error())),
    );
    return;
  }

  // hashed build assets and icons: cache first, filled on first use
  event.respondWith(
    caches.match(req).then(
      (hit) =>
        hit ||
        fetch(req).then((res) => {
          if (res.ok && res.type === 'basic') {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        }),
    ),
  );
});

// ---- Web Push (SPEC §15.4) -----------------------------------------------------------------------------------
// The payload is {title, body, urgency, url, tag, message_id?, approval_id?, actions}, encrypted end to end.

const ICON = '/icon-192.png';

function readPush(event) {
  try {
    return event.data ? event.data.json() : {};
  } catch {
    return { body: event.data ? event.data.text() : '' };
  }
}

async function focusedClient() {
  const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  return wins.find((w) => w.visibilityState === 'visible' && w.focused);
}

async function showPush(p) {
  // the open, focused app already shows it in the thread (and as a toast)
  if (await focusedClient()) return;
  const urgent = p.urgency === 'high' || p.urgency === 'critical';
  await self.registration.showNotification(p.title || 'Tandem', {
    body: p.body || '',
    icon: ICON,
    badge: ICON,
    tag: p.tag || undefined,
    renotify: !!p.tag,
    requireInteraction: urgent,
    data: { url: p.url || '/#/thread', approval_id: p.approval_id || null, message_id: p.message_id ?? null },
    actions: Array.isArray(p.actions)
      ? p.actions.filter((a) => a && (a.action === 'approve' || a.action === 'open')).map((a) => ({ action: a.action, title: a.title }))
      : [],
  });
}

self.addEventListener('push', (event) => {
  event.waitUntil(showPush(readPush(event)));
});

async function openUrl(target) {
  const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  for (const w of wins) {
    if ('focus' in w) {
      if ('navigate' in w) await w.navigate(target).catch(() => {});
      return w.focus();
    }
  }
  return self.clients.openWindow(target);
}

async function approve(data) {
  try {
    const res = await fetch(`/api/approvals/${encodeURIComponent(data.approval_id)}/decision`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ decision: 'approve' }),
    });
    if (!res.ok) throw new Error(String(res.status));
    await self.registration.showNotification('Approved', {
      body: 'Tandem is on it.',
      icon: ICON,
      badge: ICON,
      tag: `tandem-approved-${data.approval_id}`,
      data: { url: data.url || '/#/thread' },
    });
  } catch {
    // signed out, already decided or offline: show the card instead
    await openUrl(data.url || '/#/thread');
  }
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  if (event.action === 'approve' && data.approval_id) {
    event.waitUntil(approve(data));
    return;
  }
  event.waitUntil(openUrl(data.url || '/'));
});

// The push service rotated or expired the subscription: subscribe again with the server's key.
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil(
    (async () => {
      const res = await fetch('/api/push/vapid', { credentials: 'include' });
      if (!res.ok) return;
      const { public_key, enabled } = await res.json();
      if (!enabled) return;
      const padded = public_key + '='.repeat((4 - (public_key.length % 4)) % 4);
      const raw = atob(padded.replace(/-/g, '+').replace(/_/g, '/'));
      const key = Uint8Array.from(raw, (c) => c.charCodeAt(0));
      const sub = await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
      const old = event.oldSubscription;
      if (old && old.endpoint !== sub.endpoint) {
        await fetch('/api/push/subscriptions', {
          method: 'DELETE',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ endpoint: old.endpoint }),
        }).catch(() => {});
      }
      await fetch('/api/push/subscriptions', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sub.toJSON()),
      });
    })().catch(() => {}),
  );
});
