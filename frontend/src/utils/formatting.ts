export function formatBinaryBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return 'N/A';
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  const units = ['KiB', 'MiB', 'GiB', 'TiB'];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const rounded = value >= 10 ? value.toFixed(0) : value.toFixed(1);
  return `${rounded} ${units[unitIndex]}`;
}

export function formatBinarySizeFromGiB(valueGiB: number | null | undefined): string {
  if (valueGiB === null || valueGiB === undefined || Number.isNaN(valueGiB)) return 'N/A';
  if (valueGiB >= 1024) return `${Math.round(valueGiB / 1024)} TiB`;
  if (valueGiB >= 1) return `${Math.round(valueGiB)} GiB`;
  const valueMiB = valueGiB * 1024;
  if (valueMiB >= 1) return `${Math.round(valueMiB)} MiB`;
  return `${Math.round(valueMiB * 1024)} KiB`;
}

export function formatTemperature(valueCelsius: number | null | undefined): string {
  if (valueCelsius === null || valueCelsius === undefined || Number.isNaN(valueCelsius)) return 'N/A';
  return `${valueCelsius.toFixed(1)} °C`;
}

export type TileStatus = 'ok' | 'info' | 'warn' | 'critical' | 'unknown';

export function getStatusClass(status: TileStatus): string {
  if (status === 'critical') return 'quick-status--critical';
  if (status === 'warn') return 'quick-status--warn';
  if (status === 'info') return 'quick-status--info';
  if (status === 'ok') return 'quick-status--ok';
  return 'quick-status--unknown';
}

export function getStaleHintClass(status: TileStatus): string {
  if (status === 'critical') return 'quick-status--stale-hint-critical';
  if (status === 'warn') return 'quick-status--stale-hint-warn';
  if (status === 'info' || status === 'ok') return 'quick-status--stale-hint-ok';
  return 'quick-status--stale-hint-unknown';
}

export function formatUpdatedAge(reportedAt: string | null | undefined, nowMs: number): string | null {
  if (!reportedAt) {
    return 'upd: no data';
  }
  const parsed = Date.parse(reportedAt);
  if (Number.isNaN(parsed)) {
    return null;
  }
  const elapsedSeconds = Math.max(0, Math.round((nowMs - parsed) / 1000));
  if (elapsedSeconds < 45) {
    return 'upd: just now';
  }
  if (elapsedSeconds < 3600) {
    const minutes = Math.max(1, Math.round(elapsedSeconds / 60));
    return `upd: ${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  }
  if (elapsedSeconds < 86_400) {
    const hours = Math.max(1, Math.round(elapsedSeconds / 3600));
    return `upd: ${hours} hour${hours === 1 ? '' : 's'} ago`;
  }
  const days = Math.max(1, Math.round(elapsedSeconds / 86_400));
  return `upd: ${days} day${days === 1 ? '' : 's'} ago`;
}
