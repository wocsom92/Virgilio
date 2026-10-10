import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  QuickStatusTile,
  fetchQuickStatusTileDetail,
  fetchQuickStatusTileHistory,
  fetchQuickStatusTiles,
  isCancelledError,
} from '../api/client';
import { getUserFacingErrorMessage } from '../utils/errors';
import { usePolling } from './usePolling';

export interface QuickStatusGroup {
  key: string;
  backendId: number | null;
  backendName: string;
  displayOrder: number;
  items: QuickStatusTile[];
}

const SUMMARY_INTERVAL_MS = 60_000;
const HISTORY_REFRESH_MS = 300_000;

function groupQuickStatusTiles(tiles: QuickStatusTile[]): QuickStatusGroup[] {
  const grouped = new Map<string, QuickStatusGroup>();
  for (const tile of tiles) {
    const groupKey = `backend:${tile.backend_id}`;
    const current = grouped.get(groupKey) ?? {
      key: groupKey,
      backendId: tile.backend_id,
      backendName: tile.backend_name,
      displayOrder: tile.backend_display_order ?? 0,
      items: [],
    };
    current.items.push(tile);
    grouped.set(groupKey, current);
  }

  return Array.from(grouped.values()).sort((a, b) => {
    if (a.displayOrder !== b.displayOrder) {
      return a.displayOrder - b.displayOrder;
    }
    const byName = a.backendName.localeCompare(b.backendName, undefined, { sensitivity: 'base' });
    if (byName !== 0) {
      return byName;
    }
    return a.key.localeCompare(b.key);
  });
}

const SUMMARY_FIELDS: Array<keyof QuickStatusTile> = [
  'backend_id',
  'backend_display_order',
  'backend_name',
  'label',
  'metric_key',
  'display_variant',
  'display_unit',
  'value',
  'display_value',
  'status',
  'stale',
  'show_updated_age',
  'has_details',
  'reported_at',
];

function summaryEqual(a: QuickStatusTile, b: QuickStatusTile): boolean {
  return SUMMARY_FIELDS.every((field) => a[field] === b[field]);
}

function mergeHydratedTiles(current: QuickStatusTile[], incoming: QuickStatusTile[]): QuickStatusTile[] {
  if (incoming.length === 0) {
    return current;
  }
  const incomingById = new Map(incoming.map((tile) => [tile.id, tile]));
  let changed = false;
  const next = current.map((tile) => {
    const hydrated = incomingById.get(tile.id);
    if (!hydrated) {
      return tile;
    }
    changed = true;
    return { ...tile, ...hydrated, history_loaded: true };
  });
  return changed ? next : current;
}

/**
 * Apply a fresh summary on top of the current tiles. Unchanged tiles keep their object identity
 * (so memoised cards skip re-rendering) and already loaded history/series is never discarded;
 * tiles whose status or value moved are flagged for an early history refresh via `changedIds`.
 */
function mergeSummaryTiles(
  current: QuickStatusTile[],
  incoming: QuickStatusTile[]
): { tiles: QuickStatusTile[]; changedIds: number[] } {
  const currentById = new Map(current.map((tile) => [tile.id, tile]));
  const changedIds: number[] = [];
  let identical = current.length === incoming.length;
  const tiles = incoming.map((summaryTile, index) => {
    const existing = currentById.get(summaryTile.id);
    if (!existing) {
      identical = false;
      return summaryTile;
    }
    if (summaryEqual(existing, summaryTile)) {
      if (current[index] !== existing) {
        identical = false;
      }
      return existing;
    }
    identical = false;
    if (
      existing.status !== summaryTile.status ||
      existing.display_value !== summaryTile.display_value ||
      Boolean(existing.stale) !== Boolean(summaryTile.stale)
    ) {
      changedIds.push(summaryTile.id);
    }
    return {
      ...existing,
      ...summaryTile,
      history_loaded: existing.history_loaded ?? false,
      history: existing.history_loaded ? existing.history : summaryTile.history,
      series: existing.history_loaded ? existing.series : summaryTile.series,
      series_summary: existing.history_loaded ? existing.series_summary : summaryTile.series_summary,
      details: existing.details ?? summaryTile.details,
      has_details: summaryTile.has_details ?? existing.has_details,
    };
  });
  return { tiles: identical ? current : tiles, changedIds };
}

