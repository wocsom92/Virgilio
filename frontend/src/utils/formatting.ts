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
