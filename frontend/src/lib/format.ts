// Formatting helpers: times, durations, countdowns, sizes and argument values.

/** Parse an ISO timestamp; naive ones (no zone) are UTC, as the server writes them. */
export function parseTs(ts: string | null | undefined): number {
  if (!ts) return NaN;
  const naive = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(ts);
  return Date.parse(naive ? ts + 'Z' : ts);
}

export interface Countdown {
  seconds: number;
  expired: boolean;
  text: string;
  /** under a minute left */
  urgent: boolean;
}

/** Time left until `expiresAt`, as "14:05", "1:02:03" or "expired". */
export function countdown(expiresAt: string, now: number = Date.now()): Countdown {
  const end = parseTs(expiresAt);
  if (Number.isNaN(end)) return { seconds: 0, expired: false, text: '', urgent: false };
  const seconds = Math.max(0, Math.floor((end - now) / 1000));
  if (seconds <= 0) return { seconds: 0, expired: true, text: 'expired', urgent: true };
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  const text = h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  return { seconds, expired: false, text, urgent: seconds < 60 };
}

/** "just now", "5 min ago", "3 h ago", "yesterday", then a date. */
export function relativeTime(ts: string, now: number = Date.now()): string {
  const t = parseTs(ts);
  if (Number.isNaN(t)) return '';
  const diff = Math.round((now - t) / 1000);
  if (diff < 0) {
    const ahead = -diff;
    if (ahead < 60) return 'in a moment';
    if (ahead < 3600) return `in ${Math.round(ahead / 60)} min`;
    if (ahead < 86400) return `in ${Math.round(ahead / 3600)} h`;
    return shortDate(ts);
  }
  if (diff < 45) return 'just now';
  if (diff < 3600) return `${Math.max(1, Math.round(diff / 60))} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  if (diff < 172800) return 'yesterday';
  return shortDate(ts);
}

export function clockTime(ts: string): string {
  const t = parseTs(ts);
  if (Number.isNaN(t)) return '';
  return new Date(t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

export function shortDate(ts: string): string {
  const t = parseTs(ts);
  if (Number.isNaN(t)) return '';
  return new Date(t).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

export function dateTime(ts: string): string {
  const t = parseTs(ts);
  if (Number.isNaN(t)) return '';
  return new Date(t).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

/** Elapsed time between two timestamps (or until now), as "42s", "3m 05s", "1h 02m". */
export function duration(from: string | null, to: string | null, now: number = Date.now()): string {
  const a = parseTs(from);
  if (Number.isNaN(a)) return '';
  const b = to ? parseTs(to) : now;
  const s = Math.max(0, Math.round((b - a) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '';
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

/** An argument value for display: strings as they are (multi-line intact), everything else as JSON. */
export function formatValue(v: unknown): string {
  if (typeof v === 'string') return v;
  if (v === undefined) return '';
  try {
    const compact = JSON.stringify(v);
    if (compact === undefined) return String(v);
    return compact.length <= 60 ? compact : JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

/** "email_send" -> "Email send" */
export function humanize(name: string): string {
  const s = name.replace(/[_.-]+/g, ' ').trim();
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

export function greetingFor(hour: number): string {
  if (hour < 5) return 'Good evening';
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}
