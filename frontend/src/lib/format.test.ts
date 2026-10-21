import { describe, expect, it } from 'vitest';
import { countdown, duration, formatBytes, formatValue, humanize, parseTs, relativeTime } from './format';

const NOW = Date.parse('2026-10-21T13:00:00Z');

describe('format', () => {
  it('countdown shows m:ss, h:mm:ss and expired', () => {
    expect(countdown('2026-10-21T13:14:05Z', NOW)).toMatchObject({ text: '14:05', expired: false, urgent: false });
    expect(countdown('2026-10-21T14:02:03Z', NOW).text).toBe('1:02:03');
    expect(countdown('2026-10-21T13:00:30Z', NOW)).toMatchObject({ text: '0:30', urgent: true });
    expect(countdown('2026-10-21T12:59:00Z', NOW)).toMatchObject({ text: 'expired', expired: true });
    expect(countdown('garbage', NOW).text).toBe('');
  });

  it('treats naive timestamps as UTC', () => {
    expect(parseTs('2026-10-21T13:00:00')).toBe(NOW);
    expect(parseTs('2026-10-21T09:00:00-04:00')).toBe(NOW);
  });

  it('relativeTime', () => {
    expect(relativeTime('2026-10-21T12:59:50Z', NOW)).toBe('just now');
    expect(relativeTime('2026-10-21T12:55:00Z', NOW)).toBe('5 min ago');
    expect(relativeTime('2026-10-21T10:00:00Z', NOW)).toBe('3 h ago');
    expect(relativeTime('2026-10-20T10:00:00Z', NOW)).toBe('yesterday');
    expect(relativeTime('2026-10-21T13:10:00Z', NOW)).toBe('in 10 min');
  });

  it('duration', () => {
    expect(duration('2026-10-21T12:59:18Z', null, NOW)).toBe('42s');
    expect(duration('2026-10-21T12:56:55Z', '2026-10-21T13:00:00Z')).toBe('3m 05s');
    expect(duration('2026-10-21T11:58:00Z', null, NOW)).toBe('1h 02m');
    expect(duration(null, null, NOW)).toBe('');
  });

  it('formatBytes', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(5120)).toBe('5.0 KB');
    expect(formatBytes(30 * 1024 * 1024)).toBe('30 MB');
  });

  it('formatValue keeps multi-line strings and pretty-prints the rest', () => {
    expect(formatValue('a\nb')).toBe('a\nb');
    expect(formatValue({ a: 1 })).toBe('{"a":1}');
    expect(formatValue(['ar_1'])).toBe('["ar_1"]');
    expect(formatValue({ long: 'x'.repeat(60) })).toBe(`{\n  "long": "${'x'.repeat(60)}"\n}`);
    expect(formatValue(5)).toBe('5');
    expect(formatValue(null)).toBe('null');
    expect(humanize('email_send')).toBe('Email send');
  });
});
