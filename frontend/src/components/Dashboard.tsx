import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchDashboard, fetchQuickStatusTiles, MonitoredBackend, QuickStatusTile, refreshBackend } from '../api/client';
import { useLocalStorage } from '../hooks/useLocalStorage';
import { getUserFacingErrorMessage } from '../utils/errors';
import { BackendCard } from './BackendCard';
import { QuickStatusTileCard } from './QuickStatusTileCard';

interface DashboardProps {
  canRefresh: boolean;
  mode: 'monitoring' | 'graphs';
}

export function Dashboard({ canRefresh, mode }: DashboardProps) {
  const [backends, setBackends] = useState<MonitoredBackend[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [quickStatusTiles, setQuickStatusTiles] = useState<QuickStatusTile[]>([]);
  const [quickStatusError, setQuickStatusError] = useState<string | null>(null);
  const [selectedQuickStatusTileId, setSelectedQuickStatusTileId] = useState<number | null>(null);
  const isQuickStatusFetchingRef = useRef(false);
  const [refreshingId, setRefreshingId] = useState<number | null>(null);
  const [hiddenBackendIds, setHiddenBackendIds] = useLocalStorage<number[]>('dashboard-hidden-backends', []);
  const isFetchingRef = useRef(false);

  const loadQuickStatus = useCallback(async () => {
    if (isQuickStatusFetchingRef.current) {
      return;
    }
    isQuickStatusFetchingRef.current = true;
    setQuickStatusError(null);
    try {
      const tiles = await fetchQuickStatusTiles();
      setQuickStatusTiles(tiles);
    } catch (err) {
      setQuickStatusTiles([]);
      setQuickStatusError(
        getUserFacingErrorMessage(err, 'Could not load quick tiles. Check the API connection and try again.')
      );
    } finally {
      isQuickStatusFetchingRef.current = false;
    }
  }, []);

  const loadData = useCallback(
    async (options: { showSpinner?: boolean } = {}) => {
      if (isFetchingRef.current) {
        return;
      }
      const { showSpinner = false } = options;
      isFetchingRef.current = true;
      if (showSpinner) {
        setLoading(true);
      }
      setError(null);
      try {
        const data = await fetchDashboard();
        const sorted = [...data].sort((a, b) => {
          if (a.display_order !== b.display_order) {
            return a.display_order - b.display_order;
          }
          return a.name.localeCompare(b.name);
        });
        setBackends(sorted);
        setHiddenBackendIds((prev) => {
          const valid = prev.filter((id) => sorted.some((backend) => backend.id === id));
          if (valid.length === prev.length) {
            return prev;
          }
          return valid;
        });
      } catch (err) {
        setError(getUserFacingErrorMessage(err, 'Could not load dashboard data. Check the API connection and try again.'));
      } finally {
        if (showSpinner) {
          setLoading(false);
        }
        isFetchingRef.current = false;
      }
    },
    [setHiddenBackendIds]
  );

  useEffect(() => {
    void loadData({ showSpinner: true });
  }, [loadData]);

  useEffect(() => {
    const DASHBOARD_REFRESH_INTERVAL_MS = 30_000;
    const intervalId = window.setInterval(() => {
      void loadData();
    }, DASHBOARD_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(intervalId);
  }, [loadData]);

  useEffect(() => {
    void loadQuickStatus();
  }, [loadQuickStatus]);

  useEffect(() => {
    const QUICK_STATUS_REFRESH_INTERVAL_MS = 300_000;
    const intervalId = window.setInterval(() => {
      void loadQuickStatus();
    }, QUICK_STATUS_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(intervalId);
  }, [loadQuickStatus]);

  const handleRefresh = async (backend: MonitoredBackend) => {
    if (!canRefresh) return;
    setRefreshingId(backend.id);
    try {
      const snapshot = await refreshBackend(backend.id);
      setBackends((prev) =>
        prev.map((item) =>
          item.id === backend.id ? { ...item, latest_snapshot: snapshot, last_seen_at: new Date().toISOString() } : item
        )
      );
    } catch (err) {
      setError(getUserFacingErrorMessage(err, 'Could not refresh this backend. Check the monitor connection and try again.'));
    } finally {
      setRefreshingId(null);
    }
  };

  const toggleBackendVisibility = useCallback(
    (backendId: number) => {
      setHiddenBackendIds((prev) =>
        prev.includes(backendId) ? prev.filter((id) => id !== backendId) : [...prev, backendId]
      );
    },
    [setHiddenBackendIds]
  );

  const quickStatusGroups = useMemo(() => {
    const grouped = new Map<number, QuickStatusTile[]>();
    for (const tile of quickStatusTiles) {
      const current = grouped.get(tile.backend_id) ?? [];
      current.push(tile);
      grouped.set(tile.backend_id, current);
    }
    const backendMeta = new Map(backends.map((backend) => [backend.id, backend] as const));
    const orderedGroups = backends
      .filter((backend) => grouped.has(backend.id))
      .map((backend) => ({
        backendId: backend.id,
        backendName: backend.name,
        displayOrder: backend.display_order,
        items: grouped.get(backend.id) ?? [],
      }));
    const fallbackGroups = Array.from(grouped.entries())
      .filter(([backendId]) => !backendMeta.has(backendId))
      .map(([backendId, items]) => ({
        backendId,
        backendName: items[0]?.backend_name ?? `Backend #${backendId}`,
        displayOrder: items[0]?.backend_display_order ?? 0,
        items,
      }))
      .sort((a, b) => {
        if (a.displayOrder !== b.displayOrder) {
          return a.displayOrder - b.displayOrder;
        }
        const byName = a.backendName.localeCompare(b.backendName, undefined, { sensitivity: 'base' });
        if (byName !== 0) {
          return byName;
        }
        return a.backendId - b.backendId;
      });
    return [...orderedGroups, ...fallbackGroups];
  }, [backends, quickStatusTiles]);

  const isMonitoringView = mode === 'monitoring';
  const shouldShowDashboardError = !isMonitoringView || quickStatusGroups.length === 0;

  return (
    <div className="d-flex flex-column gap-4">
      <div className="d-flex justify-content-between align-items-center">
        <div>
          <h2 className="fw-semibold text-uppercase mb-1">{isMonitoringView ? 'Quick Tiles' : 'All Graphs'}</h2>
          <p className="text-secondary small mb-0">
            {isMonitoringView
              ? 'Server quick tiles and drill-down status details.'
              : 'Historical metrics, snapshots, and warning states for every backend.'}
          </p>
        </div>
        <button
          className="btn btn-outline-light"
          onClick={() => {
            void loadData({ showSpinner: true });
            void loadQuickStatus();
          }}
          disabled={loading}
        >
          Reload
        </button>
      </div>
      {isMonitoringView ? (
        <>
          {quickStatusGroups.length > 0 ? (
            <div className="quick-status-sections">
              {quickStatusGroups.map((group) => (
                <section className="quick-status-section" key={group.backendId}>
                  <div className="quick-status-section__header">
                    <h3 className="quick-status-section__title">{group.backendName}</h3>
                    <span className="quick-status-section__count">
                      {group.items.length} {group.items.length === 1 ? 'tile' : 'tiles'}
                    </span>
                  </div>
                  <div className="quick-status-grid">
                    {group.items.map((tile) => (
                      <div className="quick-status-grid__item" key={tile.id}>
                        <QuickStatusTileCard
                          tile={tile}
                          onClick={
                            tile.details && tile.details.length > 0
                              ? () =>
                                  setSelectedQuickStatusTileId((current) => (current === tile.id ? null : tile.id))
                              : undefined
                          }
                        />
                      </div>
                    ))}
                  </div>
                  {(() => {
                    const selectedTile = group.items.find((tile) => tile.id === selectedQuickStatusTileId);
                    if (!selectedTile?.details?.length) {
                      return null;
                    }
                    return (
                      <div className="quick-status-detail card-panel rounded-4 p-3 mt-3">
                        <div className="d-flex justify-content-between align-items-center gap-2 mb-2">
                          <div>
                            <div className="text-uppercase card-panel__heading fw-semibold">{selectedTile.label}</div>
                            <div className="small text-secondary">{selectedTile.backend_name}</div>
                          </div>
                          <button
                            type="button"
                            className="btn btn-sm btn-outline-light"
                            onClick={() => setSelectedQuickStatusTileId(null)}
                          >
                            Close
                          </button>
                        </div>
                        <ul className="mb-0 small quick-status-detail__list">
                          {selectedTile.details.map((line) => (
                            <li
                              key={`${line.severity}:${line.text}`}
                              className={
                                line.severity === 'critical'
                                  ? 'text-danger'
                                  : line.severity === 'warn'
                                    ? 'text-warning'
                                    : 'text-success'
                              }
                            >
                              <code>{line.text}</code>
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  })()}
                </section>
              ))}
            </div>
          ) : (
            !quickStatusError && (
              <div className="card-panel rounded-4 p-4">
                <div className="fw-semibold mb-1">No quick tiles configured</div>
                <div className="text-secondary small">
                  {loading ? 'Loading current monitoring state…' : 'Add quick tiles from Administration to populate this view.'}
                </div>
              </div>
            )
          )}
          {quickStatusError && <div className="alert alert-warning small mb-0">{quickStatusError}</div>}
          {error && shouldShowDashboardError && <div className="alert alert-danger">{error}</div>}
        </>
      ) : (
        <>
          {error && <div className="alert alert-danger">{error}</div>}
          {loading ? (
            <div className="text-secondary">Loading metrics…</div>
          ) : backends.length === 0 ? (
            <div className="alert alert-secondary text-dark">No backends configured yet.</div>
          ) : (
            backends.map((backend) => (
              <BackendCard
                key={backend.id}
                backend={backend}
                onRefresh={canRefresh ? handleRefresh : undefined}
                disabled={refreshingId === backend.id}
                hidden={hiddenBackendIds.includes(backend.id)}
                onToggleHidden={toggleBackendVisibility}
              />
            ))
          )}
        </>
      )}
    </div>
  );
}
