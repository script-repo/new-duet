// Approval cards: drafts for editable arguments and the decision body built from them.

import { formatValue } from './format';
import type { Approval } from './types';

export type Drafts = Record<string, string>;

/** Initial text for each editable argument. */
export function initialDrafts(a: Approval): Drafts {
  const out: Drafts = {};
  for (const key of a.editable) out[key] = formatValue(a.args[key]);
  return out;
}

/** Convert a draft back to the type of the original value. Throws with a readable message. */
export function parseDraft(original: unknown, text: string, key: string): unknown {
  if (typeof original === 'string' || original === undefined) return text;
  if (typeof original === 'number') {
    const n = Number(text.trim());
    if (text.trim() === '' || !Number.isFinite(n)) throw new Error(`${key} must be a number`);
    return n;
  }
  if (typeof original === 'boolean') {
    const t = text.trim().toLowerCase();
    if (t === 'true' || t === 'yes') return true;
    if (t === 'false' || t === 'no') return false;
    throw new Error(`${key} must be true or false`);
  }
  if (original === null && text.trim() === '') return null;
  try {
    return JSON.parse(text);
  } catch {
    if (original === null) return text;
    throw new Error(`${key} must be valid JSON`);
  }
}

export interface EditResult {
  args: Record<string, unknown>;
  changed: string[];
  errors: string[];
}

/**
 * The arguments to send with an `edit` decision: the original arguments with editable keys replaced by their
 * parsed drafts. Keys that aren't editable are never changed, whatever the drafts say.
 */
export function buildEditArgs(a: Approval, drafts: Drafts): EditResult {
  const args: Record<string, unknown> = { ...a.args };
  const changed: string[] = [];
  const errors: string[] = [];
  for (const key of a.editable) {
    if (!(key in drafts)) continue;
    const before = formatValue(a.args[key]);
    if (drafts[key] === before) continue;
    try {
      args[key] = parseDraft(a.args[key], drafts[key], key);
      changed.push(key);
    } catch (e) {
      errors.push((e as Error).message);
    }
  }
  return { args, changed, errors };
}

export type DecisionBody =
  | { decision: 'approve'; note?: string }
  | { decision: 'reject'; note?: string }
  | { decision: 'edit'; args: Record<string, unknown>; note?: string };

/** Approve sends `edit` when any draft changed, so the user's edits are what runs. */
export function approveBody(a: Approval, drafts: Drafts | null): DecisionBody | { error: string } {
  if (!drafts) return { decision: 'approve' };
  const r = buildEditArgs(a, drafts);
  if (r.errors.length) return { error: r.errors.join('; ') };
  return r.changed.length ? { decision: 'edit', args: r.args } : { decision: 'approve' };
}

export function rejectBody(note: string): DecisionBody {
  const n = note.trim();
  return n ? { decision: 'reject', note: n } : { decision: 'reject' };
}

/** Argument rows for display: editable ones first in their declared order, then the rest. */
export function argRows(a: Approval): { key: string; value: string; editable: boolean; multiline: boolean }[] {
  const keys = [...a.editable, ...Object.keys(a.args).filter((k) => !a.editable.includes(k))];
  return keys.map((key) => {
    const value = formatValue(a.args[key]);
    return { key, value, editable: a.editable.includes(key), multiline: value.includes('\n') || value.length > 80 };
  });
}
