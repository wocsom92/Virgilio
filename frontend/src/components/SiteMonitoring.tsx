import { useCallback, useEffect, useRef, useState } from 'react';
import classNames from 'classnames';

import { SiteMonitorStatus, fetchSiteMonitorStatuses } from '../api/client';
import { getUserFacingErrorMessage } from '../utils/errors';

const SITE_MONITOR_PAGE_SIZE = 20;

function getStatusClass(status: SiteMonitorStatus['status']): string {
  if (status === 'critical') return 'quick-status--critical';
  if (status === 'warn') return 'quick-status--warn';
  if (status === 'ok') return 'quick-status--ok';
  return 'quick-status--unknown';
}

function getStatusBadgeClass(status: SiteMonitorStatus['status']): string {
  if (status === 'critical') return 'bg-danger';
  if (status === 'warn') return 'bg-warning text-dark';
  if (status === 'ok') return 'bg-success';
  return 'bg-secondary';
}

function formatCheckedAt(value: string | null): string {
  if (!value) {
    return 'No samples yet';
  }
  return new Date(value).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function SiteMonitoring() {
  const [sites, setSites] = useState<SiteMonitorStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [expandedSiteId, setExpandedSiteId] = useState<number | null>(null);
  const isFetchingRef = useRef(false);

  const loadSites = useCallback(async (options: { showSpinner?: boolean } = {}) => {
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
      const data = await fetchSiteMonitorStatuses();
      setSites(data);
    } catch (err) {
      setError(getUserFacingErrorMessage(err, 'Could not load site monitoring data. Check the API connection and try again.'));
    } finally {
      if (showSpinner) {
        setLoading(false);
      }
      isFetchingRef.current = false;
    }
  }, []);

  useEffect(() => {
    void loadSites({ showSpinner: true });
  }, [loadSites]);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      void loadSites();
    }, 30_000);
    return () => window.clearInterval(intervalId);
  }, [loadSites]);

  useEffect(() => {
    const totalPages = Math.max(1, Math.ceil(sites.length / SITE_MONITOR_PAGE_SIZE));
    setCurrentPage((prev) => Math.min(prev, totalPages));
  }, [sites]);

  const totalPages = Math.max(1, Math.ceil(sites.length / SITE_MONITOR_PAGE_SIZE));
  const pageStart = (currentPage - 1) * SITE_MONITOR_PAGE_SIZE;
  const pagedSites = sites.slice(pageStart, pageStart + SITE_MONITOR_PAGE_SIZE);
  const visibleStart = sites.length === 0 ? 0 : pageStart + 1;
  const visibleEnd = Math.min(sites.length, pageStart + pagedSites.length);

  return (
    <div className="d-flex flex-column gap-4">
      <div className="d-flex justify-content-between align-items-center gap-3 flex-wrap">
        <div>
          <h2 className="fw-semibold text-uppercase mb-1">Site Monitoring</h2>
          <p className="text-secondary small mb-0">
            Reachability and HTTP checks with 48 half-hour results covering the last 24 hours.
          </p>
        </div>
        <button
          className="btn btn-outline-light"
          type="button"
          onClick={() => {
            void loadSites({ showSpinner: true });
          }}
          disabled={loading}
        >
          Reload
        </button>
      </div>

      {error && <div className="alert alert-danger mb-0">{error}</div>}

      {loading ? (
        <div className="text-secondary">Loading site checks…</div>
      ) : sites.length === 0 ? (
        <div className="card-panel rounded-4 p-4">
          <div className="fw-semibold mb-1">No site checks configured</div>
          <div className="text-secondary small">Add ping or HTTP checks from Administration to populate this view.</div>
        </div>
      ) : (
        <>
          <div className="d-flex justify-content-between align-items-center gap-3 flex-wrap text-secondary small">
            <span>
              Showing {visibleStart}-{visibleEnd} of {sites.length} sites
            </span>
            {totalPages > 1 && (
              <div className="d-flex align-items-center gap-2">
                <span>
                  Page {currentPage} of {totalPages}
                </span>
                <button
                  className="btn btn-sm btn-outline-light"
                  type="button"
                  onClick={() => setCurrentPage((prev) => Math.max(1, prev - 1))}
                  disabled={currentPage <= 1}
                >
                  Previous
                </button>
                <button
                  className="btn btn-sm btn-outline-light"
                  type="button"
                  onClick={() => setCurrentPage((prev) => Math.min(totalPages, prev + 1))}
                  disabled={currentPage >= totalPages}
                >
                  Next
                </button>
              </div>
            )}
          </div>
          <div className="site-monitor-list">
            {pagedSites.map((site) => (
              <article
                className={classNames('site-monitor-row card-panel rounded-4 p-3', {
                  'site-monitor-row--expanded': expandedSiteId === site.id,
                })}
                key={site.id}
                role="button"
                tabIndex={0}
                aria-expanded={expandedSiteId === site.id}
                onClick={() => setExpandedSiteId((current) => (current === site.id ? null : site.id))}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    setExpandedSiteId((current) => (current === site.id ? null : site.id));
                  }
                }}
              >
                <div className="site-monitor-row__summary">
                  <div className="site-monitor-row__content">
                    <div className="site-monitor-row__identity">
                      <span className="site-monitor-row__title">{site.name}</span>
                    </div>
                  </div>
                </div>
                <div
                  className="site-monitor-row__history quick-status-history"
                  aria-label="48 half-hour check results across the last 24 hours"
                >
                  {site.history.map((status, index) => (
                    <span
                      key={`${site.id}:${index}`}
                      className={classNames('quick-status-history__segment', getStatusClass(status))}
                    />
                  ))}
                </div>
                {expandedSiteId === site.id && (
                  <div className="site-monitor-row__details">
                    <span className="badge bg-secondary text-uppercase">{site.check_type}</span>
                    <span className={classNames('badge text-uppercase', getStatusBadgeClass(site.status))}>
                      {site.status}
                    </span>
                    <span className="site-monitor-row__value">{site.display_value}</span>
                    <span className="site-monitor-row__target">{site.target}</span>
                    <span>Last check: {formatCheckedAt(site.checked_at)}</span>
                    {site.consecutive_failures > 0 && (
                      <span>{site.consecutive_failures} consecutive failures</span>
                    )}
                    {site.detail && <span className="site-monitor-row__detail text-warning">{site.detail}</span>}
                  </div>
                )}
              </article>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
