import { useCallback, useEffect, useRef, useState } from 'react';
import { FiChevronDown, FiChevronUp } from 'react-icons/fi';

import { NotificationEvent, fetchNotifications, isCancelledError, markNotificationsRead } from '../api/client';
import { formatNotificationDeliveryError, getUserFacingErrorMessage } from '../utils/errors';

const PAGE_SIZE = 20;

interface NotificationCenterProps {
  /** Called once the unread events have been marked as read on the server. */
  onMarkedRead: () => void;
}

function severityKey(severity: string): 'ok' | 'warn' | 'critical' | 'info' {
  if (severity === 'critical' || severity === 'warn' || severity === 'ok') {
    return severity;
  }
  return 'info';
}

function getSeverityBadgeClass(severity: string) {
  return `badge-${severityKey(severity)}`;
}

/** Notification history. Mounted only while the view is open; no background polling. */
export function NotificationCenter({ onMarkedRead }: NotificationCenterProps) {
  const [items, setItems] = useState<NotificationEvent[]>([]);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalItems, setTotalItems] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const controllerRef = useRef<AbortController | null>(null);

  const loadPage = useCallback(async (targetPage: number) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setLoading(true);
    try {
      const data = await fetchNotifications(targetPage, PAGE_SIZE, controller.signal);
      if (controller.signal.aborted) {
        return;
      }
      setItems(data.items);
      setPage(data.page);
      setTotalPages(data.total_pages);
      setTotalItems(data.total_items);
      setError(null);
    } catch (err) {
      if (isCancelledError(err) || controller.signal.aborted) {
        return;
      }
      setError(
        getUserFacingErrorMessage(err, 'Could not load notification history. Check the API connection and try again.')
      );
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await markNotificationsRead();
        if (!cancelled) {
          onMarkedRead();
        }
      } catch {
        // The list still loads; the badge simply keeps its previous count.
      }
      if (!cancelled) {
        await loadPage(1);
      }
    })();
    return () => {
      cancelled = true;
      controllerRef.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadPage]);

  const toggleExpanded = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const rangeStart = totalItems === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const rangeEnd = Math.min(page * PAGE_SIZE, totalItems);

  return (
    <section className="notification-center-page card-panel rounded-4 p-3 p-md-4">
      <div className="notification-center-page__header">
        <div>
          <h2 className="fw-semibold text-uppercase mb-1">History</h2>
          <p className="text-secondary small mb-0">
            Alerts, recoveries, Telegram delivery status, and local quick-status events.
          </p>
        </div>
        <button
          className="btn btn-ghost"
          type="button"
          onClick={() => {
            void loadPage(page);
          }}
          disabled={loading}
        >
          Reload
        </button>
      </div>
      <div className="d-flex flex-column gap-3 notification-center-body">
        {loading && <div className="text-secondary small">Loading notifications...</div>}
        {error && <div className="alert alert-danger mb-0">{error}</div>}
        {!loading && !error && totalItems > 0 && (
          <div className="d-flex align-items-center justify-content-between gap-2 small text-secondary">
            <span>
              Showing {rangeStart}-{rangeEnd} of {totalItems}
            </span>
            <span>
              Page {page} / {totalPages}
            </span>
          </div>
        )}
        {!loading && !items.length && !error && <div className="text-secondary small">No notifications yet.</div>}
        {!loading && items.length > 0 && (
          <div className="d-flex flex-column gap-2 notification-center-list">
            {items.map((item) => {
              const deliveryError = formatNotificationDeliveryError(item.error_message);
              const isExpanded = expandedIds.has(item.id);
              return (
                <article
                  key={item.id}
                  className={`notification-entry notification-entry--${severityKey(item.severity)}${isExpanded ? ' notification-entry--expanded' : ''}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => toggleExpanded(item.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      toggleExpanded(item.id);
                    }
                  }}
                >
                  <div className="notification-entry__summary">
                    <div className="notification-entry__meta">
                      <span className={`badge text-uppercase ${getSeverityBadgeClass(item.severity)}`}>{item.severity}</span>
                      {item.delivery_status === 'failed' && <span className="badge badge-critical">failed</span>}
                      {item.backend_name && <span className="badge">{item.backend_name}</span>}
                      <span className="text-secondary notification-entry__date">{new Date(item.created_at).toLocaleString()}</span>
                    </div>
                    <span className="notification-entry__title">{item.title}</span>
                    <span className="notification-entry__chevron">
                      {isExpanded ? <FiChevronUp size={14} /> : <FiChevronDown size={14} />}
                    </span>
                  </div>
                  {isExpanded && (
                    <div className="notification-entry__details">
                      <pre className="notification-entry__body">{item.body}</pre>
                      {deliveryError && <div className="small text-danger">Delivery error: {deliveryError}</div>}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
        {!loading && !error && totalPages > 1 && (
          <div className="d-flex align-items-center justify-content-between gap-2">
            <button
              className="btn btn-sm btn-ghost"
              type="button"
              disabled={page <= 1}
              onClick={() => {
                void loadPage(page - 1);
              }}
            >
              Previous
            </button>
            <button
              className="btn btn-sm btn-ghost"
              type="button"
              disabled={page >= totalPages}
              onClick={() => {
                void loadPage(page + 1);
              }}
            >
              Next
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
