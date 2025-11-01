import { useEffect, useMemo, useState } from 'react';
import { FiAlertTriangle, FiArrowLeft, FiArrowRight } from 'react-icons/fi';
import classNames from 'classnames';

import type {
  MetricRange,
  MetricSeriesPoint,
  MetricSnapshot,
  MonitoredBackend,
} from '../api/client';
import { fetchMetricSeries } from '../api/client';
import { MetricsChart } from './MetricsChart';
import { normalizeMountMetricSelection } from '../utils/mountMetrics';

interface BackendCardProps {
  backend: MonitoredBackend;
  onRefresh?: (backend: MonitoredBackend) => void;
  disabled?: boolean;
  hidden: boolean;
  onToggleHidden: (backendId: number) => void;
}

const RANGE_OPTIONS: Array<{ value: MetricRange; label: string }> = [
  { value: 'hourly', label: 'Hourly' },
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
];

const CHART_COLORS = {
  cpuTemp: '#f97316',
  ram: '#0d6efd',
  disk: '#20c997',
  cpuLoadOne: '#ff6384',
  cpuLoadFive: '#a855f7',
  cpuLoadFifteen: '#0dcaf0',
};

const MOUNT_COLORS = ['#10b981', '#facc15', '#38bdf8', '#ef4444', '#a855f7', '#22d3ee'];

function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const hasTimezone = /[zZ]|[+-]\d{2}:\d{2}$/.test(value);
  const normalized = hasTimezone ? value : `${value}Z`;
  const timestamp = Date.parse(normalized);
  if (Number.isNaN(timestamp)) {
    return null;
  }
  return new Date(timestamp);
}

function metricIsEnabled(selected: MonitoredBackend['selected_metrics'], key: string, fallback = true): boolean {
  const value = selected?.[key];
  if (typeof value === 'boolean') {
    return value;
  }
  if (value && typeof value === 'object' && 'enabled' in value) {
    return Boolean((value as { enabled?: boolean }).enabled);
  }
  return fallback;
}

function formatUptime(snapshot: MetricSnapshot | null | undefined) {
  if (!snapshot?.uptime_seconds) return null;
  const seconds = snapshot.uptime_seconds;
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  const remainderHours = hours % 24;
  const remainderMinutes = minutes % 60;
  return `${days}d ${remainderHours}h ${remainderMinutes}m`;
}

