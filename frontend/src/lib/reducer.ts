// The app's data, and a pure reducer that applies the server's events to it. Every function here returns a new
// state and leaves its input untouched, so the store can hold it as one raw value.

import type {
  Activity, Approval, Artifact, Brief, Message, StreamEvent, TEvent, Task, TaskLog, ThreadSnapshot, Today,
} from './types';

export const MAX_ACTIVITY = 500;
export const MAX_LOGS_PER_TASK = 500;

export interface State {
  /** thread messages, ascending by id, no duplicates */
  messages: Message[];
  approvals: Record<string, Approval>;
  tasks: Record<string, Task>;
  artifacts: Record<string, Artifact>;
  briefs: Record<string, Brief>;
  events: Record<string, TEvent>;
  /** newest first, no duplicates */
  activity: Activity[];
  /** per task, ascending by id */
  logs: Record<string, TaskLog[]>;
  today: Today | null;
  typing: boolean;
  /** older messages may exist on the server */
  hasMore: boolean;
  serverVersion: string | null;
}

export function emptyState(): State {
  return {
    messages: [],
    approvals: {},
    tasks: {},
    artifacts: {},
    briefs: {},
    events: {},
    activity: [],
    logs: {},
    today: null,
    typing: false,
    hasMore: false,
    serverVersion: null,
  };
}

/** Insert or replace by id, keeping ascending order. */
export function upsertMessages(list: Message[], incoming: Message[]): Message[] {
  if (!incoming.length) return list;
  const byId = new Map<number, Message>();
  for (const m of list) byId.set(m.id, m);
  for (const m of incoming) byId.set(m.id, m);
  return [...byId.values()].sort((a, b) => a.id - b.id);
}

function upsertActivity(list: Activity[], incoming: Activity[]): Activity[] {
  const byId = new Map<number, Activity>();
  for (const a of list) byId.set(a.id, a);
  for (const a of incoming) byId.set(a.id, a);
  return [...byId.values()].sort((a, b) => b.id - a.id).slice(0, MAX_ACTIVITY);
}

function upsertLogs(list: TaskLog[] | undefined, incoming: TaskLog[]): TaskLog[] {
  const byId = new Map<number, TaskLog>();
  for (const l of list ?? []) byId.set(l.id, l);
  for (const l of incoming) byId.set(l.id, l);
  const out = [...byId.values()].sort((a, b) => a.id - b.id);
  return out.length > MAX_LOGS_PER_TASK ? out.slice(out.length - MAX_LOGS_PER_TASK) : out;
}

const ACTIVE: ReadonlySet<string> = new Set(['queued', 'running', 'waiting']);
export const isActive = (t: Task) => ACTIVE.has(t.state);

/** Is this timestamp on the given local date (YYYY-MM-DD)? */
function onDate(ts: string | null, date: string): boolean {
  if (!ts) return false;
  const d = new Date(ts.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(ts) ? ts : ts + 'Z');
  if (Number.isNaN(d.getTime())) return false;
  const local = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  return local === date;
}

function replaceIn<T extends { id: string }>(list: T[], item: T, keep: boolean, prepend = false): T[] {
  const i = list.findIndex((x) => x.id === item.id);
  if (!keep) return i < 0 ? list : list.filter((x) => x.id !== item.id);
  if (i < 0) return prepend ? [item, ...list] : [...list, item];
  const out = list.slice();
  out[i] = item;
  return out;
}

/** Keep Today's lists in step with approvals and tasks as they change. */
function todayWithApproval(today: Today | null, a: Approval): Today | null {
  if (!today) return today;
  return { ...today, needs_you: replaceIn(today.needs_you, a, a.state === 'pending') };
}

function todayWithTask(today: Today | null, t: Task): Today | null {
  if (!today) return today;
  const wasDone = today.done.some((x) => x.id === t.id);
  const done = replaceIn(today.done, t, t.state === 'done' && (wasDone || onDate(t.finished_at, today.date) ||
    !t.finished_at), true);
  const stats = !wasDone && done.length > today.done.length
    ? { ...today.stats, tasks_done: today.stats.tasks_done + 1 }
    : today.stats;
  return { ...today, in_progress: replaceIn(today.in_progress, t, isActive(t)), done, stats };
}

/** Apply one stream event. Events for entities already known replace them; nothing is duplicated. */
export function applyEvent(s: State, ev: StreamEvent): State {
  switch (ev.type) {
    case 'hello':
      return { ...s, serverVersion: ev.data.version ?? null };
    case 'message.created': {
      const m = ev.data;
      // the agent's reply ends its typing indicator
      return { ...s, messages: upsertMessages(s.messages, [m]), typing: m.role === 'agent' ? false : s.typing };
    }
    case 'approval.created':
    case 'approval.updated': {
      const a = ev.data;
      return { ...s, approvals: { ...s.approvals, [a.id]: a }, today: todayWithApproval(s.today, a) };
    }
    case 'task.created':
    case 'task.updated': {
      const t = { ...ev.data, artifact_ids: ev.data.artifact_ids ?? s.tasks[ev.data.id]?.artifact_ids ?? [] };
      return { ...s, tasks: { ...s.tasks, [t.id]: t }, today: todayWithTask(s.today, t) };
    }
    case 'task.log': {
      const l = ev.data;
      return { ...s, logs: { ...s.logs, [l.task_id]: upsertLogs(s.logs[l.task_id], [l]) } };
    }
    case 'activity.created':
      return { ...s, activity: upsertActivity(s.activity, [ev.data]) };
    case 'artifact.created': {
      const a = ev.data;
      let tasks = s.tasks;
      const t = a.task_id ? s.tasks[a.task_id] : undefined;
      if (t && !t.artifact_ids.includes(a.id)) tasks = { ...tasks, [t.id]: { ...t, artifact_ids: [...t.artifact_ids, a.id] } };
      return { ...s, artifacts: { ...s.artifacts, [a.id]: a }, tasks };
    }
    case 'brief.created': {
      const b = ev.data;
      const today = s.today && (b.date === s.today.date || !s.today.brief) ? { ...s.today, brief: b } : s.today;
      return { ...s, briefs: { ...s.briefs, [b.id]: b }, today };
    }
    case 'event.created': {
      const e = ev.data;
      let today = s.today;
      if (today && e.status === 'notified') today = { ...today, notifications: replaceIn(today.notifications, e, true, true) };
      return { ...s, events: { ...s.events, [e.id]: e }, today };
    }
    case 'typing':
      return s.typing === ev.data.on ? s : { ...s, typing: ev.data.on };
    case 'notify':
      return s; // a side effect only (toast, system notification); the message itself arrives as message.created
    default:
      return s;
  }
}

