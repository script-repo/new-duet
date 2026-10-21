# Tandem web app

The Svelte 5 PWA for Tandem: Thread, Today, Activity, Library and Settings over the REST + SSE API in
[`docs/SPEC.md`](../docs/SPEC.md) §10–11. It compiles to static files in `../src/tandem/server/static`, which
the Python server serves on the same port, so nothing here runs at runtime. No runtime dependencies.

```bash
npm ci
npm run dev      # Vite on :5173, /api proxied to http://127.0.0.1:8000 (run `uv run tandem serve`)
npm run build    # -> ../src/tandem/server/static
npm run check    # svelte-check, warnings fail
npm test         # vitest
npm run mock     # after a build: a stand-in API with demo data and a live stream on :8000 (TOKEN=... for sign-in)
npm run icons    # re-render public/icon-*.png from the same geometry as public/icon.svg
```

## Layout

| Path | What |
|---|---|
| `src/lib/types.ts` | Entities and SSE events (SPEC §10.2–10.3) |
| `src/lib/api.ts` | `fetch` client; same-origin cookie; a 401 anywhere shows sign-in |
| `src/lib/events.ts`, `stream.ts` | Decode SSE messages; one `EventSource` with reconnect and refetch |
| `src/lib/reducer.ts` | Pure reducer: events and snapshots → state (dedupe, ordering, Today kept in step) |
| `src/lib/store.svelte.ts` | Runes store: state, route (`#/today`), actions, toasts, notifications |
| `src/lib/approval.ts` | Approval card drafts → `approve` / `edit` / `reject` bodies (only `editable` keys change) |
| `src/lib/markdown.ts` | Small markdown renderer; escapes all HTML first |
| `src/lib/voice.ts` | Web Speech API, else `MediaRecorder` → `POST /api/voice/transcribe` |
| `src/components/` | Screens, cards (`cards/`), composer, preview dialog (sandboxed iframe) |
| `public/` | Manifest, icons, `sw.js` (caches the shell; `/api` is network-only) |
| `scripts/mock-server.mjs` | Dev-only fake API used for screenshots and manual testing |

Design: warm neutrals with one clay accent, serif display type for greetings and headings, light and dark
from `prefers-color-scheme`. Left rail at ≥ 900 px, plus a Today panel beside the Thread at ≥ 1280 px;
bottom tabs on phones with Settings in the header.
