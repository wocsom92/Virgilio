import type { MountDisplayEntry, MountMetricSelection, SelectedMetricValue } from '../api/client';

function sanitizeMountEntry(entry: Partial<MountDisplayEntry>): MountDisplayEntry {
  return {
    path: entry.path ?? '',
    label: entry.label ?? '',
  };
}

export function normalizeMountMetricSelection(
  value: SelectedMetricValue | undefined | null
): MountMetricSelection {
  if (value && typeof value === 'object' && 'enabled' in value) {
    const mounts = Array.isArray(value.mounts) ? value.mounts.map(sanitizeMountEntry) : [];
    return {
      enabled: Boolean(value.enabled),
      mounts,
    };
  }
  if (typeof value === 'boolean') {
    return {
      enabled: value,
      mounts: [],
    };
  }
  return {
    enabled: false,
    mounts: [],
  };
}
