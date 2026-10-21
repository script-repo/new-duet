// Decoding of the server's SSE stream (SPEC §10.3): `event:` is the type, `data:` the JSON entity.

import type { StreamEvent, StreamEventType } from './types';

export const STREAM_EVENT_TYPES: readonly StreamEventType[] = [
  'hello',
  'message.created',
  'approval.created',
  'approval.updated',
  'task.created',
  'task.updated',
  'task.log',
  'activity.created',
  'artifact.created',
  'brief.created',
  'event.created',
  'notify',
  'typing',
];

const KNOWN = new Set<string>(STREAM_EVENT_TYPES);

/** Entity events must carry the key the reducer files them under. */
const ID_KEY: Partial<Record<StreamEventType, string>> = {
  'message.created': 'id',
  'approval.created': 'id',
  'approval.updated': 'id',
  'task.created': 'id',
  'task.updated': 'id',
  'task.log': 'task_id',
  'activity.created': 'id',
  'artifact.created': 'id',
  'brief.created': 'id',
  'event.created': 'id',
};

/** Turn one SSE message into a typed event, or null when it isn't one of ours or is malformed. */
export function decodeStreamEvent(type: string, raw: string): StreamEvent | null {
  if (!KNOWN.has(type)) return null;
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
  const key = ID_KEY[type as StreamEventType];
  if (key && (data as Record<string, unknown>)[key] == null) return null;
  if (type === 'typing') return { type, data: { on: !!(data as { on?: unknown }).on } };
  return { type, data } as StreamEvent;
}
