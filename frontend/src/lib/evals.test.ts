import { describe, expect, it } from 'vitest';
import {
  compactNumber, confusionMatrix, groupText, headlineTiles, heatStep, historyOf, modelOptions, pct, score, sparkPath, splitErrors, tagRows,
} from './evals';
import type { TriageError, TriageSummary } from './types';

const confusion = [
  [5, 33, 6, 0],
  [0, 41, 11, 0],
  [0, 10, 31, 0],
  [0, 2, 7, 4],
];

describe('confusionMatrix', () => {
  it('builds rows with shares, totals and the kind of each mistake', () => {
    const m = confusionMatrix({ labels: ['ignore', 'brief', 'notify', 'act'], confusion });
    expect(m.n).toBe(150);
    expect(m.correct).toBe(81);
    expect(m.colTotals).toEqual([5, 86, 55, 4]);
    expect(m.rows[0].total).toBe(44);
    expect(m.rows[0].cells[1].share).toBeCloseTo(33 / 44);
    expect(m.rows[2].cells[2]).toMatchObject({ correct: true, missedUrgent: false });
    expect(m.rows[2].cells[1]).toMatchObject({ missedUrgent: true, falseAlarm: false }); // notify -> brief
    expect(m.rows[0].cells[2]).toMatchObject({ falseAlarm: true }); // ignore -> notify
    expect(m.rows[3].recall).toBeCloseTo(4 / 13);
  });
  it('survives a ragged or empty matrix', () => {
    const m = confusionMatrix({ labels: ['ignore', 'brief', 'notify', 'act'], confusion: [[1]] });
    expect(m.n).toBe(1);
    expect(m.rows[1].recall).toBe(0);
    expect(m.rows[1].cells[0].share).toBe(0);
  });
  it('steps the heat, keeping any count visible', () => {
    expect(heatStep(0, 0)).toBe(0);
    expect(heatStep(0.01, 1)).toBe(1);
    expect(heatStep(0.5, 10)).toBe(3);
    expect(heatStep(1, 10)).toBe(5);
  });
});

describe('formatting', () => {
  it('formats percents, scores and counts', () => {
    expect(pct(0.5391)).toBe('54%');
    expect(pct(0.5391, 1)).toBe('53.9%');
    expect(pct(null)).toBe('–');
    expect(score(4.54)).toBe('4.5');
    expect(score(null)).toBe('–');
    expect(compactNumber(950)).toBe('950');
    expect(compactNumber(13007)).toBe('13.0k');
    expect(compactNumber(2_500_000)).toBe('2.5M');
  });
});

const err = (id: string, gold: string, pred: string): TriageError =>
  ({ id, title: id, kind: 'email', gold, pred, urgency: 'normal', reason: '', decided_by: 'model', gold_reason: '', tags: [] }) as TriageError;

describe('errors and tiles', () => {
  it('splits missed urgent from other errors', () => {
    const { missed, other } = splitErrors([err('a', 'notify', 'brief'), err('b', 'act', 'ignore'), err('c', 'act', 'notify'), err('d', 'ignore', 'notify'), err('e', 'brief', 'brief')]);
    expect(missed.map((e) => e.id)).toEqual(['a', 'b']);
    expect(other.map((e) => e.id)).toEqual(['c', 'd']);
  });

  it('highlights missed urgent when there are any', () => {
    const s = {
      metrics: { n: 150, accuracy: 0.54, interrupt: { precision: 0.71, recall: 0.78, f1: 0, tp: 42, fp: 17, fn: 12 }, missed_urgent: 12, false_alarms: 17 },
    } as unknown as TriageSummary;
    const tiles = headlineTiles(s);
    expect(tiles.map((t) => t.value)).toEqual(['54%', '78%', '71%', '12', '17']);
    expect(tiles.find((t) => t.key === 'missed')?.tone).toBe('bad');
    expect(tiles.find((t) => t.key === 'precision')?.tone).toBe('good');
    expect(tiles[1].detail).toContain('42 of 54');
  });

  it('sorts tags worst first and flags traps under their bar', () => {
    const rows = tagRows({ vip: { n: 14, accuracy: 0.29 }, trap: { n: 7, accuracy: 0.14 }, today: { n: 16, accuracy: 0.94 }, phishing: { n: 3, accuracy: 0.8 } });
    expect(rows.map((r) => r.tag)).toEqual(['trap', 'vip', 'phishing', 'today']);
    expect(rows[0]).toMatchObject({ trap: true, belowTarget: true });
    expect(rows[2]).toMatchObject({ trap: true, belowTarget: false });
  });
});

describe('modelOptions', () => {
  it('offers roles, profile names and the fakes, once each', () => {
    const opts = modelOptions({
      models: {
        triage: { name: 'cheap', provider: 'mock', model: 'mock-cheap', has_api_key: false },
        brief: { name: 'strong', provider: 'mock', model: 'mock-strong', has_api_key: false },
        chat: { name: 'strong', provider: 'mock', model: 'mock-strong', has_api_key: false },
      },
    });
    expect(opts.map((o) => o.value)).toEqual(['triage', 'brief', 'chat', 'cheap', 'strong', 'mock', 'noisy']);
    expect(modelOptions(null).map((o) => o.value)).toEqual(['triage', 'mock', 'noisy']);
  });
});

describe('sparkline and history', () => {
  it('draws a path, breaking on gaps', () => {
    expect(sparkPath([], 100, 20)).toBe('');
    expect(sparkPath([0, 1], 100, 20, 0, 1, 0)).toBe('M0 20L100 0');
    expect(sparkPath([0.5, null, 0.5], 100, 20, 0, 1, 0)).toBe('M0 10M100 10');
    expect(sparkPath([2], 100, 20, 0, 1, 0)).toBe('M0 0');
  });
  it('filters a kind, oldest first', () => {
    const h = [
      { kind: 'brief', model: 'm', ran_at: '3', headline: 4.5, missed_urgent: null },
      { kind: 'triage', model: 'm', ran_at: '2', headline: 0.6, missed_urgent: 3 },
      { kind: 'triage', model: 'm', ran_at: '1', headline: 0.5, missed_urgent: 9 },
    ];
    expect(historyOf(h, 'triage').map((x) => x.ran_at)).toEqual(['1', '2']);
  });
});

describe('groupText', () => {
  it('joins groups, with alternatives inside one', () => {
    expect(groupText([['1043', 'Oakridge'], 'reminder'])).toBe('1043 or Oakridge; reminder');
    expect(groupText(undefined)).toBe('');
  });
});
