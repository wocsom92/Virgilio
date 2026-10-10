import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { fetchDashboard, MonitoredBackend, refreshBackend } from '../api/client';
import { useQuickStatusDashboard } from '../hooks/useQuickStatusDashboard';
import { useLocalStorage } from '../hooks/useLocalStorage';
import { usePolling } from '../hooks/usePolling';
import { getUserFacingErrorMessage } from '../utils/errors';
import { QuickStatusLoadingShell, QuickStatusSections } from './QuickStatusSections';

// Chart.js and the date adapter only load when the Graphs view is opened.
const BackendCard = lazy(() => import('./BackendCard').then((module) => ({ default: module.BackendCard })));

interface DashboardProps {
  canRefresh: boolean;
  mode: 'monitoring' | 'graphs';
}

const DASHBOARD_INTERVAL_MS = 60_000;

function sortBackends(data: MonitoredBackend[]): MonitoredBackend[] {
  return [...data].sort((a, b) => {
    if (a.display_order !== b.display_order) {
      return a.display_order - b.display_order;
    }
    return a.name.localeCompare(b.name);
  });
}

function backendUnchanged(a: MonitoredBackend, b: MonitoredBackend): boolean {
  return (
    a.id === b.id &&
    a.name === b.name &&
    a.is_active === b.is_active &&
    a.display_order === b.display_order &&
    a.poll_interval_seconds === b.poll_interval_seconds &&
    a.last_warning === b.last_warning &&
    (a.latest_snapshot?.reported_at ?? null) === (b.latest_snapshot?.reported_at ?? null) &&
    JSON.stringify(a.selected_metrics ?? null) === JSON.stringify(b.selected_metrics ?? null)
  );
}

/** Keep object identity for backends that did not change so memoised cards skip re-rendering. */
function mergeBackends(current: MonitoredBackend[], incoming: MonitoredBackend[]): MonitoredBackend[] {
  const currentById = new Map(current.map((backend) => [backend.id, backend]));
  let identical = current.length === incoming.length;
  const merged = incoming.map((backend, index) => {
    const existing = currentById.get(backend.id);
    if (existing && backendUnchanged(existing, backend)) {
      if (current[index] !== existing) {
        identical = false;
      }
      return existing;
    }
    identical = false;
    return backend;
  });
  return identical ? current : merged;
}

export function Dashboard({ canRefresh, mode }: DashboardProps) {
  const isMonitoringView = mode === 'monitoring';
  const [backends, setBackends] = useState<MonitoredBackend[]>([]);
  const [actionError, setActionError] = useState<string | null>(null);
  const [refreshingId, setRefreshingId] = useState<number | null>(null);
  const [reloading, setReloading] = useState(false);
  const [hiddenBackendIds, setHiddenBackendIds] = useLocalStorage<number[]>('dashboard-hidden-backends', []);
  const [cachedGroupNames, setCachedGroupNames] = useLocalStorage<string[]>('dashboard-group-names', []);
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
  } = useQuickStatusDashboard({ enabled: isMonitoringView });

  // The heavy backend list (all latest snapshots) is only needed by the Graphs view.
  const dashboard = usePolling(fetchDashboard, { intervalMs: DASHBOARD_INTERVAL_MS, enabled: !isMonitoringView });

  useEffect(() => {
    if (!dashboard.data) {
      return;
    }
    const sorted = sortBackends(dashboard.data);
    setBackends((current) => mergeBackends(current, sorted));
    setHiddenBackendIds((prev) => {
      const valid = prev.filter((id) => sorted.some((backend) => backend.id === id));
      return valid.length === prev.length ? prev : valid;
    });
  }, [dashboard.data, setHiddenBackendIds]);

  useEffect(() => {
    if (quickStatusGroups.length === 0) {
      return;
    }
    const names = quickStatusGroups.map((group) => group.backendName);
    setCachedGroupNames((current) =>
      current.length === names.length && current.every((name, index) => name === names[index]) ? current : names
    );
  }, [quickStatusGroups, setCachedGroupNames]);

  const handleRefresh = useCallback(
    async (backend: MonitoredBackend) => {
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
        setActionError(
          getUserFacingErrorMessage(err, 'Could not refresh this backend. Check the monitor connection and try again.')
        );
      } finally {
        setRefreshingId(null);
      }
    },
    [canRefresh]
  );

  const toggleBackendVisibility = useCallback(
    (backendId: number) => {
      setHiddenBackendIds((prev) =>
        prev.includes(backendId) ? prev.filter((id) => id !== backendId) : [...prev, backendId]
      );
    },
    [setHiddenBackendIds]
  );

  const handleReload = useCallback(async () => {
    setReloading(true);
    setActionError(null);
    try {
      await Promise.all([reloadQuickStatus(), isMonitoringView ? Promise.resolve() : dashboard.refresh()]);
    } finally {
      setReloading(false);
    }
  }, [dashboard.refresh, isMonitoringView, reloadQuickStatus]);

  const dashboardError = dashboard.error
    ? getUserFacingErrorMessage(dashboard.error, 'Could not load dashboard data. Check the API connection and try again.')
    : null;
  const graphsLoading = dashboard.loading && backends.length === 0;

  return (
    <div className="dashboard-page d-flex flex-column gap-4">
      <div className="page-intro">
        <div className="page-intro__copy">
          <h2 className="page-intro__title">{isMonitoringView ? 'Quick Tiles' : 'All Graphs'}</h2>
          <p className="page-intro__description mb-0">
            {isMonitoringView
              ? 'Server quick tiles and drill-down status details.'
              : 'Historical metrics, snapshots, and warning states for every backend.'}
          </p>
        </div>
        <button
          className="btn btn-outline-light"
          onClick={() => {
            void handleReload();
          }}
          disabled={reloading}
        >
          Reload
        </button>
      </div>
      {actionError && <div className="alert alert-danger">{actionError}</div>}
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
            <QuickStatusLoadingShell groupNames={cachedGroupNames} />
          ) : (
            !quickStatusError && (
              <div className="card-panel rounded-4 p-4">
                <div className="fw-semibold mb-1">No quick tiles configured</div>
                <div className="text-secondary small">Add quick tiles from Settings to populate this view.</div>
              </div>
            )
          )}
          {quickStatusError && <div className="alert alert-warning small mb-0">{quickStatusError}</div>}
        </>
      ) : (
        <>
          {dashboardError && <div className="alert alert-danger">{dashboardError}</div>}
          {graphsLoading ? (
            <div className="text-secondary">Loading metrics…</div>
          ) : backends.length === 0 ? (
            !dashboardError && <div className="alert alert-secondary text-dark">No backends configured yet.</div>
          ) : (
            <Suspense fallback={<div className="text-secondary">Loading charts…</div>}>
              {backends.map((backend) => (
                <BackendCard
                  key={backend.id}
                  backend={backend}
                  onRefresh={canRefresh ? handleRefresh : undefined}
                  disabled={refreshingId === backend.id}
                  hidden={hiddenBackendIds.includes(backend.id)}
                  onToggleHidden={toggleBackendVisibility}
                />
              ))}
            </Suspense>
          )}
        </>
      )}
    </div>
  );
}
