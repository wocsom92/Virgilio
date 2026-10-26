import { memo, useEffect, useMemo, useState } from 'react';
import {
  Chart as ChartJS,
  ChartData,
  ChartDataset,
  Filler,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Legend,
  TimeScale,
  type ChartOptions,
  type Plugin,
  type ScriptableContext,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import 'chartjs-adapter-date-fns';

import type { MetricRange, MetricSeriesPoint } from '../api/client';

type GapPoint = { x: number; y: number | null };

ChartJS.register(LinearScale, PointElement, LineElement, Tooltip, Legend, TimeScale, Filler);

export interface DatasetConfig {
  label: string;
  color: string;
  extractor: (point: MetricSeriesPoint) => number | null;
  /** `y` (default, left) or the secondary right-hand axis `y1`. */
  axis?: 'y' | 'y1';
  /** Draw as a step function (discrete levels such as the fan speed). */
  stepped?: boolean;
  dashed?: boolean;
  /** Value formatter for this dataset's tooltip entries; falls back to the chart's `formatValue`. */
  format?: (value: number) => string;
}

/** A labelled vertical marker, e.g. "Fan 2" where the cooling fan changed level. */
export interface ChartAnnotation {
  timestamp: number;
  label: string;
}

export interface SecondaryAxisConfig {
  min?: number;
  max?: number;
  format?: (value: number) => string;
}

interface MetricsChartProps {
  title: string;
  range: MetricRange;
  points: MetricSeriesPoint[];
  loading: boolean;
  error: string | null;
  datasets: DatasetConfig[];
  suggestedMin?: number;
  suggestedMax?: number;
  emptyMessage?: string;
  pollIntervalSeconds?: number;
  /** Seconds per averaged bucket when the backend downsampled the window (see `bucket_seconds`). */
  bucketSeconds?: number | null;
  windowStart?: number | null;
  windowEnd?: number | null;
  /** Reboot timestamps (ms). */
  markers?: number[] | null;
  annotations?: ChartAnnotation[] | null;
  formatValue?: (value: number) => string;
  /** Formatter for the left axis ticks when they should be terser than the values (e.g. "off", "1"). */
  formatAxis?: (value: number) => string;
  secondaryAxis?: SecondaryAxisConfig | null;
}

const RANGE_TO_DURATION_MS: Record<MetricRange, number> = {
  hourly: 60 * 60 * 1000,
  daily: 24 * 60 * 60 * 1000,
  weekly: 7 * 24 * 60 * 60 * 1000,
};

const X_TICK_LIMIT: Record<MetricRange, number> = { hourly: 6, daily: 6, weekly: 7 };
/** Minimum gap between neighbouring time labels; Chart.js's default (3px) lets them touch. */
const X_TICK_PADDING_PX = 16;

const DEFAULT_FORMAT = (value: number) => (Number.isInteger(value) ? `${value}` : value.toFixed(1));

function parseTimestamp(value: string): number {
  if (!value) {
    return Number.NaN;
  }
  const hasTimezone = /[zZ]|[+-]\d{2}:\d{2}$/.test(value);
  const normalized = hasTimezone ? value : `${value}Z`;
  return new Date(normalized).getTime();
}

function withAlpha(color: string, alpha: number): string {
  const hex = color.trim();
  const match = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex);
  if (!match) {
    return color;
  }
  let digits = match[1];
  if (digits.length === 3) {
    digits = digits
      .split('')
      .map((char) => char + char)
      .join('');
  }
  const r = parseInt(digits.slice(0, 2), 16);
  const g = parseInt(digits.slice(2, 4), 16);
  const b = parseInt(digits.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

interface ChartPalette {
  grid: string;
  tick: string;
  marker: string;
  text: string;
  text2: string;
  surface: string;
  border: string;
  font: string;
}

const FALLBACK_PALETTE: ChartPalette = {
  grid: 'rgba(15, 23, 42, 0.08)',
  tick: '#64748b',
  marker: 'rgba(220, 38, 38, 0.6)',
  text: '#0f172a',
  text2: '#475569',
  surface: '#ffffff',
  border: '#dde3ec',
  font: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
};

function readPalette(): ChartPalette {
  if (typeof window === 'undefined' || typeof window.getComputedStyle !== 'function') {
    return FALLBACK_PALETTE;
  }
  const styles = window.getComputedStyle(document.documentElement);
  const read = (name: string, fallback: string) => styles.getPropertyValue(name).trim() || fallback;
  return {
    grid: read('--chart-grid', FALLBACK_PALETTE.grid),
    tick: read('--chart-tick', FALLBACK_PALETTE.tick),
    marker: read('--chart-marker', FALLBACK_PALETTE.marker),
    text: read('--text', FALLBACK_PALETTE.text),
    text2: read('--text-2', FALLBACK_PALETTE.text2),
    surface: read('--surface', FALLBACK_PALETTE.surface),
    border: read('--border', FALLBACK_PALETTE.border),
    font: read('--font-sans', FALLBACK_PALETTE.font),
  };
}

/** Chart.js cannot read CSS variables, so resolve the theme tokens and follow data-theme changes. */
function useChartPalette(): ChartPalette {
  const [palette, setPalette] = useState<ChartPalette>(() => readPalette());
  useEffect(() => {
    if (typeof MutationObserver === 'undefined') {
      return;
    }
    const observer = new MutationObserver(() => setPalette(readPalette()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);
  return palette;
}

function MetricsChartComponent({
  title,
  range,
  points,
  loading,
  error,
  datasets,
  suggestedMin,
  suggestedMax,
  emptyMessage = 'No data available.',
  pollIntervalSeconds,
  bucketSeconds = null,
  windowStart,
  windowEnd,
  markers = null,
  annotations = null,
  formatValue = DEFAULT_FORMAT,
  formatAxis,
  secondaryAxis = null,
}: MetricsChartProps) {
  const palette = useChartPalette();

  const processed = useMemo(() => {
    const durationMs = RANGE_TO_DURATION_MS[range];
    const windowEndValue = windowEnd ?? Date.now();
    const windowStartValue = windowStart ?? windowEndValue - durationMs;
    const normalized = points
      .map((point) => ({
        point,
        timestamp: parseTimestamp(point.reported_at),
      }))
      .filter(
        ({ timestamp }) =>
          Number.isFinite(timestamp) && timestamp >= windowStartValue && timestamp <= windowEndValue
      )
      .sort((a, b) => a.timestamp - b.timestamp);

    // Downsampled windows carry one point per bucket, so the expected spacing is the bucket
    // size, not the poll interval; otherwise every bucket would look like a gap in the data.
    const expectedIntervalMs = Math.max(pollIntervalSeconds ?? 60, bucketSeconds ?? 0, 30) * 1000;
    const gapThresholdMs = expectedIntervalMs * 2;

    let hasAnyValues = false;
    let latest: number | null = null;
    const primaryCount = datasets.filter((dataset) => (dataset.axis ?? 'y') === 'y').length;

    const configuredDatasets = datasets.map((dataset, index) => {
      const data: Array<{ x: number; y: number | null }> = [];
      let previousTimestamp: number | null = null;
      let firstSampleTimestamp: number | null = null;
      let lastSampleTimestamp: number | null = null;
      let lastValue: number | null = null;

      normalized.forEach(({ point, timestamp }) => {
        const value = dataset.extractor(point);
        if (previousTimestamp !== null && timestamp - previousTimestamp > gapThresholdMs) {
          const gapMarker = Math.max(windowStartValue, previousTimestamp + expectedIntervalMs);
          data.push({ x: gapMarker, y: null });
        }

        data.push({ x: timestamp, y: value ?? null });

        if (value !== null && value !== undefined) {
          hasAnyValues = true;
          lastValue = value;
        }
        if (firstSampleTimestamp === null) {
          firstSampleTimestamp = timestamp;
        }
        lastSampleTimestamp = timestamp;
        previousTimestamp = timestamp;
      });

      if (data.length === 0) {
        data.push({ x: windowStartValue, y: null }, { x: windowEndValue, y: null });
      } else {
        if (firstSampleTimestamp !== null && firstSampleTimestamp - windowStartValue > gapThresholdMs) {
          data.unshift({ x: windowStartValue, y: null });
        }
        if (lastSampleTimestamp !== null && windowEndValue - lastSampleTimestamp > gapThresholdMs) {
          data.push({ x: windowEndValue, y: null });
        }
      }

      if (index === 0) {
        latest = lastValue;
      }

      const onPrimaryAxis = (dataset.axis ?? 'y') === 'y';
      // A lone primary series (stepped or not) gets a gradient fill; a stepped secondary series
      // becomes a faint band behind the data so it reads as context rather than competing with it.
      const bandFill = !onPrimaryAxis && !!dataset.stepped;
      const filled = (onPrimaryAxis && primaryCount === 1) || bandFill;
      const fillColor = (context: ScriptableContext<'line'>) => {
        const { chart } = context;
        const area = chart.chartArea;
        if (!area || typeof chart.ctx?.createLinearGradient !== 'function') {
          return withAlpha(dataset.color, 0.12);
        }
        const gradient = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
        gradient.addColorStop(0, withAlpha(dataset.color, 0.28));
        gradient.addColorStop(1, withAlpha(dataset.color, 0.02));
        return gradient;
      };

      return {
        label: dataset.label,
        data,
        borderColor: dataset.color,
        backgroundColor: bandFill ? withAlpha(dataset.color, 0.12) : filled ? fillColor : dataset.color,
        fill: filled ? 'origin' : false,
        borderWidth: bandFill ? 1 : 1.75,
        borderDash: dataset.dashed ? [4, 3] : undefined,
        borderCapStyle: 'round' as const,
        borderJoinStyle: 'round' as const,
        stepped: dataset.stepped ? ('before' as const) : false,
        pointRadius: 0,
        pointHoverRadius: 3,
        pointHitRadius: 8,
        pointHoverBackgroundColor: dataset.color,
        pointHoverBorderColor: palette.surface,
        pointHoverBorderWidth: 1.5,
        yAxisID: dataset.axis ?? 'y',
        spanGaps: false,
        parsing: false as const,
      } satisfies ChartDataset<'line', GapPoint[]>;
    });

    return {
      datasets: configuredDatasets,
      hasAnyValues,
      latest,
      window: { start: windowStartValue, end: windowEndValue },
    };
  }, [datasets, points, range, pollIntervalSeconds, bucketSeconds, windowEnd, windowStart, palette.surface]);

  const hasSecondaryAxis = useMemo(() => datasets.some((dataset) => dataset.axis === 'y1'), [datasets]);
  const formatByLabel = useMemo(
    () => new Map(datasets.map((dataset) => [dataset.label, dataset.format ?? formatValue] as const)),
    [datasets, formatValue]
  );

  const options = useMemo<ChartOptions<'line'>>(() => {
    const tickFont = { family: palette.font, size: 11 };
    return {
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: { top: 10, right: hasSecondaryAxis ? 0 : 6, bottom: 0, left: 0 } },
      scales: {
        y: {
          position: 'left',
          suggestedMin,
          suggestedMax,
          border: { display: false },
          grid: { color: palette.grid, drawTicks: false },
          ticks: {
            color: palette.tick,
            font: tickFont,
            maxTicksLimit: 5,
            padding: 6,
            callback: (value) => (formatAxis ?? formatValue)(Number(value)),
          },
        },
        ...(hasSecondaryAxis
          ? {
              y1: {
                position: 'right' as const,
                min: secondaryAxis?.min ?? 0,
                max: secondaryAxis?.max,
                border: { display: false },
                grid: { drawOnChartArea: false, drawTicks: false },
                ticks: {
                  color: palette.tick,
                  font: tickFont,
                  stepSize: 1,
                  precision: 0,
                  padding: 6,
                  callback: (value) => (secondaryAxis?.format ?? DEFAULT_FORMAT)(Number(value)),
                },
              },
            }
          : {}),
        x: {
          type: 'time',
          min: processed.window.start,
          max: processed.window.end,
          time: {
            displayFormats: { minute: 'HH:mm', hour: 'HH:mm', day: 'EEE d MMM' },
            tooltipFormat: 'EEE d MMM, HH:mm',
          },
          border: { color: palette.grid },
          grid: { display: false },
          ticks: {
            color: palette.tick,
            font: tickFont,
            maxRotation: 0,
            autoSkip: true,
            autoSkipPadding: X_TICK_PADDING_PX,
            maxTicksLimit: X_TICK_LIMIT[range],
            padding: 4,
          },
        },
      },
      plugins: {
        legend: {
          display: datasets.length > 1,
          position: 'bottom',
          align: 'start',
          labels: {
            color: palette.tick,
            usePointStyle: true,
            pointStyle: 'line',
            boxWidth: 16,
            boxHeight: 6,
            padding: 14,
            font: tickFont,
          },
        },
        tooltip: {
          intersect: false,
          mode: 'index',
          backgroundColor: palette.surface,
          titleColor: palette.text,
          bodyColor: palette.text2,
          borderColor: palette.border,
          borderWidth: 1,
          padding: 10,
          cornerRadius: 8,
          boxPadding: 4,
          usePointStyle: true,
          titleFont: { family: palette.font, size: 11, weight: 'bold' },
          bodyFont: { family: palette.font, size: 12 },
          filter: (item) => item.parsed.y !== null && item.parsed.y !== undefined,
          callbacks: {
            label: (item) => {
              const value = item.parsed.y;
              if (value === null || value === undefined) {
                return '';
              }
              const format = formatByLabel.get(item.dataset.label ?? '') ?? formatValue;
              return ` ${item.dataset.label}: ${format(value)}`;
            },
          },
        },
        filler: { propagate: false },
      },
      elements: {
        line: { tension: 0.2 },
      },
      // Pi-friendly: no animation frames, cheap hover lookup.
      animation: false,
      normalized: true,
      interaction: {
        mode: 'nearest',
        axis: 'x',
        intersect: false,
      },
    };
  }, [
    datasets.length,
    suggestedMin,
    suggestedMax,
    processed.window.end,
    processed.window.start,
    palette,
    hasSecondaryAxis,
    secondaryAxis,
    formatValue,
    formatAxis,
    formatByLabel,
    range,
  ]);

  const markerPlugin = useMemo<Plugin<'line'>>(
    () => ({
      id: 'virgilio-markers',
      afterDatasetsDraw: (chart) => {
        const hasMarkers = markers && markers.length > 0;
        const hasAnnotations = annotations && annotations.length > 0;
        if (!hasMarkers && !hasAnnotations) return;
        const {
          ctx,
          scales: { x },
          chartArea,
        } = chart;
        if (!x || !chartArea) return;

        ctx.save();
        ctx.font = `600 10px ${palette.font}`;
        ctx.textBaseline = 'middle';
        let lastLabelRight = Number.NEGATIVE_INFINITY;

        const drawLabel = (xPos: number, text: string, color: string) => {
          const width = ctx.measureText(text).width + 8;
          let left = xPos + 3;
          if (left + width > chartArea.right) {
            left = xPos - width - 3;
          }
          if (left < lastLabelRight + 4) {
            return; // too dense to label; the line itself still shows the event
          }
          const top = chartArea.top + 2;
          ctx.fillStyle = palette.surface;
          ctx.strokeStyle = color;
          ctx.lineWidth = 1;
          ctx.setLineDash([]);
          ctx.beginPath();
          ctx.roundRect(left, top, width, 14, 4);
          ctx.fill();
          ctx.stroke();
          ctx.fillStyle = color;
          ctx.fillText(text, left + 4, top + 7);
          lastLabelRight = left + width;
        };

        // Reboots get a full-height dashed line; annotations (fan switches) only a short tick under
        // the label, since the stepped band already shows the level and full lines would clutter
        // busy windows.
        const events: Array<{ timestamp: number; label: string | null; color: string; dash: number[]; full: boolean }> =
          [];
        (annotations ?? []).forEach((annotation) =>
          events.push({ timestamp: annotation.timestamp, label: annotation.label, color: palette.tick, dash: [], full: false })
        );
        (markers ?? []).forEach((timestamp) =>
          events.push({ timestamp, label: 'reboot', color: palette.marker, dash: [4, 4], full: true })
        );
        events.sort((a, b) => a.timestamp - b.timestamp);

        events.forEach((event) => {
          if (event.timestamp < x.min || event.timestamp > x.max) return;
          const xPos = x.getPixelForValue(event.timestamp);
          ctx.strokeStyle = event.color;
          ctx.lineWidth = 1;
          ctx.setLineDash(event.dash);
          ctx.beginPath();
          ctx.moveTo(xPos, chartArea.top);
          ctx.lineTo(xPos, event.full ? chartArea.bottom : chartArea.top + 20);
          ctx.stroke();
          if (event.label) {
            drawLabel(xPos, event.label, event.color);
          }
        });
        ctx.restore();
      },
    }),
    [markers, annotations, palette]
  );
  const plugins = useMemo(() => [markerPlugin], [markerPlugin]);
  const chartData = useMemo(
    () => ({ datasets: processed.datasets }) satisfies ChartData<'line', GapPoint[]>,
    [processed.datasets]
  );

  // Keep the canvas mounted while a refresh is in flight: destroying and recreating a chart
  // every poll is the single most expensive thing this page used to do.
  const hasPoints = points.length > 0;
  if (loading && !hasPoints) {
    return <p className="text-secondary small mb-0">Loading {title}…</p>;
  }

  if (error && !hasPoints) {
    return <div className="alert alert-warning small py-2 mb-0">{error}</div>;
  }

  const latestText = processed.latest !== null ? formatValue(processed.latest) : null;

  return (
    <div className="d-flex flex-column gap-2 metrics-chart" aria-busy={loading}>
      <div className="metrics-chart__header">
        <h6 className="metrics-chart__title">{title}</h6>
        {latestText !== null && (
          <span className="metrics-chart__latest" title="Latest sample">
            {latestText}
          </span>
        )}
      </div>
      <div className="metrics-chart__canvas" style={{ minHeight: 180, position: 'relative' }}>
        {loading ? <div className="metrics-chart__progress" aria-hidden="true" /> : null}
        <Line options={options} data={chartData} plugins={plugins} />
      </div>
      {!processed.hasAnyValues && !loading && <p className="text-secondary small mb-0">{emptyMessage}</p>}
      {error ? <p className="text-warning small mb-0">{error}</p> : null}
    </div>
  );
}

export const MetricsChart = memo(MetricsChartComponent);