export function applyEvents(s: State, events: StreamEvent[]): State {
  return events.reduce(applyEvent, s);
}

/**
 * Merge a GET /api/thread page. `replace` (the newest page, on boot or reconnect) keeps messages newer than the
 * page that arrived over the stream meanwhile; an older page (`before=`) is merged in front.
 */
export function applySnapshot(s: State, snap: ThreadSnapshot, opts: { replace: boolean; limit: number }): State {
  const incoming = snap.messages ?? [];
  let messages: Message[];
  if (opts.replace) {
    const newest = incoming.length ? incoming[incoming.length - 1].id : -Infinity;
    messages = upsertMessages(incoming.slice().sort((a, b) => a.id - b.id), s.messages.filter((m) => m.id > newest));
  } else {
    messages = upsertMessages(s.messages, incoming);
  }
  const tasks: Record<string, Task> = { ...s.tasks };
  for (const [id, t] of Object.entries(snap.tasks ?? {})) tasks[id] = { ...t, artifact_ids: t.artifact_ids ?? [] };
  const hasMore = opts.replace || s.messages.length === 0 ? incoming.length >= opts.limit : (incoming.length >= opts.limit && s.hasMore);
  return {
    ...s,
    messages,
    approvals: { ...s.approvals, ...(snap.approvals ?? {}) },
    tasks,
    artifacts: { ...s.artifacts, ...(snap.artifacts ?? {}) },
    briefs: { ...s.briefs, ...(snap.briefs ?? {}) },
    hasMore,
  };
}

/** Store GET /api/today, and file the entities it carries. */
export function applyToday(s: State, today: Today): State {
  const approvals = { ...s.approvals };
  for (const a of today.needs_you ?? []) approvals[a.id] = a;
  const tasks = { ...s.tasks };
  for (const t of [...(today.in_progress ?? []), ...(today.done ?? [])]) tasks[t.id] = { ...t, artifact_ids: t.artifact_ids ?? [] };
  const briefs = today.brief ? { ...s.briefs, [today.brief.id]: today.brief } : s.briefs;
  const norm: Today = {
    date: today.date,
    brief: today.brief ?? null,
    needs_you: today.needs_you ?? [],
    in_progress: today.in_progress ?? [],
    done: today.done ?? [],
    upcoming: today.upcoming ?? [],
    notifications: today.notifications ?? [],
    stats: { ...{ events_today: 0, ignored: 0, notified: 0, tasks_done: 0 }, ...(today.stats ?? {}) },
  };
  return { ...s, approvals, tasks, briefs, today: norm };
}

export function applyActivity(s: State, items: Activity[]): State {
  return { ...s, activity: upsertActivity(s.activity, items) };
}

export function applyTaskDetail(s: State, t: Task & { logs?: TaskLog[] }): State {
  const { logs, ...task } = t;
  const next = { ...task, artifact_ids: task.artifact_ids ?? [] } as Task;
  return {
    ...s,
    tasks: { ...s.tasks, [next.id]: next },
    logs: logs ? { ...s.logs, [next.id]: upsertLogs(s.logs[next.id], logs) } : s.logs,
    today: todayWithTask(s.today, next),
  };
}

export function applyTask(s: State, t: Task): State {
  return applyTaskDetail(s, t);
}

export function applyApproval(s: State, a: Approval): State {
  return applyEvent(s, { type: 'approval.updated', data: a });
}

export function applyArtifacts(s: State, list: Artifact[]): State {
  const artifacts = { ...s.artifacts };
  for (const a of list) artifacts[a.id] = a;
  return { ...s, artifacts };
}

/** The last log line of a task, for its card. */
export function lastLog(s: State, taskId: string): TaskLog | null {
  const l = s.logs[taskId];
  return l && l.length ? l[l.length - 1] : null;
}

export function sortedArtifacts(s: State): Artifact[] {
  return Object.values(s.artifacts).sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id));
}

export function activeTasks(s: State): Task[] {
  return Object.values(s.tasks).filter(isActive).sort((a, b) => a.created_at.localeCompare(b.created_at));
}

export function pendingApprovals(s: State): Approval[] {
  return Object.values(s.approvals).filter((a) => a.state === 'pending')
    .sort((a, b) => a.expires_at.localeCompare(b.expires_at));
}