export function useQuickStatusDashboard(options: { enabled?: boolean } = {}) {
  const enabled = options.enabled ?? true;
  const [tiles, setTiles] = useState<QuickStatusTile[]>([]);
  const [initialLoadDone, setInitialLoadDone] = useState(false);
  const [hydrationError, setHydrationError] = useState<string | null>(null);
  const [selectedTileId, setSelectedTileId] = useState<number | null>(null);
  const [detailLoadingId, setDetailLoadingId] = useState<number | null>(null);
  const [hydratingGroups, setHydratingGroups] = useState<Record<string, boolean>>({});

  const selectedTileIdRef = useRef<number | null>(null);
  selectedTileIdRef.current = selectedTileId;
  const historyFetchedAtRef = useRef<Map<number, number>>(new Map());
  const inFlightRef = useRef<Set<number>>(new Set());
  const detailInFlightRef = useRef<Set<number>>(new Set());

  const summary = usePolling(fetchQuickStatusTiles, { intervalMs: SUMMARY_INTERVAL_MS, enabled });

  useEffect(() => {
    if (!summary.data) {
      return;
    }
    const incoming = summary.data;
    setTiles((current) => {
      const { tiles: merged, changedIds } = mergeSummaryTiles(current, incoming);
      for (const id of changedIds) {
        historyFetchedAtRef.current.set(id, 0);
      }
      return merged;
    });
    setInitialLoadDone(true);
  }, [summary.data]);

  useEffect(() => {
    if (summary.error) {
      setInitialLoadDone(true);
    }
  }, [summary.error]);

  const groups = useMemo(() => groupQuickStatusTiles(tiles), [tiles]);

  useEffect(() => {
    if (selectedTileId === null) {
      return;
    }
    if (!tiles.some((tile) => tile.id === selectedTileId)) {
      setSelectedTileId(null);
    }
  }, [selectedTileId, tiles]);

  // Hydrate 24h history for tiles that never loaded it (or loaded it long ago / changed state).
  // Imperative with an in-flight guard: each tile is requested at most once per cycle.
  useEffect(() => {
    if (!initialLoadDone || groups.length === 0) {
      return;
    }
    const now = Date.now();
    const isUnresolved = (tile: QuickStatusTile) => {
      if (inFlightRef.current.has(tile.id)) {
        return false;
      }
      const fetchedAt = historyFetchedAtRef.current.get(tile.id);
      return !tile.history_loaded || fetchedAt === undefined || now - fetchedAt >= HISTORY_REFRESH_MS;
    };
    const pending = groups
      .map((group) => ({ group, ids: group.items.filter(isUnresolved).map((tile) => tile.id) }))
      .filter((entry) => entry.ids.length > 0);
    if (pending.length === 0) {
      return;
    }

    const totalTiles = groups.reduce((count, group) => count + group.items.length, 0);
    const pendingIds = pending.flatMap((entry) => entry.ids);
    const requestAll = pendingIds.length === totalTiles;
    for (const id of pendingIds) {
      inFlightRef.current.add(id);
    }
    setHydratingGroups((current) => {
      const next = { ...current };
      for (const entry of pending) {
        next[entry.group.key] = true;
      }
      return next;
    });

    const settle = (ids: number[], groupKeys: string[]) => {
      const finishedAt = Date.now();
      for (const id of ids) {
        inFlightRef.current.delete(id);
        historyFetchedAtRef.current.set(id, finishedAt);
      }
      setHydratingGroups((current) => {
        const next = { ...current };
        for (const key of groupKeys) {
          delete next[key];
        }
        return next;
      });
    };

    void (async () => {
      if (requestAll) {
        try {
          const hydrated = await fetchQuickStatusTileHistory(null);
          setTiles((current) => mergeHydratedTiles(current, hydrated));
          setHydrationError(null);
        } catch (err) {
          if (!isCancelledError(err)) {
            setHydrationError(getUserFacingErrorMessage(err, 'Could not load quick-tile history. Try again.'));
          }
        } finally {
          settle(
            pendingIds,
            pending.map((entry) => entry.group.key)
          );
        }
        return;
      }
      for (const entry of pending) {
        try {
          const hydrated = await fetchQuickStatusTileHistory(entry.ids);
          setTiles((current) => mergeHydratedTiles(current, hydrated));
          setHydrationError(null);
        } catch (err) {
          if (!isCancelledError(err)) {
            setHydrationError(getUserFacingErrorMessage(err, 'Could not load quick-tile history. Try again.'));
          }
        } finally {
          settle(entry.ids, [entry.group.key]);
        }
      }
    })();
  }, [groups, initialLoadDone]);

  const toggleTileDetails = useCallback((tile: QuickStatusTile) => {
    if (!tile.has_details) {
      return;
    }
    const opened = selectedTileIdRef.current !== tile.id;
    selectedTileIdRef.current = opened ? tile.id : null;
    setSelectedTileId(selectedTileIdRef.current);
    if (!opened || (tile.details !== undefined && tile.details !== null)) {
      return;
    }
    if (detailInFlightRef.current.has(tile.id)) {
      return;
    }
    detailInFlightRef.current.add(tile.id);
    setDetailLoadingId(tile.id);
    void (async () => {
      try {
        const detailedTile = await fetchQuickStatusTileDetail(tile.id);
        setTiles((current) => mergeHydratedTiles(current, [detailedTile]));
      } catch (err) {
        if (!isCancelledError(err)) {
          setHydrationError(getUserFacingErrorMessage(err, 'Could not load quick-tile details. Try again.'));
        }
      } finally {
        detailInFlightRef.current.delete(tile.id);
        setDetailLoadingId((current) => (current === tile.id ? null : current));
      }
    })();
  }, []);

  const closeTileDetails = useCallback(() => setSelectedTileId(null), []);

  const summaryError = summary.error
    ? getUserFacingErrorMessage(summary.error, 'Could not load quick tiles. Check the API connection and try again.')
    : null;

  return {
    groups,
    loading: summary.loading && !initialLoadDone,
    error: summaryError ?? hydrationError,
    initialLoadDone,
    selectedTileId,
    detailLoadingId,
    hydratingGroups,
    reload: summary.refresh,
    toggleTileDetails,
    closeTileDetails,
  };
}
