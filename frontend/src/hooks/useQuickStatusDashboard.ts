import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  QuickStatusTile,
  fetchQuickStatusTileDetail,
  fetchQuickStatusTileHistory,
  fetchQuickStatusTiles,
} from '../api/client';
import { getUserFacingErrorMessage } from '../utils/errors';

export interface QuickStatusGroup {
  key: string;
  backendId: number | null;
  backendName: string;
  displayOrder: number;
  items: QuickStatusTile[];
}

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

function mergeHydratedTiles(current: QuickStatusTile[], incoming: QuickStatusTile[]): QuickStatusTile[] {
  if (incoming.length === 0) {
    return current;
  }

  const incomingById = new Map(incoming.map((tile) => [tile.id, tile]));
  return current.map((tile) => incomingById.get(tile.id) ?? tile);
}

function mergeSummaryTiles(current: QuickStatusTile[], incoming: QuickStatusTile[]): QuickStatusTile[] {
  if (current.length === 0) {
    return incoming;
  }

  const currentById = new Map(current.map((tile) => [tile.id, tile]));
  return incoming.map((summaryTile) => {
    const existing = currentById.get(summaryTile.id);
    if (!existing) {
      return summaryTile;
    }
    const statusChanged =
      existing.status !== summaryTile.status ||
      existing.display_value !== summaryTile.display_value ||
      Boolean(existing.stale) !== Boolean(summaryTile.stale);
    const keepHistory = !statusChanged && existing.history_loaded;
    return {
      ...existing,
      ...summaryTile,
      history_loaded: keepHistory ? existing.history_loaded || summaryTile.history_loaded : false,
      history: keepHistory ? (existing.history_loaded ? existing.history : summaryTile.history) : [],
      series: keepHistory ? (existing.history_loaded ? existing.series : summaryTile.series) : [],
      series_summary: keepHistory
        ? existing.history_loaded
          ? existing.series_summary
          : summaryTile.series_summary
        : null,
      details: existing.details ?? summaryTile.details,
      has_details: summaryTile.has_details ?? existing.has_details,
    };
  });
}

export function useQuickStatusDashboard() {
  const [tiles, setTiles] = useState<QuickStatusTile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [initialLoadDone, setInitialLoadDone] = useState(false);
  const [selectedTileId, setSelectedTileId] = useState<number | null>(null);
  const [detailLoadingId, setDetailLoadingId] = useState<number | null>(null);
  const [hydratingGroups, setHydratingGroups] = useState<Record<string, boolean>>({});

  const summaryRequestIdRef = useRef(0);
  const hydrationRunIdRef = useRef(0);

  const loadSummary = useCallback(async () => {
    const requestId = summaryRequestIdRef.current + 1;
    summaryRequestIdRef.current = requestId;
    setError(null);

    try {
      const summary = await fetchQuickStatusTiles();
      if (summaryRequestIdRef.current !== requestId) {
        return;
      }
      setTiles((current) => mergeSummaryTiles(current, summary));
    } catch (err) {
      if (summaryRequestIdRef.current !== requestId) {
        return;
      }
      setTiles((current) => (current.length > 0 ? current : []));
      setError(getUserFacingErrorMessage(err, 'Could not load quick tiles. Check the API connection and try again.'));
    } finally {
      if (summaryRequestIdRef.current === requestId) {
        setLoading(false);
        setInitialLoadDone(true);
      }
    }
  }, []);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  const groups = useMemo(() => groupQuickStatusTiles(tiles), [tiles]);

  useEffect(() => {
    if (selectedTileId === null) {
      return;
    }
    if (!tiles.some((tile) => tile.id === selectedTileId)) {
      setSelectedTileId(null);
    }
  }, [selectedTileId, tiles]);

  useEffect(() => {
    if (!initialLoadDone || groups.length === 0) {
      return;
    }

    const runId = hydrationRunIdRef.current + 1;
    hydrationRunIdRef.current = runId;
    let cancelled = false;

    void (async () => {
      for (const group of groups) {
        const unresolvedIds = group.items.filter((tile) => !tile.history_loaded).map((tile) => tile.id);
        if (unresolvedIds.length === 0) {
          continue;
        }

        setHydratingGroups((current) => ({ ...current, [group.key]: true }));
        try {
          const hydratedTiles = await fetchQuickStatusTileHistory(unresolvedIds);
          if (cancelled || hydrationRunIdRef.current !== runId) {
            return;
          }
          setTiles((current) => mergeHydratedTiles(current, hydratedTiles));
        } catch (err) {
          if (!cancelled && hydrationRunIdRef.current === runId) {
            setError((current) =>
              current ?? getUserFacingErrorMessage(err, 'Could not load quick-tile history. Try again.')
            );
          }
        } finally {
          if (!cancelled && hydrationRunIdRef.current === runId) {
            setHydratingGroups((current) => {
              const next = { ...current };
              delete next[group.key];
              return next;
            });
          }
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [groups, initialLoadDone]);

  const toggleTileDetails = useCallback(
    async (tile: QuickStatusTile) => {
      if (!tile.has_details) {
        return;
      }
      if (selectedTileId === tile.id) {
        setSelectedTileId(null);
        return;
      }

      setSelectedTileId(tile.id);
      if (tile.details !== undefined && tile.details !== null) {
        return;
      }

      setDetailLoadingId(tile.id);
      try {
        const detailedTile = await fetchQuickStatusTileDetail(tile.id);
        setTiles((current) => mergeHydratedTiles(current, [detailedTile]));
      } catch (err) {
        setError(getUserFacingErrorMessage(err, 'Could not load quick-tile details. Try again.'));
      } finally {
        setDetailLoadingId((current) => (current === tile.id ? null : current));
      }
    },
    [selectedTileId]
  );

  return {
    groups,
    loading,
    error,
    initialLoadDone,
    selectedTileId,
    detailLoadingId,
    hydratingGroups,
    reload: loadSummary,
    toggleTileDetails,
    closeTileDetails: () => setSelectedTileId(null),
  };
}
