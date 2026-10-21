# Notifications

Tandem stays quiet until something needs you. When it does, the notification goes to the thread (always),
to every device where you turned on notifications (Web Push, even with the app closed), and to any ntfy
topic or webhook you configured. This page explains how that works and how to set it up on each platform.

## How it works

1. **Triage** decides whether an event is worth telling you about (SPEC §7.2).
2. **The notification policy** decides whether to interrupt you *now* (SPEC §7.3): only urgency at or
   above `min_urgency`, never during `quiet_hours` except `critical`, and at most `max_per_hour`, with the
   rest coalesced into one "N more things need you". Approvals and finished tasks push only outside
   quiet hours.
3. **The notifier** carries what the policy decided to the channels:
   - `ui`: a thread message and an SSE `notify` event, which the open app shows as a toast (and a system
     notification while its tab is hidden).
   - `webpush`: an encrypted push to each subscribed device, through that browser's push service
     (Google FCM, Mozilla autopush, Apple, Microsoft).
   - `ntfy` / `webhook`: a POST to the URL you configured, `always` or only as a `fallback`.

Web Push never bypasses the policy: it only carries what step 2 already decided to push. The one
exception is **Send test notification** in Settings (`POST /api/push/test`), which you trigger yourself.

A push carries `{title, body, urgency, url, tag, message_id, approval_id, actions}`. The service worker
shows it with an **Open** button (focus the app at the thread) and, for approval cards, **Approve**,
which posts the decision with your session cookie and confirms with a short "Approved" notification
(if that fails, because you are signed out, the card was already decided or you are offline, it opens
the card instead). **Approve is never offered for cards tagged `pay` or `delete`**: those you decide on
the card itself. High and critical pushes stay on screen until you act (`requireInteraction`, where the
platform supports it). Repeated pushes about the same thing share a `tag` (and a push-service `Topic`),
so they replace each other instead of stacking. When the app is open and focused, the service worker
doesn't show a system notification: the thread already has it.

## Turning it on

Open **Settings → Notifications** on the device and tap **Turn on notifications**. The browser asks for
permission, subscribes, and registers the device with your Tandem server. Each device is listed with its
browser and system ("Chrome on Android"), when it last received a push, and its last error. Tap **Send test
notification** to check the whole path.

