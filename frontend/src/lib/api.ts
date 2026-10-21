// The HTTP client. Every request goes with the session cookie (same origin); a 401 anywhere asks for sign-in.

import type { DecisionBody } from './approval';
import type {
  Activity, AppConfig, Approval, Artifact, AuditRow, Brief, MemoryEntry, Message, NotifySettings, PushSubscriptionInfo,
  Skill, TEvent, Task, TaskLog, ThreadSnapshot, Today, VapidInfo,
} from './types';

export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let unauthorized: () => void = () => {};

export function onUnauthorized(fn: () => void): void {
  unauthorized = fn;
}

/** A query string from the defined, non-empty values only: `{limit: 50, before: undefined}` -> "?limit=50". */
export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  }
  return parts.length ? `?${parts.join('&')}` : '';
}

/** A readable message from an error response: FastAPI's `detail` (string or list), `title`, or the status. */
export async function errorText(res: Response): Promise<string> {
  try {
    const body = await res.clone().json();
    if (typeof body?.detail === 'string') return body.detail;
    if (Array.isArray(body?.detail)) return body.detail.map((d: { msg?: string }) => d.msg ?? '').filter(Boolean).join('; ');
    if (typeof body?.title === 'string') return body.title;
    if (typeof body?.error === 'string') return body.error;
  } catch {
    /* not JSON */
  }
  return `${res.status} ${res.statusText}`.trim();
}

export interface RequestOptions {
  /** a 401 here doesn't trigger the sign-in screen (the login call itself) */
  quiet401?: boolean;
  signal?: AbortSignal;
}

export async function request(method: string, path: string, body?: unknown, opts: RequestOptions = {}): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  const res = await fetch(path, { method, headers, body: payload, credentials: 'same-origin', signal: opts.signal });
  if (res.status === 401) {
    if (!opts.quiet401) unauthorized();
    throw new HttpError(401, 'Sign in required');
  }
  if (!res.ok) throw new HttpError(res.status, await errorText(res));
  return res;
}

async function json<T>(method: string, path: string, body?: unknown, opts?: RequestOptions): Promise<T> {
  const res = await request(method, path, body, opts);
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const enc = encodeURIComponent;

export const THREAD_PAGE = 50;

export const api = {
  health: () => json<{ ok: boolean; version: string; mode: string }>('GET', '/api/health'),
  config: () => json<AppConfig>('GET', '/api/config'),
  login: (token: string) => request('POST', '/api/auth/login', { token }, { quiet401: true }),
  thread: (before?: number, limit = THREAD_PAGE) => json<ThreadSnapshot>('GET', `/api/thread${query({ limit, before })}`),
  send: (text: string, harness?: string) =>
    json<{ message: Message }>('POST', '/api/thread', harness ? { text, harness } : { text }),
  approvals: (state?: string) => json<Approval[]>('GET', `/api/approvals${query({ state })}`),
  decide: (id: string, body: DecisionBody) => json<Approval>('POST', `/api/approvals/${enc(id)}/decision`, body),
  tasks: (state?: string) => json<Task[]>('GET', `/api/tasks${query({ state })}`),
  task: (id: string) => json<Task & { logs: TaskLog[] }>('GET', `/api/tasks/${enc(id)}`),
  steer: (id: string, text: string) => json<Task>('POST', `/api/tasks/${enc(id)}/steer`, { text }),
  stop: (id: string) => json<Task>('POST', `/api/tasks/${enc(id)}/stop`),
  activity: (before?: number, limit = 100) => json<Activity[]>('GET', `/api/activity${query({ limit, before })}`),
  today: () => json<Today>('GET', '/api/today'),
  runBrief: () => json<Brief>('POST', '/api/brief/run'),
  runCheckin: () => json<{ new_events: number; notified: number; ignored: number }>('POST', '/api/checkin/run'),
  events: (status?: string, limit?: number) => json<TEvent[]>('GET', `/api/events${query({ status, limit })}`),
  artifacts: () => json<Artifact[]>('GET', '/api/artifacts'),
  memory: () => json<MemoryEntry[]>('GET', '/api/memory'),
  addMemory: (body: { kind: string; content: string; key?: string }) => json<MemoryEntry>('POST', '/api/memory', body),
  forget: (id: string) => json<{ ok: boolean }>('DELETE', `/api/memory/${enc(id)}`),
  skills: () => json<Skill[]>('GET', '/api/skills'),
  audit: (limit = 50) => json<AuditRow[]>('GET', `/api/audit${query({ limit })}`),
  outbox: () => json<unknown>('GET', '/api/outbox'),
  vapid: () => json<VapidInfo>('GET', '/api/push/vapid'),
  pushSubscriptions: () => json<PushSubscriptionInfo[]>('GET', '/api/push/subscriptions'),
  addPushSubscription: (sub: PushSubscriptionJSON) => json<PushSubscriptionInfo>('POST', '/api/push/subscriptions', sub),
  removePushSubscription: (by: { endpoint: string } | { id: string }) =>
    json<{ ok: boolean }>('DELETE', '/api/push/subscriptions', by),
  testPush: () => json<{ delivered: number; subscriptions: number }>('POST', '/api/push/test'),
  notifySettings: () => json<NotifySettings>('GET', '/api/settings/notify'),
  saveNotifySettings: (changes: Partial<NotifySettings>) => json<NotifySettings>('PUT', '/api/settings/notify', changes),
  transcribe: (audio: Blob, filename: string) => {
    const form = new FormData();
    form.append('file', audio, filename);
    return json<{ text: string }>('POST', '/api/voice/transcribe', form);
  },
};

export const artifactUrls = (id: string) => ({
  preview: `/api/artifacts/${enc(id)}/preview`,
  download: `/api/artifacts/${enc(id)}/download`,
});
