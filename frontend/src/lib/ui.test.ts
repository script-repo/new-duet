import { describe, expect, it } from 'vitest';
import { describeMatch, outboxSections, stateTone, urgencyTone } from './ui';

describe('ui helpers', () => {
  it('describes policy matches', () => {
    expect(describeMatch({ tool: 'payments_pay', args: { amount: { gt: 5000 } } })).toBe('tool payments_pay · amount > 5000');
    expect(describeMatch({ tags: ['send', 'pay'] })).toBe('tagged send or pay');
    expect(describeMatch({ tool: 'sandbox_exec', sandbox: 'local' })).toBe('tool sandbox_exec · sandbox local');
    expect(describeMatch({})).toBe('any tool');
  });

  it('reads the outbox as a list or an object of lists', () => {
    expect(outboxSections([{ to: 'a' }])).toEqual([{ name: 'Sent', items: [{ to: 'a' }] }]);
    expect(outboxSections({ messages: [{ to: 'a' }], payments: [], note: 'x' }).map((s) => s.name)).toEqual(['messages', 'payments']);
    expect(outboxSections(null)).toEqual([]);
    expect(outboxSections([])).toEqual([]);
  });

  it('maps states and urgencies to tones', () => {
    expect(stateTone('failed')).toBe('danger');
    expect(stateTone('done')).toBe('ok');
    expect(urgencyTone('critical')).toBe('danger');
    expect(urgencyTone(undefined)).toBe('info');
  });
});
