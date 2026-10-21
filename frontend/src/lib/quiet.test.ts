import { describe, expect, it } from 'vitest';
import { describeQuietHours, formatQuietHours, parseQuietHours, quietMinutes, validTime } from './quiet';

describe('quiet hours', () => {
  it('parses the server string', () => {
    expect(parseQuietHours('21:00-07:00')).toEqual({ start: '21:00', end: '07:00' });
    expect(parseQuietHours(' 9:30 - 17:00 ')).toEqual({ start: '09:30', end: '17:00' });
    expect(parseQuietHours(null)).toBeNull();
    expect(parseQuietHours('25:00-07:00')).toBeNull();
    expect(parseQuietHours('nope')).toBeNull();
  });
  it('formats two times, rejecting bad or empty windows', () => {
    expect(formatQuietHours('22:00', '06:30')).toBe('22:00-06:30');
    expect(formatQuietHours('22:00', '22:00')).toBeNull();
    expect(formatQuietHours('7:00', '08:00')).toBeNull();
    expect(validTime('23:59')).toBe(true);
    expect(validTime('24:00')).toBe(false);
  });
  it('describes the window, wrapping midnight', () => {
    expect(quietMinutes('21:00', '07:00')).toBe(600);
    expect(describeQuietHours('21:00', '07:00')).toBe('10 h, overnight');
    expect(describeQuietHours('12:00', '13:30')).toBe('1 h 30 min');
    expect(describeQuietHours('12:00', '12:00')).toBe('');
  });
});
