import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('react-chartjs-2', () => ({
  Line: () => <div data-testid="line-chart" />,
}));

import { MetricsChart } from './MetricsChart';

const datasets = [{ label: '%', color: '#000', extractor: (point: { ram_used_percent?: number | null }) => point.ram_used_percent ?? null }];

describe('MetricsChart', () => {
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
});
