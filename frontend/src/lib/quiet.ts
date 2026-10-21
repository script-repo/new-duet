// Quiet hours "HH:MM-HH:MM" (SPEC §15.4 notify settings): parse, format and describe.

const HHMM = /^([01]\d|2[0-3]):([0-5]\d)$/;

export function validTime(t: string): boolean {
  return HHMM.test(t.trim());
}

/** "21:00-07:00" -> {start: "21:00", end: "07:00"}; null for off or malformed. */
export function parseQuietHours(s: string | null | undefined): { start: string; end: string } | null {
  if (!s) return null;
  const m = s.trim().match(/^(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$/);
  if (!m) return null;
  const pad = (t: string) => (t.length === 4 ? '0' + t : t);
  const start = pad(m[1]);
  const end = pad(m[2]);
  return validTime(start) && validTime(end) ? { start, end } : null;
}

/** Two times as the server's string, or null when either is invalid or they are equal (no window). */
export function formatQuietHours(start: string, end: string): string | null {
  const a = start.trim();
  const b = end.trim();
  if (!validTime(a) || !validTime(b) || a === b) return null;
  return `${a}-${b}`;
}

function minutes(t: string): number {
  const [h, m] = t.split(':').map(Number);
  return h * 60 + m;
}

/** Length of the window in minutes, wrapping past midnight. */
export function quietMinutes(start: string, end: string): number {
  const d = minutes(end) - minutes(start);
  return d > 0 ? d : d + 24 * 60;
}

/** "10 h, overnight" / "1 h 30 min" */
export function describeQuietHours(start: string, end: string): string {
  if (!validTime(start) || !validTime(end) || start === end) return '';
  const total = quietMinutes(start, end);
  const h = Math.floor(total / 60);
  const m = total % 60;
  const len = h && m ? `${h} h ${m} min` : h ? `${h} h` : `${m} min`;
  return minutes(end) <= minutes(start) ? `${len}, overnight` : len;
}
