import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchDashboard, MonitoredBackend, refreshBackend } from '../api/client';
import { useQuickStatusDashboard } from '../hooks/useQuickStatusDashboard';
import { useLocalStorage } from '../hooks/useLocalStorage';
import { getUserFacingErrorMessage } from '../utils/errors';
import { BackendCard } from './BackendCard';
import { QuickStatusLoadingShell, QuickStatusSections } from './QuickStatusSections';

interface DashboardProps {
  canRefresh: boolean;
  mode: 'monitoring' | 'graphs';
  projectId?: number | null;
}

export function Dashboard({ canRefresh, mode, projectId = null }: DashboardProps) {
  const [backends, setBackends] = useState<MonitoredBackend[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshingId, setRefreshingId] = useState<number | null>(null);
  const [hiddenBackendIds, setHiddenBackendIds] = useLocalStorage<number[]>('dashboard-hidden-backends', []);
  const isFetchingRef = useRef(false);
  const {
    groups: quickStatusGroups,
    loading: quickStatusLoading,
    error: quickStatusError,
    initialLoadDone: quickStatusInitialLoadDone,
    selectedTileId: selectedQuickStatusTileId,
    detailLoadingId,
    hydratingGroups,
    reload: reloadQuickStatus,
    toggleTileDetails,
    closeTileDetails,
  } = useQuickStatusDashboard(projectId);

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
    const QUICK_STATUS_REFRESH_INTERVAL_MS = 300_000;
    const intervalId = window.setInterval(() => {
      void reloadQuickStatus();
    }, QUICK_STATUS_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(intervalId);
  }, [reloadQuickStatus]);

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

  const isMonitoringView = mode === 'monitoring';
  const isProjectView = isMonitoringView && projectId !== null;
  const shouldShowDashboardError = !isMonitoringView || quickStatusGroups.length === 0;
  const quickStatusShellGroupNames = useMemo(() => {
    if (isProjectView) {
      return ['Project quick tiles'];
    }
    return backends.map((backend) => backend.name);
  }, [backends, isProjectView]);

  return (
    <div className="dashboard-page d-flex flex-column gap-4">
      <div className="page-intro">
        <div className="page-intro__copy">
          <h2 className="page-intro__title">{isMonitoringView ? 'Quick Tiles' : 'All Graphs'}</h2>
          <p className="page-intro__description mb-0">
            {isMonitoringView
              ? isProjectView
                ? 'Project quick tiles and drill-down status details.'
                : 'Server quick tiles and drill-down status details.'
              : 'Historical metrics, snapshots, and warning states for every backend.'}
          </p>
        </div>
        <button
          className="btn btn-outline-light"
          onClick={() => {
            void loadData({ showSpinner: true });
            void reloadQuickStatus();
          }}
          disabled={loading}
        >
          Reload
        </button>
      </div>
      {isMonitoringView ? (
        <>
          {quickStatusGroups.length > 0 ? (
            <QuickStatusSections
              groups={quickStatusGroups}
              hydratingGroups={hydratingGroups}
              selectedTileId={selectedQuickStatusTileId}
              detailLoadingId={detailLoadingId}
              onTileToggle={toggleTileDetails}
              onCloseDetails={closeTileDetails}
            />
          ) : quickStatusLoading && !quickStatusInitialLoadDone ? (
            <QuickStatusLoadingShell groupNames={quickStatusShellGroupNames} />
          ) : (
            !quickStatusError && (
              <div className="card-panel rounded-4 p-4">
                <div className="fw-semibold mb-1">No quick tiles configured</div>
                <div className="text-secondary small">
                  {isProjectView
                    ? 'Assign quick tiles to this project from Settings to populate this view.'
                    : 'Add quick tiles from Settings to populate this view.'}
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
