// App state (Svelte 5 runes): the reduced data, the route, the stream and the side effects of events.

import { api, HttpError, onUnauthorized, THREAD_PAGE } from './api';
import { showSystemNotification } from './notify';
import {
  applyActivity, applyApproval, applyArtifacts, applyEvent, applySnapshot, applyTask, applyTaskDetail, applyToday,
  emptyState, type State,
} from './reducer';
import { connectStream } from './stream';
import type { Approval, AppConfig, Artifact, NotifyPayload, StreamEvent, Task } from './types';
import type { DecisionBody } from './approval';

export type Route = 'thread' | 'today' | 'activity' | 'library' | 'settings';
export const ROUTES: Route[] = ['thread', 'today', 'activity', 'library', 'settings'];

export interface Toast {
  id: number;
  title: string;
  body: string;
  urgency: string;
  kind: 'notify' | 'error' | 'info';
}

export function errorMessage(err: unknown): string {
  if (err instanceof HttpError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

function routeFromHash(): Route {
  const h = typeof location !== 'undefined' ? location.hash.replace(/^#\/?/, '') : '';
  return (ROUTES as string[]).includes(h) ? (h as Route) : 'thread';
}

class AppStore {
  data = $state.raw<State>(emptyState());
  config = $state<AppConfig | null>(null);
  route = $state<Route>(routeFromHash());
  needsAuth = $state(false);
  booted = $state(false);
  bootError = $state<string | null>(null);
  connected = $state(false);
  toasts = $state<Toast[]>([]);
  /** ticks every second, for countdowns and running durations */
  now = $state(Date.now());
  loadingOlder = $state(false);
  /** the artifact open in the preview dialog */
  preview = $state<Artifact | null>(null);

  private stopStream: (() => void) | null = null;
  private toastSeq = 0;
  private clock: ReturnType<typeof setInterval> | undefined;

  constructor() {
    onUnauthorized(() => {
      this.needsAuth = true;
      this.stopStream?.();
      this.stopStream = null;
    });
  }

  // ---- lifecycle ---------------------------------------------------------------------------------

  start(): void {
    if (typeof window === 'undefined') return;
    window.addEventListener('hashchange', () => {
      this.route = routeFromHash();
      this.preview = null;
    });
    this.clock ??= setInterval(() => (this.now = Date.now()), 1000);
    void this.boot();
  }

  async boot(): Promise<void> {
    this.bootError = null;
    try {
      await Promise.all([this.loadThread(), this.loadToday(), this.loadConfig()]);
      if (this.needsAuth) return;
      this.booted = true;
      this.openStream();
    } catch (e) {
      if (!this.needsAuth) this.bootError = errorMessage(e);
    }
  }

  async signIn(token: string): Promise<string | null> {
    try {
      await api.login(token.trim());
    } catch (e) {
      return e instanceof HttpError && (e.status === 401 || e.status === 403)
        ? 'That token was not accepted.'
        : errorMessage(e);
    }
    this.needsAuth = false;
    await this.boot();
    return null;
  }

  private openStream(): void {
    this.stopStream?.();
    this.stopStream = connectStream('/api/stream', {
      onEvent: (ev) => this.dispatch(ev),
      onStatus: (c) => (this.connected = c),
      onReconnect: () => void this.refresh(),
      onClosed: async () => {
        // find out why: a 401 here flips needsAuth and we stop retrying
        try {
          await api.health();
          await this.loadThread();
        } catch {
          /* still down; retry later */
        }
        return !this.needsAuth;
      },
    });
  }

  /** After a reconnect: the stream may have missed events. */
  async refresh(): Promise<void> {
    await Promise.allSettled([this.loadThread(), this.loadToday()]);
  }

  navigate(r: Route): void {
    if (location.hash !== `#/${r}`) location.hash = `#/${r}`;
    this.route = r;
  }

  // ---- events ------------------------------------------------------------------------------------

  dispatch(ev: StreamEvent): void {
    this.data = applyEvent(this.data, ev);
    if (ev.type === 'notify') this.onNotify(ev.data);
  }

  private onNotify(n: NotifyPayload): void {
    this.toast({ title: n.title, body: n.body, urgency: n.urgency, kind: 'notify' });
    if (typeof document !== 'undefined' && document.hidden) void showSystemNotification(n);
  }

  toast(t: Omit<Toast, 'id'>, ms = t.kind === 'error' ? 8000 : 6000): void {
    const id = ++this.toastSeq;
    this.toasts = [...this.toasts.slice(-3), { ...t, id }];
    setTimeout(() => this.dismiss(id), ms);
  }

  dismiss(id: number): void {
    this.toasts = this.toasts.filter((t) => t.id !== id);
  }

  fail(e: unknown, what: string): void {
    if (e instanceof HttpError && e.status === 401) return;
    this.toast({ title: what, body: errorMessage(e), urgency: 'normal', kind: 'error' });
  }

  // ---- loads -------------------------------------------------------------------------------------

  async loadThread(): Promise<void> {
    const snap = await api.thread();
    this.data = applySnapshot(this.data, snap, { replace: true, limit: THREAD_PAGE });
  }

  async loadOlder(): Promise<void> {
    const first = this.data.messages[0];
    if (!first || this.loadingOlder) return;
    this.loadingOlder = true;
    try {
      const snap = await api.thread(first.id);
      this.data = applySnapshot(this.data, snap, { replace: false, limit: THREAD_PAGE });
    } catch (e) {
      this.fail(e, 'Could not load earlier messages');
    } finally {
      this.loadingOlder = false;
    }
  }

  async loadToday(): Promise<void> {
    const today = await api.today();
    this.data = applyToday(this.data, today);
  }

  async loadConfig(): Promise<void> {
    this.config = await api.config();
  }

  async loadActivity(before?: number): Promise<number> {
    const items = await api.activity(before);
    this.data = applyActivity(this.data, items);
    return items.length;
  }

  async loadTasks(): Promise<void> {
    const tasks = await api.tasks();
    let d = this.data;
    for (const t of tasks) d = applyTask(d, t);
    this.data = d;
  }

  async loadTask(id: string): Promise<void> {
    const t = await api.task(id);
    this.data = applyTaskDetail(this.data, t);
  }

  async loadArtifacts(): Promise<void> {
    this.data = applyArtifacts(this.data, await api.artifacts());
  }

  // ---- actions -----------------------------------------------------------------------------------

  async send(text: string, harness?: string): Promise<boolean> {
    try {
      const { message } = await api.send(text, harness);
      if (message) this.data = applyEvent(this.data, { type: 'message.created', data: message });
      return true;
    } catch (e) {
      this.fail(e, 'Message not sent');
      return false;
    }
  }

  async decide(a: Approval, body: DecisionBody): Promise<boolean> {
    try {
      this.data = applyApproval(this.data, await api.decide(a.id, body));
      return true;
    } catch (e) {
      this.fail(e, 'Decision not recorded');
      return false;
    }
  }

  async steer(t: Task, text: string): Promise<boolean> {
    try {
      this.data = applyTask(this.data, await api.steer(t.id, text));
      return true;
    } catch (e) {
      this.fail(e, 'Could not steer the task');
      return false;
    }
  }

  async stop(t: Task): Promise<void> {
    try {
      this.data = applyTask(this.data, await api.stop(t.id));
    } catch (e) {
      this.fail(e, 'Could not stop the task');
    }
  }

  async runBrief(): Promise<void> {
    try {
      const b = await api.runBrief();
      this.data = applyEvent(this.data, { type: 'brief.created', data: b });
      await this.loadToday();
      this.toast({ title: 'Brief ready', body: b.greeting, urgency: 'normal', kind: 'info' });
    } catch (e) {
      this.fail(e, 'Brief failed');
    }
  }

  async runCheckin(): Promise<void> {
    try {
      const r = await api.runCheckin();
      await this.loadToday();
      this.toast({
        title: 'Check-in done',
        body: `${r.new_events} new · ${r.notified} notified · ${r.ignored} ignored`,
        urgency: 'normal',
        kind: 'info',
      });
    } catch (e) {
      this.fail(e, 'Check-in failed');
    }
  }
}

export const app = new AppStore();
