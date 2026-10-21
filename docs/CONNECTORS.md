# Connectors: Gmail, Google Calendar, Slack, Microsoft 365

Tandem talks to these four services directly over OAuth 2.0 (authorization code + PKCE). You register
an app with each provider once, put its client id and secret in Tandem's environment, and press
**Connect** on the Connections screen. Tokens are encrypted at rest with the server key, refreshed
automatically, and never shown to a model, a log or the UI ([SPEC §15](SPEC.md#15-v02-real-apps-real-phones-real-measurements)).

- [How it fits together](#how-it-fits-together)
- [Environment variables](#environment-variables)
- [Google: Gmail and Calendar](#google-gmail-and-calendar)
- [Slack](#slack)
- [Microsoft 365 (Entra ID)](#microsoft-365-entra-id)
- [Without a public URL](#without-a-public-url)
- [Scopes and why](#scopes-and-why)
- [Testing status](#testing-status)

## How it fits together

1. Set `TANDEM_PUBLIC_URL` to the address your browser (and, for push, the provider) uses to reach
   Tandem, e.g. `https://tandem.example.com`. The OAuth redirect URI is always
   **`{TANDEM_PUBLIC_URL}/api/oauth/callback`**; register exactly that with each provider.
2. Set the provider's client id and secret env vars (below). That alone makes Connect buttons appear:
   a provider whose client id is set and that has no connector in `tandem.yaml` is offered with a
   default name: `gmail`, `calendar` (Google Calendar), `slack`, `outlook` (Microsoft 365). If the
   name is taken by another connector, the type name is used (`google_calendar`, …).
3. Or configure connectors explicitly in `tandem.yaml` (names prefix the tools):

   ```yaml
   connectors:
     - {type: gmail, name: gmail, pubsub_topic: projects/my-project/topics/tandem-gmail,
        push_token_env: GMAIL_PUSH_TOKEN}
     - {type: google_calendar, name: calendar, calendar_id: primary}
     - {type: slack, name: slack, channels: [general, C0123456789]}
     - {type: m365, name: outlook, tenant: common}
   ```

   Common options: `client_id_env` / `client_secret_env` (to use other env var names), `poll_every`
   (default `5m`), `push: false` (never subscribe), `scopes` (extra scopes to request).

4. Press **Connect**. Tandem sends you to the provider with a single-use `state` (10 minutes) and a
   PKCE challenge; the provider sends you back to `/api/oauth/callback`; Tandem exchanges the code,
   stores the token, reads your account, subscribes to push when it can, and returns you to
   `/#/connections?connected=<name>` (or `?error=<message>`).
5. **Disconnect** revokes the token at the provider where an endpoint exists (Google, Slack;
   Microsoft has none, so remove the app under *My Apps* if you want it revoked there), stops push and
   deletes the token and cursors.

Every outward action (send, reply, create an event, post to Slack) is tagged `send` (Slack posts also
`external_post`) and goes through the approval card per `policy.yaml`. Reads are tagged `read`.

## Environment variables

| Variable | Needed for | Notes |
|---|---|---|
| `TANDEM_PUBLIC_URL` | all | Builds the redirect URI and push URLs. Push only when it is `https://`. |
| `TANDEM_SECRET_KEY` | optional | Fernet key for the token vault; else `<data>/secret.key` is created. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Gmail, Calendar | One Google OAuth client serves both. |
| `GMAIL_PUSH_TOKEN` (any name, via `push_token_env`) | Gmail push | A random string you choose; goes in the Pub/Sub push URL. |
| `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET` | Slack | From *Basic Information → App Credentials*. |
| `SLACK_SIGNING_SECRET` (or `signing_secret_env`) | Slack push | Verifies Events API requests. Without it Slack polls. |
| `MS_CLIENT_ID`, `MS_CLIENT_SECRET` | Microsoft 365 | Application (client) id and a client secret *value*. |

Secrets are read from the server environment only, and every value is registered with the redactor.

## Google: Gmail and Calendar

### 1. Project and APIs

1. In the [Google Cloud console](https://console.cloud.google.com/), create a project (or pick one).
2. *APIs & Services → Library*: enable **Gmail API**, **Google Calendar API** and, for Gmail push,
   **Cloud Pub/Sub API**.

### 2. OAuth consent screen

1. *APIs & Services → OAuth consent screen* (in newer consoles: *Google Auth Platform → Branding /
   Audience / Data access*).
2. User type: **Internal** if you are on Google Workspace and only your organization will use it;
   otherwise **External**.
3. Add the scopes listed under [Scopes and why](#scopes-and-why).
4. External apps in **Testing** status: add your Google account under **Test users**. Be aware that
   Google expires refresh tokens of External apps in Testing after about 7 days, so you will have to
   press Connect again weekly. Publishing the app avoids that, but `gmail.readonly` is a *restricted*
   scope and a published app requesting it needs Google's verification. For a personal deployment,
   Internal (Workspace) or weekly reconnects in Testing are the practical options.

### 3. OAuth client

1. *Credentials → Create credentials → OAuth client ID*, type **Web application**.
2. **Authorized redirect URIs**: `{TANDEM_PUBLIC_URL}/api/oauth/callback`, e.g.
   `https://tandem.example.com/api/oauth/callback` (for local testing also
   `http://127.0.0.1:8000/api/oauth/callback`; Google allows `http` only for loopback).
3. Copy the client id and secret into `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

### 4. Gmail push (optional)

Gmail push goes through Pub/Sub: Gmail publishes to a topic, and a push subscription posts to Tandem.

1. *Pub/Sub → Topics → Create topic*, e.g. `tandem-gmail`. Its full name is
   `projects/<project-id>/topics/tandem-gmail`.
2. On the topic, *Permissions → Add principal* `gmail-api-push@system.gserviceaccount.com` with role
   **Pub/Sub Publisher**. (An organization policy that restricts sharing to your domain can block
   this; an org admin must allow it.)
3. Choose a random push token, e.g. `openssl rand -hex 24`, and set it as `GMAIL_PUSH_TOKEN`.
4. *Create subscription* on the topic: delivery type **Push**, endpoint
   `{TANDEM_PUBLIC_URL}/api/webhooks/gmail?token=<GMAIL_PUSH_TOKEN>` (use your connector's name in
   place of `gmail`).
5. In `tandem.yaml`: `pubsub_topic: projects/<project-id>/topics/tandem-gmail` and
   `push_token_env: GMAIL_PUSH_TOKEN`.

Tandem calls `users.watch` on connect and renews it daily (Google requires at least every 7 days).
A push only rings the doorbell: Tandem then reads `users.history.list` from its stored `historyId`
and ignores the payload. Without `push_token_env`, Tandem generates a token and keeps it encrypted;
pinning it in the environment is the way to know the value for the subscription URL.

### 5. Calendar push

Nothing to set up in the console: on connect Tandem calls `events.watch` with a channel whose secret
token it checks on every notification, and renews the channel a day before it expires (7-day TTL).
Requirements: `TANDEM_PUBLIC_URL` must be `https://` with a valid, publicly trusted certificate (no
self-signed certificates, no IP addresses). Older Google docs required verifying the domain in Search
Console; if `events.watch` fails with "Unauthorized WebHook callback channel", verify the domain.

## Slack

Tandem uses a **user token**: it reads your DMs, group DMs and the channels you list, and posts as
you (each post waits on your OK).

### 1. Create the app from a manifest

[api.slack.com/apps](https://api.slack.com/apps) → **Create New App → From an app manifest**, pick
your workspace, paste (replace the URL):

```yaml
display_information:
  name: Tandem
  description: Your proactive assistant
oauth_config:
  redirect_urls:
    - https://tandem.example.com/api/oauth/callback
  scopes:
    user:
      - channels:history
      - groups:history
      - im:history
      - mpim:history
      - channels:read
      - groups:read
      - im:read
      - mpim:read
      - users:read
      - users:read.email
      - search:read
      - chat:write
settings:
  event_subscriptions:
    request_url: https://tandem.example.com/api/webhooks/slack
    user_events:
      - message.im
      - message.mpim
      - message.channels
      - message.groups
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
```

- Without a public URL, delete the `event_subscriptions` block; Tandem then polls.
- Slack verifies the request URL right away by sending a `url_verification` challenge, which Tandem
  answers once `SLACK_SIGNING_SECRET` is set and the server is running with a `slack` connector (set
  `SLACK_CLIENT_ID` or configure it). If you created the app first, re-verify under
  *Event Subscriptions*.
- `token_rotation_enabled: true` is supported: Tandem stores the refresh token and refreshes before
  expiry.

### 2. Credentials

*Basic Information → App Credentials*: copy **Client ID**, **Client Secret** and **Signing Secret**
into `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_SIGNING_SECRET`. Then press Connect in Tandem;
Slack asks you to allow the user scopes.

### Notes

- Polling covers DMs, group DMs and the channels in `channels` (names or ids), up to
  `max_conversations` (50). A mention in another channel arrives only with push (the event names the
  channel, and Tandem fetches it from the API).
- Push requests are checked with `X-Slack-Signature` (v0 HMAC-SHA256 of `v0:<timestamp>:<body>`) and
  rejected when `X-Slack-Request-Timestamp` is more than 5 minutes off.
- Slack has tightened `conversations.history` rate limits for some apps distributed outside the Slack
  Marketplace. An internal app installed only in your own workspace is the intended setup; check
  Slack's rate-limit documentation if polling returns `ratelimited`.

## Microsoft 365 (Entra ID)

### 1. App registration

1. [Microsoft Entra admin center](https://entra.microsoft.com/) → *Identity → Applications → App
   registrations → New registration*.
2. **Supported account types**: your organization only (single tenant), any organization, or any
   organization plus personal Microsoft accounts. Match `tenant` in `tandem.yaml`: your tenant id or
   domain for single tenant, `organizations`, or `common` (default) for work and personal accounts.
3. **Redirect URI**: platform **Web**, `{TANDEM_PUBLIC_URL}/api/oauth/callback`.
4. Copy the **Application (client) ID** into `MS_CLIENT_ID`.

### 2. Client secret

*Certificates & secrets → Client secrets → New client secret*. Copy its **Value** (not the Secret ID)
into `MS_CLIENT_SECRET`. Secrets expire (6–24 months); set a reminder to rotate it.

### 3. API permissions

*API permissions → Add a permission → Microsoft Graph → Delegated permissions*: `offline_access`,
`openid`, `email`, `User.Read`, `Mail.Read`, `Mail.Send`, `Calendars.ReadWrite`.

None of these needs admin consent by default, but many tenants disable user consent for third-party
apps; then an admin must press **Grant admin consent for <tenant>** on this page, or approve the
request from the consent prompt's "Need admin approval" flow.

### Push

On connect Tandem creates two Graph subscriptions (new inbox mail; created/updated/deleted events) with
a random `clientState`, answers Graph's `validationToken` handshake as `text/plain`, rejects
notifications whose `clientState` doesn't match, and renews the subscriptions (PATCH) a day before they
expire (they last 2.5 days; Graph's cap for Outlook resources is about 7 days). Notifications get a
`202` at once and trigger a delta query in the background.

## Without a public URL

Everything except push works on `http://127.0.0.1:8000`:

- Redirect URIs: Google accepts `http://127.0.0.1:<port>/…` and Microsoft accepts
  `http://localhost:<port>/…`; Slack expects `https` redirect URLs, so for Slack use a tunnel
  (Cloudflare Tunnel, Tailscale Funnel, …) or a reverse proxy with TLS. Set `TANDEM_PUBLIC_URL` to the
  address you registered.
- Connectors **poll** on each check-in (`schedule.checkin_every`, default 15 minutes) from their
  cursors (Gmail `historyId`, Calendar `syncToken`, Slack per-channel `ts`, Graph `deltaLink`), and the
  Connections screen says "polling … push needs an https TANDEM_PUBLIC_URL".
- `POST /api/checkin/run` polls immediately.

## Scopes and why

| Connector | Scope | Why |
|---|---|---|
| Gmail | `openid`, `email` | Know which account is connected (id token) |
| | `gmail.readonly` | Read new mail, history and threads; `users.watch` |
| | `gmail.send` | Send and reply (only after your approval) |
| Google Calendar | `openid`, `email` | Account |
| | `calendar.events` | Read events, sync tokens, `events.watch`, create events with invites |
| Slack (user) | `im:history`, `mpim:history`, `channels:history`, `groups:history` | Read DMs, group DMs and listed channels |
| | `im:read`, `mpim:read`, `channels:read`, `groups:read` | List your conversations |
| | `users:read`, `users:read.email` | Show who wrote, match VIP rules by email |
| | `search:read` | The `_search` tool |
| | `chat:write` | Post as you (only after your approval) |
| Microsoft 365 | `openid`, `email`, `offline_access`, `User.Read` | Account and refresh tokens |
| | `Mail.Read` | Inbox delta, search, read, mail subscriptions |
| | `Mail.Send` | Send and reply (only after your approval) |
| | `Calendars.ReadWrite` | Calendar delta, Today's events, create events with invites |

Tandem never asks for `gmail.modify`, delete permissions or admin scopes. Add more with `scopes:` in a
connector's config if a custom tool needs them.

## Testing status

Honestly: these connectors were developed and tested **only against recorded and hand-written API
responses** (`tests/fixtures/connectors/`, mocked with `respx`). The tests cover the OAuth flow (PKCE,
single-use and expiring state, the callback, refresh on expiry and on 401, revocation), each
connector's polling and normalization (including Gmail's expired `historyId`, Calendar's `410 Gone`
and Graph's `deltaLink`), the push handshakes (Slack challenge and signatures, Graph `validationToken`
and `clientState`, the Gmail push token, the Calendar channel token) and every action through the
approval gate with the exact request body. **No test has run against the live Google, Slack or
Microsoft APIs yet**, and the console steps above have not been re-walked click by click. Please report
anything that differs.