export function BackendCard({ backend, onRefresh, disabled, hidden, onToggleHidden }: BackendCardProps) {
  const snapshot = backend.latest_snapshot ?? null;
  const warnings = snapshot?.warnings ?? [];
  const hasWarnings = warnings.length > 0;
  const lastSeenDate = parseDate(backend.last_seen_at);
  const lastSeen = lastSeenDate ? lastSeenDate.toLocaleString() : 'Never';
  const latestSampleDate = snapshot?.reported_at ? parseDate(snapshot.reported_at) : null;
  const latestSample = latestSampleDate ? latestSampleDate.toLocaleString() : null;
  const [range, setRange] = useState<MetricRange>('hourly');
  const [series, setSeries] = useState<MetricSeriesPoint[]>([]);
  const [seriesLoading, setSeriesLoading] = useState(false);
  const [seriesError, setSeriesError] = useState<string | null>(null);
  const [rebootMarkers, setRebootMarkers] = useState<number[]>([]);
  const [rangeOffsets, setRangeOffsets] = useState<Record<MetricRange, number>>({
    hourly: 0,
    daily: 0,
    weekly: 0,
  });
  const [windowBounds, setWindowBounds] = useState<{ start: number; end: number } | null>(null);
  const [navigationOffsets, setNavigationOffsets] = useState<{ previous: number | null; next: number | null }>({
    previous: null,
    next: null,
  });
  const selectedMetrics = backend.selected_metrics ?? undefined;
  const mountSelection = normalizeMountMetricSelection(backend.selected_metrics?.mounted_usage);
  const selectedMounts = mountSelection.enabled ? mountSelection.mounts : [];
  const showMountNotice = mountSelection.enabled && selectedMounts.length === 0;
  const uptime = formatUptime(snapshot);
  const currentOffset = rangeOffsets[range] ?? 0;

  useEffect(() => {
    if (hidden) {
      setSeries([]);
      setSeriesError(null);
      setSeriesLoading(false);
      setWindowBounds(null);
      setNavigationOffsets({ previous: null, next: null });
      return;
    }
    let cancelled = false;
    setSeriesLoading(true);
    setSeriesError(null);
    void (async () => {
      try {
        const data = await fetchMetricSeries(backend.id, range, currentOffset);
        if (!cancelled) {
          setSeries(data.points);
          setRebootMarkers(
            (data.reboot_markers || [])
              .map((value) => parseDate(value)?.getTime() ?? Number.NaN)
              .filter((ts): ts is number => Number.isFinite(ts))
          );
          setRangeOffsets((prev) => {
            if (prev[range] === data.window_offset) {
              return prev;
            }
            return { ...prev, [range]: data.window_offset };
          });
          const parsedStart = Date.parse(data.window_start);
          const parsedEnd = Date.parse(data.window_end);
          setWindowBounds(
            Number.isFinite(parsedStart) && Number.isFinite(parsedEnd)
              ? { start: parsedStart, end: parsedEnd }
              : null
          );
          setNavigationOffsets({
            previous: data.previous_offset_with_data ?? null,
            next: data.next_offset_with_data ?? null,
          });
        }
      } catch (err) {
        if (!cancelled) {
          setSeries([]);
          setSeriesError(err instanceof Error ? err.message : 'Unable to load metrics');
        }
      } finally {
        if (!cancelled) {
          setSeriesLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [backend.id, range, currentOffset, hidden]);

  const windowLabel = useMemo(() => {
    if (!windowBounds || Number.isNaN(windowBounds.start) || Number.isNaN(windowBounds.end)) {
      return null;
    }
    const start = new Date(windowBounds.start);
    const end = new Date(windowBounds.end);
    return `${start.toLocaleString()} – ${end.toLocaleString()}`;
  }, [windowBounds]);

  const baseCharts = useMemo(() => {
    const charts: Array<{
      key: string;
      title: string;
      datasets: Array<{
        label: string;
        color: string;
        extractor: (point: MetricSeriesPoint) => number | null;
      }>;
      suggestedMin: number;
      suggestedMax: number | undefined;
      emptyMessage: string;
    }> = [];

    if (metricIsEnabled(selectedMetrics, 'cpu_temperature_c')) {
      charts.push({
        key: 'cpu-temp',
        title: 'CPU Temperature',
        datasets: [
          {
            label: '°C',
            color: CHART_COLORS.cpuTemp,
            extractor: (point: MetricSeriesPoint) => point.cpu_temperature_c ?? null,
          },
        ],
        suggestedMin: 0,
        suggestedMax: 100,
        emptyMessage: 'Waiting for CPU temperature samples.',
      });
    }

    if (metricIsEnabled(selectedMetrics, 'ram_used_percent')) {
      charts.push({
        key: 'ram-usage',
        title: 'RAM Usage',
        datasets: [
          {
            label: '% used',
            color: CHART_COLORS.ram,
            extractor: (point: MetricSeriesPoint) => point.ram_used_percent ?? null,
          },
        ],
        suggestedMin: 0,
        suggestedMax: 100,
        emptyMessage: 'Waiting for RAM samples.',
      });
    }

    if (metricIsEnabled(selectedMetrics, 'disk_usage_percent')) {
      charts.push({
        key: 'disk-usage',
        title: 'Disk Usage',
        datasets: [
          {
            label: '% used',
            color: CHART_COLORS.disk,
            extractor: (point: MetricSeriesPoint) => point.disk_usage_percent ?? null,
          },
        ],
        suggestedMin: 0,
        suggestedMax: 100,
        emptyMessage: 'Waiting for disk samples.',
      });
    }

    if (metricIsEnabled(selectedMetrics, 'cpu_load')) {
      charts.push({
        key: 'cpu-load',
        title: 'CPU Load Average',
        datasets: [
          {
            label: '1 min',
            color: CHART_COLORS.cpuLoadOne,
            extractor: (point: MetricSeriesPoint) => point.cpu_load?.one ?? null,
          },
          {
            label: '5 min',
            color: CHART_COLORS.cpuLoadFive,
            extractor: (point: MetricSeriesPoint) => point.cpu_load?.five ?? null,
          },
          {
            label: '15 min',
            color: CHART_COLORS.cpuLoadFifteen,
            extractor: (point: MetricSeriesPoint) => point.cpu_load?.fifteen ?? null,
          },
        ],
        suggestedMin: 0,
        suggestedMax: 8,
        emptyMessage: 'Waiting for load averages.',
      });
    }

    // Network throughput per interface (bps)
    const interfaces = new Set<string>();
    series.forEach((point) => {
      point.network_bps?.forEach((entry) => {
        if (entry.interface) interfaces.add(entry.interface);
      });
    });
    if (interfaces.size > 0) {
      const ifaceColors = ['#0ea5e9', '#22c55e', '#eab308', '#ef4444', '#a855f7'];
      const datasets: Array<{
        label: string;
        color: string;
        extractor: (point: MetricSeriesPoint) => number | null;
      }> = [];
      Array.from(interfaces).forEach((iface, idx) => {
        const color = ifaceColors[idx % ifaceColors.length];
        datasets.push({
          label: `${iface} TX`,
          color,
          extractor: (point: MetricSeriesPoint) =>
            point.network_bps?.find((entry) => entry.interface === iface)?.tx_bps ?? null,
        });
        datasets.push({
          label: `${iface} RX`,
          color: '#38bdf8',
          extractor: (point: MetricSeriesPoint) =>
            point.network_bps?.find((entry) => entry.interface === iface)?.rx_bps ?? null,
        });
      });
      charts.push({
        key: 'network-bps',
        title: 'Network Throughput (bps)',
        datasets,
        suggestedMin: 0,
        suggestedMax: undefined,
        emptyMessage: 'Waiting for network samples.',
      });
    }

    return charts;
  }, [selectedMetrics, series]);

  const mountCharts = useMemo(
    () =>
      selectedMounts.map((mount, index) => {
        const color = MOUNT_COLORS[index % MOUNT_COLORS.length];
        const label = mount.label || mount.path || `Mount ${index + 1}`;
        return {
          key: `mount-${mount.path || mount.label || index}`,
          title: label,
          datasets: [
            {
              label: '% used',
              color,
              extractor: (point: MetricSeriesPoint) => {
                const match = point.mounted_usage?.find((volume) => volume.mount_point === mount.path);
                return match?.used_percent ?? null;
              },
            },
          ],
          suggestedMin: 0,
          suggestedMax: 100,
          emptyMessage: `Waiting for ${label} samples.`,
        };
      }),
    [selectedMounts]
  );

  const miniCharts = useMemo(() => [...baseCharts, ...mountCharts], [baseCharts, mountCharts]);

  const handleRangeChange = (nextRange: MetricRange) => {
    setRange(nextRange);
    setRangeOffsets((prev) => ({ ...prev, [nextRange]: 0 }));
    setWindowBounds(null);
    setNavigationOffsets({ previous: null, next: null });
  };

  const handleNavigateTo = (targetOffset: number | null | undefined) => {
    if (targetOffset === null || targetOffset === undefined) {
      return;
    }
    setRangeOffsets((prev) => ({ ...prev, [range]: Math.max(0, targetOffset) }));
  };

  const canGoPrevious = navigationOffsets.previous !== null && navigationOffsets.previous > currentOffset;
  const canGoNext = navigationOffsets.next !== null && navigationOffsets.next < currentOffset;

  return (
    <div className={classNames('card shadow-sm border-0 overflow-hidden', { 'border border-danger': hasWarnings })}>
      <div className="card-header bg-dark text-light">
        <div className="d-flex flex-column flex-md-row gap-2 align-items-start align-items-md-center w-100">
          <div className="flex-grow-1">
            <span className="fw-semibold me-2 text-uppercase">{backend.name}</span>
            <small className="text-secondary">{backend.base_url}</small>
          </div>
          <div className="d-flex flex-wrap align-items-center justify-content-end gap-2 w-100 w-md-auto">
            <button
              type="button"
              className={classNames('btn btn-sm', hidden ? 'btn-light text-dark' : 'btn-outline-light')}
              onClick={() => onToggleHidden(backend.id)}
            >
              {hidden ? 'Show' : 'Hide'}
            </button>
            {hasWarnings && (
              <span className="badge bg-danger d-flex align-items-center gap-1">
                <FiAlertTriangle /> Warning
              </span>
            )}
            <span className="badge bg-secondary text-break">Last seen: {lastSeen}</span>
            {backend.latest_snapshot?.backend_version && (
              <span className="badge bg-secondary text-break">Monitor v{backend.latest_snapshot.backend_version}</span>
            )}
            {onRefresh && (
              <button
                className="btn btn-sm btn-outline-light"
                onClick={() => onRefresh(backend)}
                disabled={disabled}
              >
                Refresh
              </button>
            )}
          </div>
        </div>
      </div>
      {hidden ? (
        <div className="card-body bg-dark text-light">
          <div className="text-secondary fst-italic">Metrics hidden for this backend. Press Show to display details.</div>
        </div>
      ) : (
        <>
          <div className="card-body bg-dark text-light">
            <div className="d-flex flex-column gap-3">
              <div className="d-flex flex-wrap justify-content-between gap-2 align-items-center">
                <div className="d-flex flex-wrap align-items-center gap-2">
                  <div className="btn-group">
                    {RANGE_OPTIONS.map((option) => (
                      <button
                        key={option.value}
                        type="button"
                        className={classNames('btn btn-sm', {
                          'btn-light text-dark': range === option.value,
                          'btn-outline-light': range !== option.value,
                        })}
                        onClick={() => handleRangeChange(option.value)}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                  <div className="btn-group" role="group" aria-label="Navigate time windows">
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-light"
                      onClick={() => handleNavigateTo(navigationOffsets.previous)}
                      disabled={!canGoPrevious || seriesLoading}
                    >
                      <FiArrowLeft /> Earlier
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-light"
                      onClick={() => handleNavigateTo(navigationOffsets.next)}
                      disabled={!canGoNext || seriesLoading}
                    >
                      Later <FiArrowRight />
                    </button>
                  </div>
                </div>
                <div className="d-flex flex-column align-items-end">
                  {windowLabel && <span className="small text-secondary">Window: {windowLabel}</span>}
                  {/* Latest sample display removed by request */}
                </div>
              </div>
              <div className="row row-cols-1 row-cols-md-2 row-cols-xl-4 g-3">
                {miniCharts.map((chart) => (
                  <div className="col" key={chart.key}>
                    <div className="card-panel rounded-4 p-3 h-100 bg-dark bg-opacity-25">
                      <MetricsChart
                        title={chart.title}
                        range={range}
                        points={series}
                        loading={seriesLoading}
                        error={seriesError}
                        datasets={chart.datasets}
                        suggestedMin={chart.suggestedMin}
                        suggestedMax={chart.suggestedMax}
                        emptyMessage={chart.emptyMessage}
                        pollIntervalSeconds={backend.poll_interval_seconds}
                        windowStart={windowBounds?.start}
                        windowEnd={windowBounds?.end}
                        markers={rebootMarkers}
                      />
                    </div>
                  </div>
                ))}
              </div>
              {showMountNotice && (
                <p className="card-panel__muted small mb-0">
                  No mount points selected for this backend. Configure them in the admin panel.
                </p>
              )}
              {snapshot && (
                <div className="row g-3">
                  <div className="col-12 col-lg-6">
                    <div className="card-panel rounded-4 p-3 h-100">
                      <h6 className="text-uppercase card-panel__heading fw-semibold mb-2">Latest Snapshot</h6>
                      <ul className="list-unstyled small mb-0">
                        <li>
                          CPU temp:{' '}
                          {snapshot.cpu_temperature_c !== undefined && snapshot.cpu_temperature_c !== null
                            ? `${snapshot.cpu_temperature_c.toFixed(1)} °C`
                            : 'N/A'}
                        </li>
                        <li>
                          RAM used:{' '}
                          {snapshot.ram_used_percent !== undefined && snapshot.ram_used_percent !== null
                            ? `${snapshot.ram_used_percent.toFixed(1)}%`
                            : 'N/A'}
                        </li>
                        <li>
                          Disk used:{' '}
                          {snapshot.disk_usage_percent !== undefined && snapshot.disk_usage_percent !== null
                            ? `${snapshot.disk_usage_percent.toFixed(1)}%`
                            : 'N/A'}
                        </li>
                        <li>
                          CPU load:{' '}
                          {snapshot.cpu_load
                            ? `${snapshot.cpu_load.one?.toFixed(2) ?? '-'} / ${snapshot.cpu_load.five?.toFixed(2) ?? '-'} / ${
                                snapshot.cpu_load.fifteen?.toFixed(2) ?? '-'
                              }`
                            : 'N/A'}
                        </li>
                        <li>Monitor version: {snapshot.backend_version ?? 'N/A'}</li>
                        <li>OS version: {snapshot.os_version ?? 'N/A'}</li>
                        <li>Uptime: {uptime ?? 'N/A'}</li>
                      </ul>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
          {hasWarnings && (
            <div className="alert alert-danger mt-4 mb-0">
              <h6 className="fw-semibold d-flex align-items-center gap-2">
                <FiAlertTriangle /> Active Warnings
              </h6>
              <ul className="mb-0 small">
                {warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}
