import { describe, expect, it } from 'vitest';
import * as f from './fixtures';
import {
  activeTasks, applyEvent, applyEvents, applySnapshot, applyTaskDetail, applyToday, emptyState, lastLog,
  MAX_ACTIVITY, pendingApprovals, sortedArtifacts,
} from './reducer';

const withToday = () => applyToday(emptyState(), f.today());

describe('reducer: messages', () => {
  it('appends messages in id order and dedupes', () => {
    let s = emptyState();
    s = applyEvents(s, [
      { type: 'message.created', data: f.message(3) },
      { type: 'message.created', data: f.message(1) },
      { type: 'message.created', data: f.message(3, { text: 'edited' }) },
    ]);
    expect(s.messages.map((m) => m.id)).toEqual([1, 3]);
    expect(s.messages[1].text).toBe('edited');
  });

  it('an agent message clears typing; a user message does not', () => {
    let s = applyEvent(emptyState(), { type: 'typing', data: { on: true } });
    expect(s.typing).toBe(true);
    s = applyEvent(s, { type: 'message.created', data: f.message(1, { role: 'user' }) });
    expect(s.typing).toBe(true);
    s = applyEvent(s, { type: 'message.created', data: f.message(2, { role: 'agent' }) });
    expect(s.typing).toBe(false);
  });

  it('does not mutate the input state', () => {
    const s = emptyState();
    const frozen = Object.freeze({ ...s, messages: Object.freeze([f.message(1)]) as never });
    const next = applyEvent(frozen, { type: 'message.created', data: f.message(2) });
    expect(frozen.messages).toHaveLength(1);
    expect(next.messages).toHaveLength(2);
  });

  it('typing returns the same state when unchanged', () => {
    const s = emptyState();
    expect(applyEvent(s, { type: 'typing', data: { on: false } })).toBe(s);
  });
});

describe('reducer: approvals', () => {
  it('created adds to Needs you; updated to approved removes it', () => {
    let s = withToday();
    s = applyEvent(s, { type: 'approval.created', data: f.approval() });
    expect(s.approvals.ap_1.state).toBe('pending');
    expect(s.today!.needs_you.map((a) => a.id)).toEqual(['ap_1']);
    s = applyEvent(s, { type: 'approval.updated', data: f.approval({ state: 'approved' }) });
    expect(s.approvals.ap_1.state).toBe('approved');
    expect(s.today!.needs_you).toEqual([]);
  });

  it('pendingApprovals sorts by expiry', () => {
    let s = emptyState();
    s = applyEvent(s, { type: 'approval.created', data: f.approval({ id: 'ap_2', expires_at: '2026-10-21T13:30:00Z' }) });
    s = applyEvent(s, { type: 'approval.created', data: f.approval({ id: 'ap_1', expires_at: '2026-10-21T13:10:00Z' }) });
    s = applyEvent(s, { type: 'approval.created', data: f.approval({ id: 'ap_3', state: 'expired' }) });
    expect(pendingApprovals(s).map((a) => a.id)).toEqual(['ap_1', 'ap_2']);
  });
});

describe('reducer: tasks', () => {
  it('moves a task from In progress to Done today and counts it once', () => {
    let s = withToday();
    s = applyEvent(s, { type: 'task.created', data: f.task({ state: 'queued' }) });
    expect(s.today!.in_progress.map((t) => t.id)).toEqual(['tk_1']);
    const done = f.task({ state: 'done', finished_at: '2026-10-21T15:00:00Z' }); // the fixture's Today date
    s = applyEvent(s, { type: 'task.updated', data: { ...done } });
    s = applyEvent(s, { type: 'task.updated', data: { ...done, summary: 'ok' } });
    expect(s.today!.in_progress).toEqual([]);
    expect(s.today!.done.map((t) => t.id)).toEqual(['tk_1']);
    expect(s.today!.stats.tasks_done).toBe(1);
  });

  it('keeps known artifact ids when an update omits them', () => {
    let s = applyEvent(emptyState(), { type: 'task.created', data: f.task({ artifact_ids: ['ar_1'] }) });
    const { artifact_ids: _omit, ...rest } = f.task({ steps: 5 });
    s = applyEvent(s, { type: 'task.updated', data: rest as never });
    expect(s.tasks.tk_1.artifact_ids).toEqual(['ar_1']);
    expect(s.tasks.tk_1.steps).toBe(5);
  });

  it('activeTasks lists queued, running and waiting only', () => {
    let s = emptyState();
    for (const [id, state] of [['a', 'running'], ['b', 'done'], ['c', 'waiting'], ['d', 'failed']] as const) {
      s = applyEvent(s, { type: 'task.created', data: f.task({ id, state }) });
    }
    expect(activeTasks(s).map((t) => t.id).sort()).toEqual(['a', 'c']);
  });

  it('task.log appends per task in order, deduped', () => {
    let s = emptyState();
    s = applyEvents(s, [
      { type: 'task.log', data: f.log(2) },
      { type: 'task.log', data: f.log(1) },
      { type: 'task.log', data: f.log(2) },
      { type: 'task.log', data: f.log(9, { task_id: 'tk_2' }) },
    ]);
    expect(s.logs.tk_1.map((l) => l.id)).toEqual([1, 2]);
    expect(lastLog(s, 'tk_1')!.text).toBe('step 2');
    expect(lastLog(s, 'tk_2')!.id).toBe(9);
    expect(lastLog(s, 'nope')).toBeNull();
  });

  it('task detail merges logs with streamed ones', () => {
    let s = applyEvent(emptyState(), { type: 'task.log', data: f.log(3) });
    s = applyTaskDetail(s, { ...f.task(), logs: [f.log(1), f.log(2)] });
    expect(s.logs.tk_1.map((l) => l.id)).toEqual([1, 2, 3]);
    expect(s.tasks.tk_1.title).toBe('Cash summary');
  });
});

