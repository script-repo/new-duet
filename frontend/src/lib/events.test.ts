import { describe, expect, it } from 'vitest';
import { decodeStreamEvent, STREAM_EVENT_TYPES } from './events';
import { backoff } from './stream';
import { errorText, query } from './api';
import * as f from './fixtures';

describe('decodeStreamEvent', () => {
  it('decodes every SSE event type in the spec', () => {
    expect(STREAM_EVENT_TYPES).toHaveLength(14);
    const ev = decodeStreamEvent('message.created', JSON.stringify(f.message(7)));
    expect(ev).toEqual({ type: 'message.created', data: f.message(7) });
    expect(decodeStreamEvent('task.log', JSON.stringify(f.log(1)))?.type).toBe('task.log');
    expect(decodeStreamEvent('notify', '{"title":"t","body":"b","urgency":"high","message_id":3}')?.type).toBe('notify');
  });

  it('rejects unknown types, bad JSON and non-objects', () => {
    expect(decodeStreamEvent('nope', '{}')).toBeNull();
    expect(decodeStreamEvent('message.created', '{oops')).toBeNull();
    expect(decodeStreamEvent('message.created', '[1,2]')).toBeNull();
    expect(decodeStreamEvent('message.created', 'null')).toBeNull();
  });

  it('rejects entity events without their key', () => {
    expect(decodeStreamEvent('approval.updated', '{"state":"approved"}')).toBeNull();
    expect(decodeStreamEvent('task.log', '{"id":1,"text":"x"}')).toBeNull();
  });

  it('normalizes typing to a boolean', () => {
    expect(decodeStreamEvent('typing', '{"on":1}')).toEqual({ type: 'typing', data: { on: true } });
    expect(decodeStreamEvent('typing', '{}')).toEqual({ type: 'typing', data: { on: false } });
  });
});

describe('api helpers', () => {
  it('query skips empty values and encodes', () => {
    expect(query({ limit: 50, before: undefined, state: '' })).toBe('?limit=50');
    expect(query({ q: 'a b&c' })).toBe('?q=a%20b%26c');
    expect(query({})).toBe('');
  });

  it('errorText reads FastAPI details', async () => {
    const r1 = new Response(JSON.stringify({ detail: 'Not found' }), { status: 404 });
    expect(await errorText(r1)).toBe('Not found');
    const r2 = new Response(JSON.stringify({ detail: [{ msg: 'field required' }, { msg: 'bad' }] }), { status: 422 });
    expect(await errorText(r2)).toBe('field required; bad');
    const r3 = new Response('boom', { status: 502, statusText: 'Bad Gateway' });
    expect(await errorText(r3)).toBe('502 Bad Gateway');
  });

  it('reconnect backoff grows and caps at 30s', () => {
    expect(backoff(0)).toBe(1000);
    expect(backoff(2)).toBe(4000);
    expect(backoff(20)).toBe(30_000);
  });
});
