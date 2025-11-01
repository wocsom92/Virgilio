import { useMemo } from 'react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Legend,
  TimeScale,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import 'chartjs-adapter-date-fns';

import type { MetricRange, MetricSeriesPoint } from '../api/client';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend, TimeScale);

interface DatasetConfig {
  label: string;
  color: string;
  extractor: (point: MetricSeriesPoint) => number | null;
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
  windowStart?: number | null;
  windowEnd?: number | null;
  markers?: number[] | null;
}

const RANGE_TO_DURATION_MS: Record<MetricRange, number> = {
  hourly: 60 * 60 * 1000,
  daily: 24 * 60 * 60 * 1000,
  weekly: 7 * 24 * 60 * 60 * 1000,
};

function parseTimestamp(value: string): number {
  if (!value) {
    return Number.NaN;
  }
  const hasTimezone = /[zZ]|[+-]\d{2}:\d{2}$/.test(value);
  const normalized = hasTimezone ? value : `${value}Z`;
  return new Date(normalized).getTime();
}

export function MetricsChart({
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
  windowStart,
  windowEnd,
  markers = null,
}: MetricsChartProps) {
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

    const expectedIntervalMs = Math.max(pollIntervalSeconds ?? 60, 30) * 1000;
    const gapThresholdMs = expectedIntervalMs * 2;

    let hasAnyValues = false;

    const configuredDatasets = datasets.map((dataset) => {
      const data: Array<{ x: number; y: number | null }> = [];
      let previousTimestamp: number | null = null;
      let firstSampleTimestamp: number | null = null;
      let lastSampleTimestamp: number | null = null;

      normalized.forEach(({ point, timestamp }) => {
        const value = dataset.extractor(point);
        if (previousTimestamp !== null && timestamp - previousTimestamp > gapThresholdMs) {
          const gapMarker = Math.max(windowStartValue, previousTimestamp + expectedIntervalMs);
          data.push({ x: gapMarker, y: null });
        }

        data.push({ x: timestamp, y: value ?? null });

        if (value !== null && value !== undefined) {
          hasAnyValues = true;
          if (firstSampleTimestamp === null) {
            firstSampleTimestamp = timestamp;
          }
          lastSampleTimestamp = timestamp;
        } else {
          if (firstSampleTimestamp === null) {
            firstSampleTimestamp = timestamp;
          }
          lastSampleTimestamp = timestamp;
        }
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

      return {
        label: dataset.label,
        data,
        borderColor: dataset.color,
        backgroundColor: dataset.color,
        borderWidth: 2,
        pointRadius: 0,
        spanGaps: false,
        parsing: false,
      };
    });

    return {
      datasets: configuredDatasets,
      hasAnyValues,
      window: { start: windowStartValue, end: windowEndValue },
    };
  }, [datasets, points, range, pollIntervalSeconds, windowEnd, windowStart]);

  const options = useMemo(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          suggestedMin,
          suggestedMax,
          ticks: {
            color: '#adb5bd',
            callback: (value: string | number) => `${value}`,
          },
          grid: {
            color: 'rgba(255,255,255,0.08)',
          },
        },
        x: {
          type: 'time' as const,
          min: processed.window.start,
          max: processed.window.end,
          ticks: {
            color: '#adb5bd',
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 8,
          },
          grid: {
            display: false,
          },
        },
      },
      plugins: {
        legend: {
          display: datasets.length > 1,
          labels: {
            color: '#f8f9fa',
          },
          position: 'bottom' as const,
        },
        tooltip: {
          intersect: false,
          mode: 'index' as const,
        },
      },
      elements: {
        line: {
          tension: 0.3,
        },
      },
    }),
    [datasets.length, suggestedMin, suggestedMax, processed.window.end, processed.window.start]
  );

  const markerPlugin = useMemo(
    () => ({
      id: `markers-${title}`,
      afterDraw: (chart: any) => {
        if (!markers || markers.length === 0) return;
        const {
          ctx,
          scales: { x },
          chartArea,
        } = chart;
        ctx.save();
        ctx.strokeStyle = 'rgba(248,113,113,0.6)';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        markers.forEach((timestamp) => {
          const xPos = x.getPixelForValue(timestamp);
          ctx.beginPath();
          ctx.moveTo(xPos, chartArea.top);
          ctx.lineTo(xPos, chartArea.bottom);
          ctx.stroke();
        });
        ctx.restore();
      },
    }),
    [markers, title]
  );

  if (loading) {
    return <p className="text-secondary small mb-0">Loading {title}…</p>;
  }

  if (error) {
    return (
      <div className="alert alert-warning small py-2 mb-0">
        Unable to load {title.toLowerCase()}: {error}
      </div>
    );
  }

  return (
    <div className="d-flex flex-column gap-2">
      <h6 className="text-uppercase small fw-semibold mb-0 text-secondary">{title}</h6>
      <div style={{ minHeight: 160 }}>
        <Line
          options={options}
          data={{
            datasets: processed.datasets,
          }}
          plugins={[markerPlugin]}
        />
      </div>
      {!processed.hasAnyValues && (
        <p className="text-secondary small mb-0">{emptyMessage}</p>
      )}
    </div>
  );
}
