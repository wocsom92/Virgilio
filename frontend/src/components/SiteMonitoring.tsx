import { memo, useCallback, useEffect, useState } from 'react';
import classNames from 'classnames';

import { SiteMonitorStatus, fetchSiteMonitorStatuses } from '../api/client';
import { usePolling } from '../hooks/usePolling';
import { getUserFacingErrorMessage } from '../utils/errors';
import { getStatusClass } from '../utils/formatting';

const SITE_MONITOR_PAGE_SIZE = 20;
const SITE_MONITOR_INTERVAL_MS = 60_000;

function getStatusBadgeClass(status: SiteMonitorStatus['status']): string {
  if (status === 'critical') return 'bg-danger';
  if (status === 'warn') return 'bg-warning text-dark';
  if (status === 'ok') return 'bg-success';
  return 'bg-secondary';
}

function formatCheckedAt(value: string | null | undefined): string {
  if (!value) {
    return 'No samples yet';
  }
  const hasTimezone = /[zZ]|[+-]\d{2}:\d{2}$/.test(value);
  const normalized = hasTimezone ? value : `${value}Z`;
  const timestamp = Date.parse(normalized);
  if (Number.isNaN(timestamp)) {
    return 'Invalid date';
  }
  return new Date(timestamp).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

interface SiteMonitorRowProps {
  site: SiteMonitorStatus;
  expanded: boolean;
  onToggle: (siteId: number) => void;
}

const SiteMonitorRow = memo(function SiteMonitorRow({ site, expanded, onToggle }: SiteMonitorRowProps) {
  return (
    <article
      className={classNames('site-monitor-row card-panel rounded-4 p-3', {
        'site-monitor-row--expanded': expanded,
      })}
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      onClick={() => onToggle(site.id)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onToggle(site.id);
        }
      }}
    >
      <div className="site-monitor-row__summary">
        <div className="site-monitor-row__content">
          <div className="site-monitor-row__identity">
            <span className="site-monitor-row__title" title={site.name}>
              {site.name}
            </span>
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
      </div>
      {expanded && (
        <div className="site-monitor-row__details">
          <span className="badge bg-secondary text-uppercase">{site.check_type}</span>
          <span className={classNames('badge text-uppercase', getStatusBadgeClass(site.status))}>{site.status}</span>
          <span className="site-monitor-row__value">{site.display_value}</span>
          <span className="site-monitor-row__target">{site.target}</span>
          <span>Last check: {formatCheckedAt(site.checked_at)}</span>
          {site.consecutive_failures > 0 && <span>{site.consecutive_failures} consecutive failures</span>}
          {site.detail && <span className="site-monitor-row__detail text-warning">{site.detail}</span>}
        </div>
      )}
    </article>
  );
});

export function SiteMonitoring() {
  const polling = usePolling(fetchSiteMonitorStatuses, { intervalMs: SITE_MONITOR_INTERVAL_MS });
  const sites = polling.data ?? [];
  const [currentPage, setCurrentPage] = useState(1);
  const [expandedSiteId, setExpandedSiteId] = useState<number | null>(null);
  const [reloading, setReloading] = useState(false);

  useEffect(() => {
    const totalPages = Math.max(1, Math.ceil(sites.length / SITE_MONITOR_PAGE_SIZE));
    setCurrentPage((prev) => Math.min(prev, totalPages));
  }, [sites.length]);

  const toggleExpanded = useCallback((siteId: number) => {
    setExpandedSiteId((current) => (current === siteId ? null : siteId));
  }, []);

  const handleReload = async () => {
    setReloading(true);
    try {
      await polling.refresh();
    } finally {
      setReloading(false);
    }
  };

  const error = polling.error
    ? getUserFacingErrorMessage(polling.error, 'Could not load site monitoring data. Check the API connection and try again.')
    : null;
  const loading = polling.loading && polling.data === null;
  const totalPages = Math.max(1, Math.ceil(sites.length / SITE_MONITOR_PAGE_SIZE));
  const pageStart = (currentPage - 1) * SITE_MONITOR_PAGE_SIZE;
  const pagedSites = sites.slice(pageStart, pageStart + SITE_MONITOR_PAGE_SIZE);
  const visibleStart = sites.length === 0 ? 0 : pageStart + 1;
  const visibleEnd = Math.min(sites.length, pageStart + pagedSites.length);

  return (
    <div className="site-monitoring-page d-flex flex-column gap-4">
      <div className="page-intro">
        <div className="page-intro__copy">
          <h2 className="page-intro__title">Site Monitoring</h2>
          <p className="page-intro__description mb-0">
            Reachability and HTTP checks with 48 half-hour results covering the last 24 hours.
          </p>
        </div>
        <button
          className="btn btn-outline-light"
          type="button"
          onClick={() => {
            void handleReload();
          }}
          disabled={reloading}
        >
          Reload
        </button>
      </div>

      {error && <div className="alert alert-danger mb-0">{error}</div>}

      {loading ? (
        <div className="text-secondary">Loading site checks…</div>
      ) : sites.length === 0 ? (
        !error && (
          <div className="card-panel rounded-4 p-4">
            <div className="fw-semibold mb-1">No site checks configured</div>
            <div className="text-secondary small">Add ping or HTTP checks from Settings to populate this view.</div>
          </div>
        )
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
              <SiteMonitorRow key={site.id} site={site} expanded={expandedSiteId === site.id} onToggle={toggleExpanded} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