describe('reducer: activity, artifacts, briefs, events', () => {
  it('activity is newest first, deduped and capped', () => {
    let s = emptyState();
    s = applyEvent(s, { type: 'activity.created', data: f.activity(1) });
    s = applyEvent(s, { type: 'activity.created', data: f.activity(3) });
    s = applyEvent(s, { type: 'activity.created', data: f.activity(1) });
    expect(s.activity.map((a) => a.id)).toEqual([3, 1]);
    for (let i = 10; i < MAX_ACTIVITY + 20; i++) s = applyEvent(s, { type: 'activity.created', data: f.activity(i) });
    expect(s.activity).toHaveLength(MAX_ACTIVITY);
    expect(s.activity[0].id).toBe(MAX_ACTIVITY + 19);
  });

  it('artifact.created files the artifact and links it to its task', () => {
    let s = applyEvent(emptyState(), { type: 'task.created', data: f.task() });
    s = applyEvent(s, { type: 'artifact.created', data: f.artifact() });
    s = applyEvent(s, { type: 'artifact.created', data: f.artifact() });
    expect(s.tasks.tk_1.artifact_ids).toEqual(['ar_1']);
    s = applyEvent(s, { type: 'artifact.created', data: f.artifact({ id: 'ar_2', created_at: '2026-10-21T14:00:00Z' }) });
    expect(sortedArtifacts(s).map((a) => a.id)).toEqual(['ar_2', 'ar_1']);
  });

  it('brief.created becomes the brief on Today for the same date', () => {
    let s = withToday();
    s = applyEvent(s, { type: 'brief.created', data: f.brief() });
    expect(s.today!.brief!.id).toBe('br_1');
    s = applyEvent(s, { type: 'brief.created', data: f.brief({ id: 'br_old', date: '2026-10-20' }) });
    expect(s.today!.brief!.id).toBe('br_1');
    expect(s.briefs.br_old).toBeDefined();
  });

  it('event.created adds notified events to Today notifications', () => {
    let s = withToday();
    s = applyEvent(s, { type: 'event.created', data: f.event({ id: 'ev_a', status: 'ignored' }) });
    s = applyEvent(s, { type: 'event.created', data: f.event({ id: 'ev_b' }) });
    expect(Object.keys(s.events).sort()).toEqual(['ev_a', 'ev_b']);
    expect(s.today!.notifications.map((e) => e.id)).toEqual(['ev_b']);
  });

  it('hello records the server version; notify changes nothing', () => {
    let s = applyEvent(emptyState(), { type: 'hello', data: { server_time: f.T0, version: '0.1.0' } });
    expect(s.serverVersion).toBe('0.1.0');
    const same = applyEvent(s, { type: 'notify', data: { title: 't', body: 'b', urgency: 'high', message_id: 1 } });
    expect(same).toBe(s);
  });
});

describe('reducer: snapshots', () => {
  it('replace keeps messages that streamed in after the page', () => {
    let s = applyEvent(emptyState(), { type: 'message.created', data: f.message(5) });
    s = applySnapshot(s, f.snapshot({ messages: [f.message(3), f.message(4)] }), { replace: true, limit: 50 });
    expect(s.messages.map((m) => m.id)).toEqual([3, 4, 5]);
  });

  it('replace drops stale messages older than the newest page', () => {
    let s = applySnapshot(emptyState(), f.snapshot({ messages: [f.message(1), f.message(2)] }), { replace: true, limit: 50 });
    s = applySnapshot(s, f.snapshot({ messages: [f.message(2, { text: 'new' }), f.message(3)] }), { replace: true, limit: 50 });
    expect(s.messages.map((m) => m.id)).toEqual([2, 3]);
    expect(s.messages[0].text).toBe('new');
  });

  it('an older page merges in front and sets hasMore by page size', () => {
    let s = applySnapshot(emptyState(), f.snapshot({ messages: [f.message(3), f.message(4)] }), { replace: true, limit: 2 });
    expect(s.hasMore).toBe(true);
    s = applySnapshot(s, f.snapshot({ messages: [f.message(2)], approvals: { ap_1: f.approval() } }), { replace: false, limit: 2 });
    expect(s.messages.map((m) => m.id)).toEqual([2, 3, 4]);
    expect(s.hasMore).toBe(false);
    expect(s.approvals.ap_1).toBeDefined();
  });

  it('today files its approvals, tasks and brief', () => {
    const s = applyToday(emptyState(), f.today({ needs_you: [f.approval()], in_progress: [f.task()], brief: f.brief() }));
    expect(s.approvals.ap_1).toBeDefined();
    expect(s.tasks.tk_1.artifact_ids).toEqual([]);
    expect(s.briefs.br_1).toBeDefined();
    expect(s.today!.stats.events_today).toBe(5);
  });
});
