import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { QuickStatusTile } from '../api/client';
import {
  fetchQuickStatusTileDetail,
  fetchQuickStatusTileHistory,
  fetchQuickStatusTiles,
} from '../api/client';
import { useQuickStatusDashboard } from './useQuickStatusDashboard';

vi.mock('../api/client', () => ({
  fetchQuickStatusTileDetail: vi.fn(),
  fetchQuickStatusTileHistory: vi.fn(),
  fetchQuickStatusTiles: vi.fn(),
  isCancelledError: () => false,
}));

function makeTile(overrides: Partial<QuickStatusTile> = {}): QuickStatusTile {
  return {
    id: overrides.id ?? 1,
    backend_id: overrides.backend_id ?? 1,
    backend_display_order: overrides.backend_display_order ?? 1,
    backend_name: overrides.backend_name ?? 'Alpha',
    label: overrides.label ?? 'Disk',
    metric_key: overrides.metric_key ?? 'disk_usage_percent',
    display_variant: overrides.display_variant ?? 'tile',
    value: overrides.value ?? 42,
    display_value: overrides.display_value ?? '42%',
    status: overrides.status ?? 'ok',
    history_loaded: overrides.history_loaded ?? false,
    has_details: overrides.has_details ?? false,
    history: overrides.history ?? [],
    series: overrides.series ?? [],
    series_summary: overrides.series_summary ?? null,
    reported_at: overrides.reported_at ?? '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

describe('useQuickStatusDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('hydrates every tile with one history request on first load', async () => {
    vi.mocked(fetchQuickStatusTiles).mockResolvedValue([
      makeTile({ id: 1, backend_id: 1, backend_name: 'Alpha' }),
      makeTile({ id: 2, backend_id: 2, backend_name: 'Beta', backend_display_order: 2 }),
    ]);
    vi.mocked(fetchQuickStatusTileHistory).mockResolvedValue([
      makeTile({ id: 1, history_loaded: true, history: Array(12).fill('ok') }),
      makeTile({ id: 2, backend_id: 2, backend_name: 'Beta', history_loaded: true, history: Array(12).fill('warn') }),
    ]);

    const { result } = renderHook(() => useQuickStatusDashboard());

    await waitFor(() => {
      expect(result.current.groups.every((group) => group.items.every((tile) => tile.history_loaded))).toBe(true);
    });
    expect(fetchQuickStatusTileHistory).toHaveBeenCalledTimes(1);
    expect(fetchQuickStatusTileHistory).toHaveBeenCalledWith(null);
    expect(result.current.groups.map((group) => group.backendName)).toEqual(['Alpha', 'Beta']);
  });

  it('keeps tile identity and history when a summary poll reports no change', async () => {
    const summary = [makeTile({ id: 1 })];
    vi.mocked(fetchQuickStatusTiles).mockResolvedValue(summary);
    vi.mocked(fetchQuickStatusTileHistory).mockResolvedValue([
      makeTile({ id: 1, history_loaded: true, history: Array(12).fill('ok') }),
    ]);

    const { result } = renderHook(() => useQuickStatusDashboard());
    await waitFor(() => {
      expect(result.current.groups[0]?.items[0]?.history_loaded).toBe(true);
    });
    const before = result.current.groups[0].items[0];

    vi.mocked(fetchQuickStatusTiles).mockResolvedValue([makeTile({ id: 1 })]);
    await act(async () => {
      await result.current.reload();
    });

    const after = result.current.groups[0].items[0];
    expect(after).toBe(before);
    expect(after.history).toHaveLength(12);
    expect(fetchQuickStatusTileHistory).toHaveBeenCalledTimes(1);
  });

  it('refreshes only the changed tile history after a status change', async () => {
    vi.mocked(fetchQuickStatusTiles).mockResolvedValue([makeTile({ id: 1 }), makeTile({ id: 2, label: 'RAM' })]);
    vi.mocked(fetchQuickStatusTileHistory).mockResolvedValue([
      makeTile({ id: 1, history_loaded: true, history: Array(12).fill('ok') }),
      makeTile({ id: 2, label: 'RAM', history_loaded: true, history: Array(12).fill('ok') }),
    ]);

    const { result } = renderHook(() => useQuickStatusDashboard());
    await waitFor(() => {
      expect(result.current.groups[0]?.items.every((tile) => tile.history_loaded)).toBe(true);
    });

    vi.mocked(fetchQuickStatusTiles).mockResolvedValue([
      makeTile({ id: 1, status: 'critical', display_value: '95%' }),
      makeTile({ id: 2, label: 'RAM' }),
    ]);
    vi.mocked(fetchQuickStatusTileHistory).mockResolvedValue([
      makeTile({ id: 1, status: 'critical', history_loaded: true, history: [...Array(11).fill('ok'), 'critical'] }),
    ]);
    await act(async () => {
      await result.current.reload();
    });

    await waitFor(() => {
      expect(fetchQuickStatusTileHistory).toHaveBeenCalledTimes(2);
    });
    expect(fetchQuickStatusTileHistory).toHaveBeenLastCalledWith([1]);
    await waitFor(() => {
      expect(result.current.groups[0].items[0].history[11]).toBe('critical');
    });
    // The unchanged tile kept its strip throughout (never blanked).
    expect(result.current.groups[0].items[1].history).toHaveLength(12);
  });

  it('loads details once when a tile is opened', async () => {
    vi.mocked(fetchQuickStatusTiles).mockResolvedValue([makeTile({ id: 1, has_details: true })]);
    vi.mocked(fetchQuickStatusTileHistory).mockResolvedValue([makeTile({ id: 1, has_details: true, history_loaded: true })]);
    vi.mocked(fetchQuickStatusTileDetail).mockResolvedValue(
      makeTile({ id: 1, has_details: true, history_loaded: true, details: [{ text: 'User: root', severity: 'warn' }] })
    );

    const { result } = renderHook(() => useQuickStatusDashboard());
    await waitFor(() => {
      expect(result.current.groups[0]?.items[0]?.history_loaded).toBe(true);
    });

    await act(async () => {
      result.current.toggleTileDetails(result.current.groups[0].items[0]);
    });
    await waitFor(() => {
      expect(result.current.groups[0].items[0].details).toHaveLength(1);
    });
    expect(result.current.selectedTileId).toBe(1);
    expect(fetchQuickStatusTileDetail).toHaveBeenCalledTimes(1);

    await act(async () => {
      result.current.toggleTileDetails(result.current.groups[0].items[0]);
    });
    expect(result.current.selectedTileId).toBeNull();
  });
});
