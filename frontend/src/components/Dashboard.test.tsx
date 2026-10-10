import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { MonitoredBackend, QuickStatusTile } from '../api/client';
import { Dashboard } from './Dashboard';
import {
  fetchDashboard,
  fetchQuickStatusTileDetail,
  fetchQuickStatusTileHistory,
  fetchQuickStatusTiles,
  refreshBackend,
} from '../api/client';

vi.mock('../api/client', () => ({
  fetchDashboard: vi.fn(),
  fetchQuickStatusTileDetail: vi.fn(),
  fetchQuickStatusTileHistory: vi.fn(),
  fetchQuickStatusTiles: vi.fn(),
  refreshBackend: vi.fn(),
  isCancelledError: () => false,
}));

vi.mock('./BackendCard', () => ({
  BackendCard: ({ backend, hidden, onToggleHidden, onRefresh }: any) => (
    <div data-testid="backend-card">
      <span data-testid="backend-name">{backend.name}</span>
      <span data-testid={`backend-hidden-${backend.id}`}>{hidden ? 'hidden' : 'visible'}</span>
      <button type="button" aria-label={`toggle backend ${backend.id}`} onClick={() => onToggleHidden(backend.id)}>
        Toggle
      </button>
      {onRefresh && (
        <button type="button" aria-label={`refresh backend ${backend.id}`} onClick={() => onRefresh(backend)}>
          Refresh
        </button>
      )}
      <span data-testid={`backend-snapshot-${backend.id}`}>{backend.latest_snapshot?.reported_at ?? 'none'}</span>
    </div>
  ),
}));

function makeBackend(overrides: Partial<MonitoredBackend> = {}): MonitoredBackend {
  return {
    id: overrides.id ?? 1,
    name: overrides.name ?? 'Backend',
    base_url: overrides.base_url ?? 'http://example',
    api_token: overrides.api_token ?? 'token',
    is_active: overrides.is_active ?? true,
    display_order: overrides.display_order ?? 1,
    poll_interval_seconds: overrides.poll_interval_seconds ?? 60,
    notes: overrides.notes ?? null,
    selected_metrics: overrides.selected_metrics ?? null,
    last_seen_at: overrides.last_seen_at ?? null,
    last_warning: overrides.last_warning ?? null,
    latest_snapshot: overrides.latest_snapshot ?? null,
  };
}

function makeQuickStatusTile(overrides: Partial<QuickStatusTile> = {}): QuickStatusTile {
  return {
    id: overrides.id ?? 1,
    backend_id: overrides.backend_id ?? 1,
    backend_display_order: overrides.backend_display_order ?? 1,
    backend_name: overrides.backend_name ?? 'Backend',
    label: overrides.label ?? 'Disk',
    metric_key: overrides.metric_key ?? 'disk_usage_percent',
    display_variant: overrides.display_variant ?? 'tile',
    value: overrides.value ?? 42,
    display_value: overrides.display_value ?? '42%',
    status: overrides.status ?? 'ok',
    history_loaded: overrides.history_loaded ?? true,
    has_details: overrides.has_details ?? false,
    history: overrides.history ?? Array.from({ length: 12 }, () => 'ok' as const),
    series: overrides.series ?? [],
    series_summary: overrides.series_summary ?? null,
    reported_at: overrides.reported_at ?? '2024-01-01T00:00:00Z',
  };
}

