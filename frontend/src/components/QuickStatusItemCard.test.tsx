import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { QuickStatusTile } from '../api/client';
import { QuickStatusItemCard } from './QuickStatusItemCard';

function makeQuickStatusTile(overrides: Partial<QuickStatusTile> = {}): QuickStatusTile {
  return {
    id: overrides.id ?? 1,
    backend_id: overrides.backend_id ?? 1,
    project_id: overrides.project_id ?? null,
    backend_display_order: overrides.backend_display_order ?? 1,
    backend_name: overrides.backend_name ?? 'Home',
    label: overrides.label ?? 'Bedroom',
    metric_key: overrides.metric_key ?? 'prometheus_query',
    display_variant: overrides.display_variant ?? 'temperature',
    value: overrides.value ?? 22.6,
    display_value: overrides.display_value ?? '22.6°C',
    display_unit: overrides.display_unit ?? '°C',
    status: overrides.status ?? 'ok',
    history_loaded: overrides.history_loaded ?? true,
    history: overrides.history ?? [],
    series:
      overrides.series ??
      [
        { recorded_at: '2024-01-01T00:00:00Z', value: null },
        { recorded_at: '2024-01-01T00:00:00Z', value: 21.2 },
        { recorded_at: '2024-01-01T12:00:00Z', value: 22.6 },
        { recorded_at: '2024-01-01T23:00:00Z', value: 23.1 },
        { recorded_at: '2024-01-02T00:00:00Z', value: null },
      ],
    series_summary:
      overrides.series_summary ??
      {
        min_value: 21.2,
        max_value: 23.1,
        average_value: 22.3,
        delta_value: 1.9,
      },
    reported_at: overrides.reported_at ?? '2024-01-01T23:00:00Z',
    stale: overrides.stale ?? false,
    show_updated_age: overrides.show_updated_age ?? false,
    highlights: overrides.highlights ?? [],
    breakdown: overrides.breakdown ?? [],
    details: overrides.details ?? [],
  };
}

describe('QuickStatusItemCard', () => {
  it('shows exact graph value only after probing the chart', () => {
    render(<QuickStatusItemCard tile={makeQuickStatusTile()} />);

    expect(screen.queryByTestId('quick-status-chart-probe')).not.toBeInTheDocument();

    const chart = screen.getByTestId('quick-status-chart');
    Object.defineProperty(chart, 'getBoundingClientRect', {
      value: () => ({
        left: 0,
        top: 0,
        width: 100,
        height: 52,
        right: 100,
        bottom: 52,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }),
    });

    fireEvent.touchStart(chart, { touches: [{ clientX: 100 }] });

    expect(screen.getByTestId('quick-status-chart-probe')).toHaveTextContent('23.1°C');
  });

  it('clears the chart probe when mouse leaves the chart', () => {
    render(<QuickStatusItemCard tile={makeQuickStatusTile()} />);

    const chart = screen.getByTestId('quick-status-chart');
    Object.defineProperty(chart, 'getBoundingClientRect', {
      value: () => ({
        left: 0,
        top: 0,
        width: 100,
        height: 52,
        right: 100,
        bottom: 52,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }),
    });

    fireEvent.mouseMove(chart, { clientX: 50 });
    expect(screen.getByTestId('quick-status-chart-probe')).toBeInTheDocument();

    fireEvent.mouseLeave(chart);
    expect(screen.queryByTestId('quick-status-chart-probe')).not.toBeInTheDocument();
  });

  it('renders self-monitoring dashboard highlights and endpoint breakdown', () => {
    const highlightTile = makeQuickStatusTile({
      display_variant: 'self_monitoring',
      display_value: '2.4k req/h',
      display_unit: 'req/h',
      highlights: [
        { label: 'Backend', value: '1.7k req/h', severity: 'info' },
        { label: '4xx+5xx', value: '3 req/h', severity: 'warn' },
      ],
    });

    const breakdownTile = makeQuickStatusTile({
      id: 2,
      display_variant: 'self_monitoring',
      display_value: '2.4k req/h',
      display_unit: 'req/h',
      breakdown: [
        { label: 'Backend /dashboard', value: '1.1k req/h', severity: 'info' },
        { label: 'Monitor /healthz', value: '220 req/h', severity: 'ok' },
      ],
    });

    const { rerender } = render(<QuickStatusItemCard tile={highlightTile} />);

    expect(screen.getByText('2.4k req/h')).toBeInTheDocument();
    expect(screen.getByText('1.7k req/h')).toBeInTheDocument();

    rerender(<QuickStatusItemCard tile={breakdownTile} />);

    expect(screen.getByText('Backend /dashboard')).toBeInTheDocument();
    expect(screen.getByText('Monitor /healthz')).toBeInTheDocument();
  });

  it('renders split line segments when the 24h series contains gaps', () => {
    const { container } = render(
      <QuickStatusItemCard
        tile={makeQuickStatusTile({
          series: [
            { recorded_at: '2024-01-01T00:00:00Z', value: null },
            { recorded_at: '2024-01-01T02:00:00Z', value: 21.2 },
            { recorded_at: '2024-01-01T06:00:00Z', value: 22.0 },
            { recorded_at: '2024-01-01T12:00:00Z', value: null },
            { recorded_at: '2024-01-01T18:00:00Z', value: 22.8 },
            { recorded_at: '2024-01-01T23:00:00Z', value: 23.1 },
            { recorded_at: '2024-01-02T00:00:00Z', value: null },
          ],
        })}
      />
    );

    expect(container.querySelectorAll('path[stroke="currentColor"]')).toHaveLength(2);
  });

  it('keeps flat 24h series visible instead of pinning them off-screen', () => {
    const { container } = render(
      <QuickStatusItemCard
        tile={makeQuickStatusTile({
          series: [
            { recorded_at: '2024-01-01T00:00:00Z', value: null },
            { recorded_at: '2024-01-01T08:00:00Z', value: 48.0 },
            { recorded_at: '2024-01-01T16:00:00Z', value: 48.0 },
            { recorded_at: '2024-01-02T00:00:00Z', value: null },
          ],
        })}
      />
    );

    expect(container.querySelector('path[stroke="currentColor"]')).toHaveAttribute('d', 'M33.33,26.00 L66.67,26.00');
  });

  it('renders isolated samples as visible dots when there is no drawable line segment yet', () => {
    const { container } = render(
      <QuickStatusItemCard
        tile={makeQuickStatusTile({
          series: [
            { recorded_at: '2024-01-01T00:00:00Z', value: null },
            { recorded_at: '2024-01-01T12:00:00Z', value: 48.0 },
            { recorded_at: '2024-01-02T00:00:00Z', value: null },
          ],
        })}
      />
    );

    expect(container.querySelectorAll('path[stroke="currentColor"]')).toHaveLength(0);
    expect(container.querySelectorAll('circle.quick-status-wide__chart-dot')).toHaveLength(1);
  });

  it('renders an empty 24h chart when the whole period is missing', () => {
    render(
      <QuickStatusItemCard
        tile={makeQuickStatusTile({
          series: [
            { recorded_at: '2024-01-01T00:00:00Z', value: null },
            { recorded_at: '2024-01-02T00:00:00Z', value: null },
          ],
          series_summary: null,
        })}
      />
    );

    expect(screen.getByTestId('quick-status-chart')).toBeInTheDocument();
    expect(screen.queryByText('Waiting for 24h history.')).not.toBeInTheDocument();
  });
});
