import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

let lastLineProps: any = null;

vi.mock('react-chartjs-2', () => ({
  Line: (props: any) => {
    lastLineProps = props;
    return <div data-testid="line-chart" />;
  },
}));

import { MetricsChart } from './MetricsChart';

const datasets = [{ label: '%', color: '#000', extractor: (point: { ram_used_percent?: number | null }) => point.ram_used_percent ?? null }];

describe('MetricsChart', () => {
  beforeEach(() => {
    lastLineProps = null;
  });

  it('shows the loading text only before the first data arrives', () => {
    render(<MetricsChart title="RAM" range="hourly" points={[]} loading error={null} datasets={datasets} />);
    expect(screen.getByText('Loading RAM…')).toBeInTheDocument();
    expect(screen.queryByTestId('line-chart')).not.toBeInTheDocument();
  });

  it('keeps the chart mounted while a refresh is in flight', () => {
    const points = [{ reported_at: new Date().toISOString(), ram_used_percent: 40 }];
    const { rerender } = render(
      <MetricsChart title="RAM" range="hourly" points={points} loading={false} error={null} datasets={datasets} />
    );
    const chart = screen.getByTestId('line-chart');

    rerender(<MetricsChart title="RAM" range="hourly" points={points} loading error={null} datasets={datasets} />);
    expect(screen.getByTestId('line-chart')).toBe(chart);
    expect(screen.queryByText('Loading RAM…')).not.toBeInTheDocument();
  });

  it('does not break a downsampled series into gaps between buckets', () => {
    const end = Date.UTC(2026, 9, 24, 12, 0, 0);
    const bucketSeconds = 288; // a daily window of 1-minute samples averaged into 300 buckets
    const points = Array.from({ length: 10 }, (_, index) => ({
      reported_at: new Date(end - (9 - index) * bucketSeconds * 1000).toISOString(),
      ram_used_percent: 40 + index,
    }));

    render(
      <MetricsChart
        title="RAM"
        range="daily"
        points={points}
        loading={false}
        error={null}
        datasets={datasets}
        pollIntervalSeconds={60}
        bucketSeconds={bucketSeconds}
        windowStart={end - 24 * 60 * 60 * 1000}
        windowEnd={end}
      />
    );

    const data = lastLineProps.data.datasets[0].data as Array<{ x: number; y: number | null }>;
    const samples = data.filter((entry) => entry.y !== null);
    expect(samples).toHaveLength(10);
    // Only the leading window gap is a null; nothing is inserted between consecutive buckets.
    expect(data.filter((entry) => entry.y === null)).toHaveLength(1);
    expect(data[0]).toEqual({ x: end - 24 * 60 * 60 * 1000, y: null });
    expect(screen.getByText('49')).toBeInTheDocument(); // latest readout
  });

  it('still marks real gaps when the poll interval is the spacing', () => {
    const end = Date.UTC(2026, 9, 24, 12, 0, 0);
    const points = [
      { reported_at: new Date(end - 10 * 60 * 1000).toISOString(), ram_used_percent: 10 },
      { reported_at: new Date(end - 9 * 60 * 1000).toISOString(), ram_used_percent: 11 },
      { reported_at: new Date(end - 60 * 1000).toISOString(), ram_used_percent: 12 },
      { reported_at: new Date(end).toISOString(), ram_used_percent: 13 },
    ];
    render(
      <MetricsChart
        title="RAM"
        range="hourly"
        points={points}
        loading={false}
        error={null}
        datasets={datasets}
        pollIntervalSeconds={60}
        windowStart={end - 60 * 60 * 1000}
        windowEnd={end}
      />
    );
    const data = lastLineProps.data.datasets[0].data as Array<{ x: number; y: number | null }>;
    const gaps = data.filter((entry) => entry.y === null);
    expect(gaps).toHaveLength(2); // leading window gap + the 8-minute hole
  });

  it('adds a right-hand axis and step styling for a secondary dataset', () => {
    const end = Date.UTC(2026, 9, 24, 12, 0, 0);
    const points = [
      { reported_at: new Date(end - 60 * 1000).toISOString(), cpu_temperature_c: 50, fan_level: 1 },
      { reported_at: new Date(end).toISOString(), cpu_temperature_c: 55, fan_level: 2 },
    ];
    render(
      <MetricsChart
        title="CPU Temperature"
        range="hourly"
        points={points}
        loading={false}
        error={null}
        datasets={[
          { label: 'CPU', color: '#f97316', extractor: (point: any) => point.cpu_temperature_c ?? null },
          {
            label: 'Fan level',
            color: '#64748b',
            extractor: (point: any) => point.fan_level ?? null,
            axis: 'y1',
            stepped: true,
            dashed: true,
          },
        ]}
        formatValue={(value) => `${value} °C`}
        secondaryAxis={{ min: 0, max: 4 }}
        annotations={[{ timestamp: end, label: 'Fan 2' }]}
        windowStart={end - 60 * 60 * 1000}
        windowEnd={end}
      />
    );
    expect(lastLineProps.options.scales.y1).toMatchObject({ position: 'right', min: 0, max: 4 });
    const fanDataset = lastLineProps.data.datasets[1];
    expect(fanDataset).toMatchObject({ yAxisID: 'y1', stepped: 'before', borderDash: [4, 3], fill: 'origin' });
    expect(lastLineProps.data.datasets[0].fill).toBe('origin');
    expect(screen.getByText('55 °C')).toBeInTheDocument();
  });
});
