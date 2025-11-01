import { describe, expect, it } from 'vitest';

import { normalizeMountMetricSelection } from './mountMetrics';

describe('normalizeMountMetricSelection', () => {
  it('returns disabled selection when value is nullish', () => {
    expect(normalizeMountMetricSelection(null)).toEqual({
      enabled: false,
      mounts: [],
    });
  });

  it('keeps boolean flags', () => {
    expect(normalizeMountMetricSelection(true)).toEqual({
      enabled: true,
      mounts: [],
    });
    expect(normalizeMountMetricSelection(false)).toEqual({
      enabled: false,
      mounts: [],
    });
  });

  it('sanitizes mount entries', () => {
    const selection = normalizeMountMetricSelection({
      enabled: 'yes',
      mounts: [
        { path: '/data', label: 'Data' },
        { path: undefined, label: undefined },
      ],
    } as any);

    expect(selection).toEqual({
      enabled: true,
      mounts: [
        { path: '/data', label: 'Data' },
        { path: '', label: '' },
      ],
    });
  });
});
