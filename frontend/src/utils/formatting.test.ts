import { describe, expect, it } from 'vitest';

import { formatUpdatedAge, getStaleHintClass, getStatusClass } from './formatting';

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
