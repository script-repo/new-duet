import { afterEach, describe, expect, it, vi } from 'vitest';
import { connectStream } from './stream';
import type { StreamEvent } from './types';

class FakeES {
  static CLOSED = 2;
  static last: FakeES | null = null;
  static count = 0;
  readyState = 1;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, (e: { data: string }) => void>();
  closed = false;
  constructor(public url: string) {
    FakeES.last = this;
    FakeES.count++;
  }
  addEventListener(type: string, fn: (e: { data: string }) => void) {
    this.listeners.set(type, fn);
  }
  close() {
    this.closed = true;
  }
  fire(type: string, data: unknown) {
    this.listeners.get(type)?.({ data: typeof data === 'string' ? data : JSON.stringify(data) });
  }
}

function handlers(retry = true) {
  const events: StreamEvent[] = [];
  const h = {
    events,
    status: [] as boolean[],
    reconnects: 0,
    onEvent: (e: StreamEvent) => events.push(e),
    onStatus: (c: boolean) => h.status.push(c),
    onReconnect: () => h.reconnects++,
    onClosed: vi.fn(async () => retry),
  };
  return h;
}

describe('connectStream', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('dispatches decoded events and ignores malformed ones', () => {
    vi.stubGlobal('EventSource', FakeES);
    const h = handlers();
    const stop = connectStream('/api/stream', h);
    const es = FakeES.last!;
    es.onopen!();
    es.fire('typing', { on: true });
    es.fire('message.created', '{not json');
    expect(h.events).toEqual([{ type: 'typing', data: { on: true } }]);
    expect(h.status).toEqual([true]);
    expect(h.reconnects).toBe(0);
    stop();
    expect(es.closed).toBe(true);
  });

  it('refetches after the browser reconnects by itself', () => {
    vi.stubGlobal('EventSource', FakeES);
    const h = handlers();
    connectStream('/api/stream', h);
    const es = FakeES.last!;
    es.onopen!();
    es.onerror!(); // readyState still CONNECTING: the browser retries
    es.onopen!();
    expect(h.status).toEqual([true, false, true]);
    expect(h.reconnects).toBe(1);
    expect(h.onClosed).not.toHaveBeenCalled();
  });

  it('when the stream is closed for good, asks before reopening with backoff', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('EventSource', FakeES);
    const h = handlers(true);
    const before = FakeES.count;
    connectStream('/api/stream', h);
    const es = FakeES.last!;
    es.readyState = FakeES.CLOSED;
    es.onerror!();
    await vi.advanceTimersByTimeAsync(0);
    expect(h.onClosed).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeES.count).toBe(before + 2);
    FakeES.last!.onopen!();
    expect(h.reconnects).toBe(1);
  });

  it('stops retrying when told to (sign-in needed)', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('EventSource', FakeES);
    const h = handlers(false);
    const before = FakeES.count;
    connectStream('/api/stream', h);
    FakeES.last!.readyState = FakeES.CLOSED;
    FakeES.last!.onerror!();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeES.count).toBe(before + 1);
  });
});