describe('Dashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    vi.mocked(fetchQuickStatusTiles).mockResolvedValue([]);
    vi.mocked(fetchQuickStatusTileHistory).mockResolvedValue([]);
    vi.mocked(fetchQuickStatusTileDetail).mockResolvedValue(makeQuickStatusTile());
  });

  it('renders sorted backend cards after fetching data', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue([
      makeBackend({ id: 2, name: 'Zulu', display_order: 2 }),
      makeBackend({ id: 1, name: 'Alpha', display_order: 1 }),
    ]);

    render(<Dashboard canRefresh={false} mode="graphs" />);

    expect(screen.getByText(/Loading metrics/i)).toBeInTheDocument();

    const names = await screen.findAllByTestId('backend-name');
    expect(names.map((node) => node.textContent)).toEqual(['Alpha', 'Zulu']);
    expect(screen.queryByText(/Loading metrics/i)).not.toBeInTheDocument();
  });

  it('toggles hidden state and refreshes a backend when allowed', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue([makeBackend({ id: 1, name: 'Gamma' })]);
    vi.mocked(refreshBackend).mockResolvedValue({
      reported_at: '2024-01-01T00:00:00Z',
    } as any);

    const user = userEvent.setup();
    render(<Dashboard canRefresh mode="graphs" />);

    await screen.findByTestId('backend-name');

    const hiddenIndicator = screen.getByTestId('backend-hidden-1');
    const toggleButton = screen.getByLabelText('toggle backend 1');
    await act(async () => {
      await user.click(toggleButton);
    });
    await waitFor(() => expect(hiddenIndicator).toHaveTextContent('hidden'));
    await act(async () => {
      await user.click(toggleButton);
    });
    await waitFor(() => expect(hiddenIndicator).toHaveTextContent('visible'));

    const refreshButton = screen.getByLabelText('refresh backend 1');
    await act(async () => {
      await user.click(refreshButton);
    });
    await waitFor(() => expect(refreshBackend).toHaveBeenCalledWith(1));
    await waitFor(() => expect(screen.getByTestId('backend-snapshot-1')).toHaveTextContent('2024-01-01T00:00:00Z'));
  });

  it('keeps quick status sections ordered by backend server order', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue([
      makeBackend({ id: 2, name: 'Zulu', display_order: 2 }),
      makeBackend({ id: 1, name: 'Alpha', display_order: 1 }),
    ]);
    vi.mocked(fetchQuickStatusTiles).mockResolvedValue([
      makeQuickStatusTile({ id: 10, backend_id: 2, backend_name: 'Zulu' }),
      makeQuickStatusTile({ id: 11, backend_id: 1, backend_name: 'Alpha' }),
    ]);

    const { container } = render(<Dashboard canRefresh={false} mode="monitoring" />);

    await waitFor(() => {
      const titles = Array.from(container.querySelectorAll('.quick-status-section__title')).map(
        (node) => node.textContent
      );
      expect(titles).toEqual(['Alpha', 'Zulu']);
    });
  });

  it('keeps quick status sections ordered by tile backend order when dashboard data is unavailable', async () => {
    vi.mocked(fetchDashboard).mockRejectedValue(new Error('dashboard unavailable'));
    vi.mocked(fetchQuickStatusTiles).mockResolvedValue([
      makeQuickStatusTile({ id: 10, backend_id: 2, backend_display_order: 2, backend_name: 'Zulu' }),
      makeQuickStatusTile({ id: 11, backend_id: 1, backend_display_order: 1, backend_name: 'Alpha' }),
    ]);

    const { container } = render(<Dashboard canRefresh={false} mode="monitoring" />);

    await waitFor(() => {
      const titles = Array.from(container.querySelectorAll('.quick-status-section__title')).map(
        (node) => node.textContent
      );
      expect(titles).toEqual(['Alpha', 'Zulu']);
    });
  });

  it('renders quick status history strips with 12 segments', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue([makeBackend({ id: 1, name: 'Alpha', display_order: 1 })]);
    vi.mocked(fetchQuickStatusTiles).mockResolvedValue([
      makeQuickStatusTile({
        id: -1,
        backend_id: 1,
        backend_display_order: 1,
        backend_name: 'Alpha',
        label: 'HB',
      }),
    ]);

    const { container } = render(<Dashboard canRefresh={false} mode="monitoring" />);

    await waitFor(() => {
      expect(container.querySelectorAll('.quick-status-history__segment')).toHaveLength(12);
    });
  });

  it('shows backend cards only in graphs view', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue([makeBackend({ id: 1, name: 'Alpha', display_order: 1 })]);
    vi.mocked(fetchQuickStatusTiles).mockResolvedValue([
      makeQuickStatusTile({ id: 10, backend_id: 1, backend_display_order: 1, backend_name: 'Alpha' }),
    ]);

    const { rerender } = render(<Dashboard canRefresh={false} mode="monitoring" />);

    await waitFor(() => {
      expect(screen.queryByTestId('backend-card')).not.toBeInTheDocument();
      expect(screen.getByText('Alpha')).toBeInTheDocument();
    });

    rerender(<Dashboard canRefresh={false} mode="graphs" />);

    await waitFor(() => {
      expect(screen.getByTestId('backend-card')).toBeInTheDocument();
    });
  });

  it('renders wide grid items for graph and temperature cards in monitoring view', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue([]);
    vi.mocked(fetchQuickStatusTiles).mockResolvedValue([
      makeQuickStatusTile({
        id: 15,
        backend_id: 1,
        backend_name: 'Home',
        metric_key: 'prometheus_query',
        display_variant: 'graph',
        series: [
          { recorded_at: '2024-01-01T00:00:00Z', value: 10 },
          { recorded_at: '2024-01-01T12:00:00Z', value: 14 },
        ],
        series_summary: {
          min_value: 10,
          max_value: 14,
          average_value: 12,
          delta_value: 4,
        },
      }),
      makeQuickStatusTile({
        id: 16,
        backend_id: 1,
        backend_name: 'Home',
        metric_key: 'prometheus_query',
        display_variant: 'temperature',
        display_value: '22.4 °C',
        series: [
          { recorded_at: '2024-01-01T00:00:00Z', value: 21.5 },
          { recorded_at: '2024-01-01T12:00:00Z', value: 22.4 },
        ],
      }),
    ]);

    const { container } = render(<Dashboard canRefresh={false} mode="monitoring" />);

    await waitFor(() => {
      expect(container.querySelectorAll('.quick-status-grid__item--wide')).toHaveLength(2);
    });
  });

  it('does not fetch the backend list in monitoring mode', async () => {
    vi.mocked(fetchQuickStatusTiles).mockResolvedValue([
      makeQuickStatusTile({ id: 10, backend_id: 1, backend_display_order: 1, backend_name: 'Alpha' }),
    ]);

    render(<Dashboard canRefresh={false} mode="monitoring" />);

    await waitFor(() => {
      expect(screen.getByText('Alpha')).toBeInTheDocument();
    });
    expect(fetchDashboard).not.toHaveBeenCalled();
    expect(fetchQuickStatusTileHistory).toHaveBeenCalledTimes(1);
  });
});
