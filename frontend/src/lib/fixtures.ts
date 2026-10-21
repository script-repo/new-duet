// Test fixtures shaped like SPEC §10.2. Used only by tests.

import type { Activity, Approval, Artifact, Brief, Message, TEvent, Task, TaskLog, ThreadSnapshot, Today } from './types';

export const T0 = '2026-10-21T13:00:00Z';

export function message(id: number, over: Partial<Message> = {}): Message {
  return { id, role: 'agent', text: `message ${id}`, card: null, created_at: T0, ...over };
}

export function approval(over: Partial<Approval> = {}): Approval {
  return {
    id: 'ap_1',
    tool: 'email_send',
    title: 'Send email to Sam',
    summary: 'Cash summary for this week',
    reason: 'Sends a message as you',
    args: { to: 'sam@harbor.example', subject: 'Cash summary', body: 'Hi Sam,\n\nAttached.\n\nThanks,\nD', cc: [], urgent: false, amount: 12 },
    editable: ['subject', 'body'],
    tags: ['send'],
    state: 'pending',
    task_id: 'tk_1',
    created_at: T0,
    expires_at: '2026-10-21T13:15:00Z',
    decided_at: null,
    note: null,
    result: null,
    ...over,
  };
}

export function task(over: Partial<Task> = {}): Task {
  return {
    id: 'tk_1',
    title: 'Cash summary',
    instructions: 'Build me a one-page cash summary',
    harness: 'builtin',
    origin: 'chat',
    state: 'running',
    summary: null,
    error: null,
    steps: 2,
    created_at: T0,
    started_at: T0,
    finished_at: null,
    artifact_ids: [],
    ...over,
  };
}

export function log(id: number, over: Partial<TaskLog> = {}): TaskLog {
  return { id, task_id: 'tk_1', ts: T0, kind: 'info', text: `step ${id}`, ...over };
}

export function activity(id: number, over: Partial<Activity> = {}): Activity {
  return { id, ts: T0, kind: 'checkin', title: `activity ${id}`, detail: null, task_id: null, level: 'info', ...over };
}

export function artifact(over: Partial<Artifact> = {}): Artifact {
  return { id: 'ar_1', task_id: 'tk_1', kind: 'xlsx', title: 'Cash summary', filename: 'cash.xlsx', size: 5120, created_at: T0, ...over };
}

export function brief(over: Partial<Brief> = {}): Brief {
  return {
    id: 'br_1',
    date: '2026-10-21',
    greeting: 'Morning. 4 things in progress and 2 that need you.',
    items: [
      { topic: 'Payment failed', summary: 'Renewal card declined; service suspends at noon.', needs_you: true, approval_id: 'ap_1' },
      { topic: 'Newsletter', summary: 'Nothing important.', needs_you: false },
    ],
    created_at: T0,
    ...over,
  };
}

export function event(over: Partial<TEvent> = {}): TEvent {
  return {
    id: 'ev_1', source: 'email', kind: 'email', title: 'Payment failed', sender: 'billing@vendor.example',
    occurred_at: T0, status: 'notified', urgency: 'high', triage: { reason: 'money + deadline' }, ...over,
  };
}

export function snapshot(over: Partial<ThreadSnapshot> = {}): ThreadSnapshot {
  return { messages: [message(1), message(2)], approvals: {}, tasks: {}, artifacts: {}, briefs: {}, ...over };
}

export function today(over: Partial<Today> = {}): Today {
  return {
    date: '2026-10-21',
    brief: null,
    needs_you: [],
    in_progress: [],
    done: [],
    upcoming: [{ title: 'Standup', start: '2026-10-21T14:00:00Z' }],
    notifications: [],
    stats: { events_today: 5, ignored: 3, notified: 1, tasks_done: 0 },
    ...over,
  };
}
