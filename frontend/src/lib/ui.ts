// Small presentation maps shared by components.

import type { ActivityLevel, TaskState } from './types';

export function stateTone(state: TaskState | string): string {
  switch (state) {
    case 'running':
    case 'queued':
      return 'info';
    case 'waiting':
      return 'warn';
    case 'done':
      return 'ok';
    case 'failed':
      return 'danger';
    default:
      return '';
  }
}

export function urgencyTone(u: string | undefined): string {
  switch (u) {
    case 'critical':
      return 'danger';
    case 'high':
      return 'warn';
    case 'low':
      return '';
    default:
      return 'info';
  }
}

export function levelTone(l: ActivityLevel | string): string {
  return l === 'error' ? 'danger' : l === 'warn' ? 'warn' : l === 'notify' ? 'accent' : '';
}

export function artifactIcon(kind: string): string {
  switch (kind) {
    case 'xlsx':
    case 'csv':
      return 'sheet';
    case 'pptx':
      return 'slides';
    case 'html':
      return 'globe';
    default:
      return 'doc';
  }
}

const OPS: Record<string, string> = { eq: '=', ne: '≠', gt: '>', lt: '<', in: 'in', regex: '~', contains: 'contains' };

function show(v: unknown): string {
  return typeof v === 'string' ? v : JSON.stringify(v);
}

/** A policy rule's match as a short phrase: `tool payments_pay · amount > 5000 · sandbox local`. */
export function describeMatch(match: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(match ?? {})) {
    if (key === 'args' && value && typeof value === 'object') {
      for (const [arg, cond] of Object.entries(value as Record<string, unknown>)) {
        if (cond && typeof cond === 'object' && !Array.isArray(cond)) {
          for (const [op, v] of Object.entries(cond as Record<string, unknown>)) parts.push(`${arg} ${OPS[op] ?? op} ${show(v)}`);
        } else {
          parts.push(`${arg} = ${show(cond)}`);
        }
      }
    } else if (key === 'tags' && Array.isArray(value)) {
      parts.push(`tagged ${value.join(' or ')}`);
    } else {
      parts.push(`${key} ${show(value)}`);
    }
  }
  return parts.join(' · ') || 'any tool';
}

export interface OutboxSection {
  name: string;
  items: Record<string, unknown>[];
}

/** /api/outbox isn't typed in the spec: accept a list, or an object of lists (`{messages: [...], payments: [...]}`). */
export function outboxSections(data: unknown): OutboxSection[] {
  const asItems = (list: unknown[]) => list.map((x) => (x && typeof x === 'object' ? (x as Record<string, unknown>) : { value: x }));
  if (Array.isArray(data)) return data.length ? [{ name: 'Sent', items: asItems(data) }] : [];
  if (!data || typeof data !== 'object') return [];
  return Object.entries(data as Record<string, unknown>)
    .filter(([, v]) => Array.isArray(v))
    .map(([name, v]) => ({ name, items: asItems(v as unknown[]) }));
}

/** "#/connections?connected=gmail" -> {path: "connections", params}; the query rides in the hash after OAuth. */
export function splitHash(hash: string): { path: string; params: URLSearchParams } {
  const h = hash.replace(/^#\/?/, '');
  const q = h.indexOf('?');
  return q < 0 ? { path: h, params: new URLSearchParams() } : { path: h.slice(0, q), params: new URLSearchParams(h.slice(q + 1)) };
}
