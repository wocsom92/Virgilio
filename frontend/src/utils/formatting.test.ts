import { describe, expect, it } from 'vitest';

import { formatBitsPerSecond, formatFan, formatUpdatedAge, getStaleHintClass, getStatusClass } from './formatting';

describe('formatUpdatedAge', () => {
  const now = Date.parse('2026-01-01T12:00:00Z');

  it('covers the no-data, invalid, and elapsed buckets', () => {
    expect(formatUpdatedAge(null, now)).toBe('upd: no data');
    expect(formatUpdatedAge('not a date', now)).toBeNull();
    expect(formatUpdatedAge('2026-01-01T11:59:40Z', now)).toBe('upd: just now');
    expect(formatUpdatedAge('2026-01-01T11:55:00Z', now)).toBe('upd: 5 minutes ago');
    expect(formatUpdatedAge('2026-01-01T11:59:00Z', now)).toBe('upd: 1 minute ago');
    expect(formatUpdatedAge('2026-01-01T09:00:00Z', now)).toBe('upd: 3 hours ago');
    expect(formatUpdatedAge('2025-12-30T12:00:00Z', now)).toBe('upd: 2 days ago');
  });
});

describe('status classes', () => {
  it('maps statuses to tile and stale-hint classes', () => {
    expect(getStatusClass('critical')).toBe('quick-status--critical');
    expect(getStatusClass('unknown')).toBe('quick-status--unknown');
    expect(getStaleHintClass('info')).toBe('quick-status--stale-hint-ok');
    expect(getStaleHintClass('warn')).toBe('quick-status--stale-hint-warn');
  });
});

describe('formatBitsPerSecond', () => {
  it('picks a unit that keeps the number readable', () => {
    expect(formatBitsPerSecond(950)).toBe('950 bps');
    expect(formatBitsPerSecond(12_500)).toBe('12.5 Kbps');
    expect(formatBitsPerSecond(3_200_000)).toBe('3.2 Mbps');
    expect(formatBitsPerSecond(125_000_000)).toBe('125 Mbps');
    expect(formatBitsPerSecond(1_100_000_000)).toBe('1.1 Gbps');
    expect(formatBitsPerSecond(null)).toBe('N/A');
  });
});

describe('formatFan', () => {
  it('describes the fan level and speed', () => {
    expect(formatFan(1, 4, 2905)).toBe('level 1/4, 2905 rpm');
    expect(formatFan(0, 4, 0)).toBe('off, 0 rpm');
    expect(formatFan(2, null, null)).toBe('level 2');
    expect(formatFan(null, null, null)).toBe('N/A');
  });
});