Web Push needs a **secure context**: Tandem must be served over HTTPS (any valid certificate, e.g. a
reverse proxy with Let's Encrypt or a Tailscale/Cloudflare tunnel). `http://localhost` and
`http://127.0.0.1` are exempt, which is enough for desktop testing but not for a phone on your LAN.

### Android

Chrome, Edge, Firefox and Samsung Internet all support it, in a tab or installed ("Add to Home screen" /
"Install app"). Installing is recommended: notifications then carry the Tandem icon and open the app
window. Actions (Approve / Open) appear inline under the notification. Android's per-app notification
settings and Do Not Disturb apply as usual.

### Desktop (Windows, macOS, Linux, ChromeOS)

Chrome, Edge, Firefox and Safari 16+ (macOS Ventura and later). The browser must be running (it can be in
the background) for pushes to arrive; installed PWAs in Chrome/Edge receive them while the browser runs.
On macOS, allow notifications for the browser in **System Settings → Notifications**; Safari shows
notifications from sites only after you allow them per site. Action buttons show in Chrome/Edge;
Safari on macOS shows them under "Options".

### iPhone and iPad (iOS/iPadOS 16.4 and later)

Apple delivers Web Push **only to a web app added to the Home Screen**, never to a Safari tab:

1. Open Tandem in Safari, tap **Share → Add to Home Screen**, then open Tandem **from the Home Screen icon**.
2. In Settings → Notifications tap **Turn on notifications**. iOS shows the permission prompt only in
   response to a tap; it cannot be triggered automatically, and if you decline you must re-enable it in
   **iOS Settings → Notifications → Tandem**.
3. Action buttons appear when you **long-press** (or expand) the notification.

Other iOS rules: there is no silent push (every push must show a notification, which Tandem always does);
Focus modes and Scheduled Summary can hold or batch Tandem's notifications, so allow Tandem in your Focus
if you want critical alerts to break through; deleting the Home Screen icon removes the subscription.
The UI detects iOS outside the Home Screen (`iosNeedsInstall()`) and shows the install steps instead of
the button.

## VAPID keys

Tandem identifies itself to push services with a VAPID key pair (RFC 8292, P-256/ES256).

- By default the key is **generated on first start** and stored in the database encrypted with the server
  key (`TANDEM_SECRET_KEY` or `<data>/secret.key`, SPEC §15.1).
- To pin it (several replicas, or rebuilding the database), set `TANDEM_VAPID_PRIVATE_KEY` to a P-256
  private key, either PEM or the raw 32-byte scalar in base64url:

  ```bash
  openssl ecparam -name prime256v1 -genkey -noout | openssl pkcs8 -topk8 -nocrypt > vapid.pem
  export TANDEM_VAPID_PRIVATE_KEY="$(cat vapid.pem)"
  ```

- `TANDEM_VAPID_SUBJECT` (or `notify.vapid_subject` in `tandem.yaml`) is the contact push services may use
  to reach you about abuse or problems: a `mailto:` or `https:` URL. Default `mailto:tandem@localhost`;
  set a real one for production, as some push services reject `localhost` subjects.
- **Rotating the key invalidates every subscription**: browsers bind a subscription to the key it was
  created with. After a rotation (or losing the server key, which loses a generated VAPID key with it),
  pushes fail with `401/403` until each device turns notifications on again (turning them on replaces
  a subscription made with the old key), and failing subscriptions are dropped after five failures in a row.

## Privacy

The payload is encrypted end to end (`aes128gcm`, RFC 8291): only the subscribed browser holds the
private key that decrypts it. The push service (Google, Mozilla, Apple, Microsoft) sees only metadata:
that your server sent something to that device, when, its size, `TTL`, `Urgency` and `Topic` (Tandem's
topics are opaque ids such as `tandem-approval-ap_1a2b3c…`, never content). Subscriptions are stored with
their endpoint and keys in your database; the API lists only the push service's host, a device label and
dates. Notification text may still appear on a lock screen: use your phone's lock-screen privacy settings
if that matters.

## Configuration

```yaml
notify:
  min_urgency: high            # low | normal | high | critical
  quiet_hours: "21:00-07:00"   # or null
  max_per_hour: 4
  push_enabled: true           # Web Push on/off (also in Settings)
  vapid_subject: mailto:you@example.com
  # channels: omit to get ui + webpush. If you list channels, include webpush to keep it.
  channels:
    - {type: ui}
    - {type: webpush}
    - {type: ntfy, url: "https://ntfy.sh/your-secret-topic", when: fallback}
    - {type: webhook, url: "https://example.com/hooks/tandem"}   # when: always (default)
```

**The `webpush` rule:** Web Push is on whenever `push_enabled` is true and at least one device subscribed,
unless you list `channels` yourself without a `{type: webpush}` entry, which turns it off (so an existing
ntfy-only setup keeps behaving exactly as before). `when: fallback` on an ntfy or webhook channel fires
only when no Web Push delivery succeeded (no subscribed device, push off, or every push service failed),
which makes ntfy a safety net for a phone whose subscription lapsed.

`min_urgency`, `quiet_hours`, `max_per_hour` and `push_enabled` can be changed at runtime in Settings
(`PUT /api/settings/notify`). Those values are stored in the database, override `tandem.yaml`, and apply
at once, without a restart.

## API

| Route | Body / answer |
|---|---|
| `GET /api/push/vapid` | `{public_key, enabled}` |
| `GET /api/push/subscriptions` | `[{id, host, label, created_at, last_ok_at, last_error, failures}]` |
| `POST /api/push/subscriptions` | the browser's `PushSubscription.toJSON()` → one subscription as above |
| `DELETE /api/push/subscriptions` | `{endpoint}` or `{id}` → `{ok}` |
| `POST /api/push/test` | → `{delivered, subscriptions}`; `409` when push is off or nobody subscribed |
| `GET /api/settings/notify` | `{min_urgency, quiet_hours, max_per_hour, push_enabled}` |
| `PUT /api/settings/notify` | any subset of those → all four; `quiet_hours` is `"HH:MM-HH:MM"` or `null` |

`GET /api/config` includes `notify.push: {enabled, subscriptions, vapid_public_key}`.

## Troubleshooting

- **The button does nothing / "unsupported"**: the page isn't a secure context (use HTTPS), the browser
  lacks Push API support, or on iOS the app isn't opened from the Home Screen.
- **Permission "denied"**: browsers don't ask twice. Re-allow it in the site settings (lock icon in the
  address bar) or, on iOS, in iOS Settings → Notifications → Tandem.
- **Test says `delivered: 0`**: check the device's `last_error` in Settings. `401/403` usually means the
  VAPID key changed (turn notifications on again on that device) or the subject was rejected (set a real
  `TANDEM_VAPID_SUBJECT`). Network errors mean the server can't reach the push service: allow outbound
  HTTPS to `fcm.googleapis.com`, `*.push.services.mozilla.com`, `*.push.apple.com` and
  `*.notify.windows.com`.
- **A device disappeared from the list**: its push service answered `404`/`410` (the browser
  unsubscribed, the app was uninstalled or the subscription expired), or it failed five times in a row.
  Turn notifications on again on that device.
- **Nothing arrives at night**: that's quiet hours. Only `critical` breaks through; everything else waits
  for the morning brief.
- **Pushes arrive late on Android**: battery optimisation can defer `normal`-urgency pushes; Tandem sends
  `high` and `critical` with `Urgency: high`.
- **iOS shows nothing while Tandem is open**: by design, the open app shows it in the thread.
