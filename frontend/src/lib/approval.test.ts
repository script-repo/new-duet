import { describe, expect, it } from 'vitest';
import { approveBody, argRows, buildEditArgs, initialDrafts, parseDraft, rejectBody } from './approval';
import { approval } from './fixtures';

describe('approval edits', () => {
  it('drafts cover editable args only, multi-line intact', () => {
    const d = initialDrafts(approval());
    expect(Object.keys(d)).toEqual(['subject', 'body']);
    expect(d.body).toBe('Hi Sam,\n\nAttached.\n\nThanks,\nD');
  });

  it('unchanged drafts approve as-is', () => {
    const a = approval();
    expect(approveBody(a, initialDrafts(a))).toEqual({ decision: 'approve' });
    expect(approveBody(a, null)).toEqual({ decision: 'approve' });
  });

  it('a changed draft becomes an edit with the full args', () => {
    const a = approval();
    const body = approveBody(a, { ...initialDrafts(a), body: 'Hi Sam,\n\nAttached.\n\nBest,\nD' });
    expect(body).toEqual({ decision: 'edit', args: { ...a.args, body: 'Hi Sam,\n\nAttached.\n\nBest,\nD' } });
  });

  it('never changes non-editable args', () => {
    const a = approval();
    const r = buildEditArgs(a, { to: 'evil@x.example', subject: 'New' });
    expect(r.args.to).toBe('sam@harbor.example');
    expect(r.changed).toEqual(['subject']);
  });

  it('parses drafts back to the original type', () => {
    expect(parseDraft(12, ' 15.5 ', 'amount')).toBe(15.5);
    expect(parseDraft(false, 'true', 'urgent')).toBe(true);
    expect(parseDraft([], '["a@b.c"]', 'cc')).toEqual(['a@b.c']);
    expect(() => parseDraft(12, 'twelve', 'amount')).toThrow('amount must be a number');
    expect(() => parseDraft({}, '{bad', 'meta')).toThrow('meta must be valid JSON');
  });

  it('reports parse errors instead of sending', () => {
    const a = approval({ editable: ['amount'] });
    expect(approveBody(a, { amount: 'lots' })).toEqual({ error: 'amount must be a number' });
  });

  it('reject note is optional and trimmed', () => {
    expect(rejectBody('  ')).toEqual({ decision: 'reject' });
    expect(rejectBody(' not now ')).toEqual({ decision: 'reject', note: 'not now' });
  });

  it('argRows lists editable first and flags multi-line values', () => {
    const rows = argRows(approval());
    expect(rows.map((r) => r.key).slice(0, 2)).toEqual(['subject', 'body']);
    expect(rows.find((r) => r.key === 'body')!.multiline).toBe(true);
    expect(rows.find((r) => r.key === 'to')!.editable).toBe(false);
  });
});
