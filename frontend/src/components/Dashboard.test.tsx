import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { MonitoredBackend } from '../api/client';
import { Dashboard } from './Dashboard';
import { fetchDashboard, refreshBackend } from '../api/client';

vi.mock('../api/client', () => ({
  fetchDashboard: vi.fn(),
  refreshBackend: vi.fn(),
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

describe('Dashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it('renders sorted backend cards after fetching data', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue([
      makeBackend({ id: 2, name: 'Zulu', display_order: 2 }),
      makeBackend({ id: 1, name: 'Alpha', display_order: 1 }),
    ]);

    render(<Dashboard canRefresh={false} />);

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
    render(<Dashboard canRefresh />);

    await screen.findByTestId('backend-name');

    const hiddenIndicator = screen.getByTestId('backend-hidden-1');
    const toggleButton = screen.getByLabelText('toggle backend 1');
    await user.click(toggleButton);
    await waitFor(() => expect(hiddenIndicator).toHaveTextContent('hidden'));
    await user.click(toggleButton);
    await waitFor(() => expect(hiddenIndicator).toHaveTextContent('visible'));

    const refreshButton = screen.getByLabelText('refresh backend 1');
    await user.click(refreshButton);
    await waitFor(() => expect(refreshBackend).toHaveBeenCalledWith(1));
    await waitFor(() => expect(screen.getByTestId('backend-snapshot-1')).toHaveTextContent('2024-01-01T00:00:00Z'));
  });
});
