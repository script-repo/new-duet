// Evals page (SPEC §15.5): pure shaping of `GET /api/evals` for tiles, the confusion matrix, bars and lists.

import type { AppConfig, EvalHistoryEntry, TriageError, TriageLabel, TriageMetrics, TriageSummary } from './types';

export const LABELS: TriageLabel[] = ['ignore', 'brief', 'notify', 'act'];
/** notify + act interrupt the person; ignore + brief don't */
export const INTERRUPT = new Set<string>(['notify', 'act']);

/** The bar to clear before a pilot (docs/REAL-MODELS.md). */
export const TARGETS = { accuracy: 0.75, interruptRecall: 0.95, interruptPrecision: 0.7, missedUrgent: 2, tagAccuracy: 0.6 };
/** tags where fake urgency must not page anyone */
export const TRAP_TAGS = new Set(['trap', 'injection', 'phishing']);

/** 0.5391 -> "54%"; null/NaN -> "–" */
export function pct(x: number | null | undefined, digits = 0): string {
  if (x == null || !Number.isFinite(x)) return '–';
  return `${(x * 100).toFixed(digits)}%`;
}

/** 4.54 -> "4.5"; null -> "–" */
export function score(x: number | null | undefined, digits = 1): string {
  if (x == null || !Number.isFinite(x)) return '–';
  return x.toFixed(digits);
}

