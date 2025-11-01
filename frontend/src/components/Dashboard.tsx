import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchDashboard, MonitoredBackend, refreshBackend } from '../api/client';
import { useLocalStorage } from '../hooks/useLocalStorage';
import { BackendCard } from './BackendCard';

interface DashboardProps {
  canRefresh: boolean;
}

export function Dashboard({ canRefresh }: DashboardProps) {
  const [backends, setBackends] = useState<MonitoredBackend[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshingId, setRefreshingId] = useState<number | null>(null);
  const [hiddenBackendIds, setHiddenBackendIds] = useLocalStorage<number[]>('dashboard-hidden-backends', []);
  const isFetchingRef = useRef(false);

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
        setError(err instanceof Error ? err.message : 'Unable to load dashboard');
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
      setError(err instanceof Error ? err.message : 'Unable to refresh backend');
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

  return (
    <div className="d-flex flex-column gap-4">
      <div className="d-flex justify-content-between align-items-center">
        <h2 className="fw-semibold text-uppercase">Overview</h2>
        <button className="btn btn-outline-light" onClick={() => void loadData({ showSpinner: true })} disabled={loading}>
          Reload
        </button>
      </div>
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
    </div>
  );
}