/** 13007 -> "13.0k" */
export function compactNumber(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '–';
  if (Math.abs(n) < 1000) return String(n);
  if (Math.abs(n) < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

export interface MatrixCell {
  gold: string;
  pred: string;
  count: number;
  /** count / the gold row's total (0 when the row is empty) */
  share: number;
  correct: boolean;
  /** gold interrupts, predicted doesn't: the expensive mistake */
  missedUrgent: boolean;
  /** predicted interrupts, gold doesn't */
  falseAlarm: boolean;
}

export interface MatrixRow {
  gold: string;
  total: number;
  cells: MatrixCell[];
  /** recall of this label (diagonal / row total) */
  recall: number;
}

export interface Matrix {
  labels: string[];
  rows: MatrixRow[];
  colTotals: number[];
  n: number;
  correct: number;
}

/** Rows are gold labels, columns predicted; shares are per row so each row reads as "where did gold X go". */
export function confusionMatrix(m: Pick<TriageMetrics, 'labels' | 'confusion'>): Matrix {
  const labels = m.labels?.length ? m.labels : LABELS;
  const grid = labels.map((_, i) => labels.map((_, j) => Number(m.confusion?.[i]?.[j] ?? 0) || 0));
  const rows = labels.map((gold, i) => {
    const total = grid[i].reduce((a, b) => a + b, 0);
    const cells = labels.map((pred, j) => ({
      gold,
      pred,
      count: grid[i][j],
      share: total ? grid[i][j] / total : 0,
      correct: i === j,
      missedUrgent: INTERRUPT.has(gold) && !INTERRUPT.has(pred),
      falseAlarm: !INTERRUPT.has(gold) && INTERRUPT.has(pred),
    }));
    return { gold, total, cells, recall: total ? grid[i][i] / total : 0 };
  });
  const colTotals = labels.map((_, j) => grid.reduce((a, r) => a + r[j], 0));
  const n = colTotals.reduce((a, b) => a + b, 0);
  const correct = labels.reduce((a, _, i) => a + grid[i][i], 0);
  return { labels, rows, colTotals, n, correct };
}

/** A heat step 0..5 for a share; 0 only for an empty cell, so any count is visible. */
export function heatStep(share: number, count: number): number {
  if (!count) return 0;
  return Math.max(1, Math.min(5, Math.ceil(share * 5)));
}

/** Errors split into the missed-urgent ones (gold notify/act, predicted ignore/brief) and the rest. */
export function splitErrors(errors: TriageError[]): { missed: TriageError[]; other: TriageError[] } {
  const missed: TriageError[] = [];
  const other: TriageError[] = [];
  for (const e of errors ?? []) {
    if (e.gold === e.pred) continue;
    (INTERRUPT.has(e.gold) && !INTERRUPT.has(e.pred) ? missed : other).push(e);
  }
  return { missed, other };
}

export interface Tile {
  key: string;
  label: string;
  value: string;
  detail: string;
  /** 'bad' highlights it (missed urgent > 0, below target) */
  tone: 'good' | 'bad' | 'neutral';
}

export function headlineTiles(s: TriageSummary): Tile[] {
  const m = s.metrics;
  const i = m.interrupt;
  return [
    {
      key: 'accuracy',
      label: 'Accuracy',
      value: pct(m.accuracy),
      detail: `${Math.round(m.accuracy * m.n)} of ${m.n} · target ${pct(TARGETS.accuracy)}`,
      tone: m.accuracy >= TARGETS.accuracy ? 'good' : 'neutral',
    },
    {
      key: 'recall',
      label: 'Interrupt recall',
      value: pct(i.recall),
      detail: `${i.tp} of ${i.tp + i.fn} urgent caught · target ${pct(TARGETS.interruptRecall)}`,
      tone: i.recall >= TARGETS.interruptRecall ? 'good' : 'neutral',
    },
    {
      key: 'precision',
      label: 'Interrupt precision',
      value: pct(i.precision),
      detail: `${i.tp} of ${i.tp + i.fp} alerts were urgent · target ${pct(TARGETS.interruptPrecision)}`,
      tone: i.precision >= TARGETS.interruptPrecision ? 'good' : 'neutral',
    },
    {
      key: 'missed',
      label: 'Missed urgent',
      value: String(m.missed_urgent),
      detail: m.missed_urgent ? `should have interrupted · target ≤ ${TARGETS.missedUrgent}` : 'nothing urgent slipped',
      tone: m.missed_urgent > 0 ? 'bad' : 'good',
    },
    {
      key: 'false',
      label: 'False alarms',
      value: String(m.false_alarms),
      detail: 'interrupted for nothing',
      tone: 'neutral',
    },
  ];
}

export interface TagRow {
  tag: string;
  n: number;
  accuracy: number;
  trap: boolean;
  belowTarget: boolean;
}

/** Tags worst first (then by size), traps flagged against their own bar. */
export function tagRows(byTag: Record<string, { n: number; accuracy: number }> | undefined): TagRow[] {
  return Object.entries(byTag ?? {})
    .map(([tag, v]) => ({
      tag,
      n: v.n,
      accuracy: v.accuracy,
      trap: TRAP_TAGS.has(tag),
      belowTarget: TRAP_TAGS.has(tag) && v.accuracy < TARGETS.tagAccuracy,
    }))
    .sort((a, b) => a.accuracy - b.accuracy || b.n - a.n || a.tag.localeCompare(b.tag));
}

export interface ModelOption {
  value: string;
  label: string;
}

/** What the server accepts for a run: roles, profile names from config, and the offline fakes. */
export function modelOptions(config: Pick<AppConfig, 'models'> | null): ModelOption[] {
  const out: ModelOption[] = [];
  const seen = new Set<string>();
  const add = (value: string, label: string) => {
    if (seen.has(value)) return;
    seen.add(value);
    out.push({ value, label });
  };
  const models = config?.models ?? {};
  const roles = Object.keys(models);
  if (!roles.includes('triage')) add('triage', 'triage role');
  for (const role of roles) {
    const m = models[role];
    add(role, `${role} role · ${m.provider}/${m.model}`);
  }
  for (const role of roles) {
    const m = models[role];
    if (m.name) add(m.name, `${m.name} profile · ${m.provider}/${m.model}`);
  }
  add('mock', 'mock (offline, deterministic)');
  add('noisy', 'noisy (mock with errors, tests the metrics)');
  return out;
}

/** The SVG path of a sparkline through `values` (oldest first) in a w×h box; null values break the line. */
export function sparkPath(values: (number | null)[], w: number, h: number, lo = 0, hi = 1, pad = 2): string {
  if (!values.length) return '';
  const step = values.length > 1 ? (w - pad * 2) / (values.length - 1) : 0;
  const y = (v: number) => {
    const t = hi === lo ? 0.5 : (Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo);
    return +(h - pad - t * (h - pad * 2)).toFixed(2);
  };
  let d = '';
  let pen = false;
  values.forEach((v, i) => {
    if (v == null || !Number.isFinite(v)) {
      pen = false;
      return;
    }
    const x = +(pad + i * step).toFixed(2);
    d += `${pen ? 'L' : 'M'}${x} ${y(v)}`;
    pen = true;
  });
  return d;
}

/** History of one kind, oldest first (the server keeps newest first). */
export function historyOf(history: EvalHistoryEntry[], kind: string): EvalHistoryEntry[] {
  return (history ?? []).filter((h) => h.kind === kind).reverse();
}

/** A brief scenario's missing/leaked groups: a group is a phrase or a list of alternatives ("Q3 or third quarter"). */
export function groupText(groups: unknown[] | undefined): string {
  return (groups ?? [])
    .map((g) => (Array.isArray(g) ? g.map(String).join(' or ') : String(g)))
    .filter(Boolean)
    .join('; ');
}
