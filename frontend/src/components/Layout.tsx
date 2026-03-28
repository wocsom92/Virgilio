import { ReactNode, useEffect, useMemo, useState } from 'react';
import type { IconType } from 'react-icons';
import { FiActivity, FiBarChart2, FiLogOut, FiMenu, FiMessageSquare, FiSettings, FiX } from 'react-icons/fi';
import {
  AuthUser,
  NotificationEvent,
  fetchBackendVersion,
  fetchNotifications,
  markNotificationsRead,
} from '../api/client';
import { DEFAULT_BACKEND_VERSION, FRONTEND_VERSION } from '../constants/versions';
import { formatNotificationDeliveryError, getUserFacingErrorMessage } from '../utils/errors';

export type AppView = 'monitoring' | 'graphs' | 'admin';

interface LayoutProps {
  activeView: AppView;
  onSwitch: (view: AppView) => void;
  currentUser: AuthUser;
  onLogout: () => void;
  children: ReactNode;
}

const VIEW_META: Record<
  AppView,
  { label: string; description: string; navDescription: string; icon: IconType }
> = {
  monitoring: {
    label: 'Monitoring',
    description: 'Quick tiles and notifications for the current fleet state.',
    navDescription: 'Quick tiles',
    icon: FiActivity,
  },
  graphs: {
    label: 'Graphs',
    description: 'All backend charts and time-window navigation.',
    navDescription: 'All graphs',
    icon: FiBarChart2,
  },
  admin: {
    label: 'Administration',
    description: 'Configuration, access control, and host operations.',
    navDescription: 'Admin menu',
    icon: FiSettings,
  },
};

export function Layout({ activeView, onSwitch, currentUser, onLogout, children }: LayoutProps) {
  const notificationPageSize = 20;
  const [backendVersion, setBackendVersion] = useState(DEFAULT_BACKEND_VERSION);
  const [menuOpen, setMenuOpen] = useState(false);
  const [notifications, setNotifications] = useState<NotificationEvent[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [notificationsLoading, setNotificationsLoading] = useState(false);
  const [notificationsError, setNotificationsError] = useState<string | null>(null);
  const [notificationPage, setNotificationPage] = useState(1);
  const [notificationTotalPages, setNotificationTotalPages] = useState(1);
  const [notificationTotalItems, setNotificationTotalItems] = useState(0);

  const viewMeta = VIEW_META[activeView];
  const canOpenAdmin = currentUser.role !== 'viewer';

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const version = await fetchBackendVersion();
        if (!cancelled && version) {
          setBackendVersion(version);
        }
      } catch {
        // Keep default version when the API is unavailable.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setMenuOpen(false);
  }, [activeView]);

  useEffect(() => {
    let cancelled = false;

    const loadNotifications = async (page = 1) => {
      try {
        const data = await fetchNotifications(page, notificationPageSize);
        if (cancelled) {
          return;
        }
        setNotifications(data.items);
        setUnreadCount(data.unread_count);
        setNotificationPage(data.page);
        setNotificationTotalPages(data.total_pages);
        setNotificationTotalItems(data.total_items);
        setNotificationsError(null);
      } catch (error) {
        if (!cancelled) {
          setNotificationsError(
            getUserFacingErrorMessage(error, 'Could not load notification history. Check the API connection and try again.')
          );
        }
      }
    };

    void loadNotifications(notificationPage);
    const timer = window.setInterval(() => {
      void loadNotifications(notificationPage);
    }, 30000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [notificationPage]);

  const loadNotificationPage = async (page: number) => {
    setNotificationsLoading(true);
    try {
      const data = await fetchNotifications(page, notificationPageSize);
      setNotifications(data.items);
      setUnreadCount(data.unread_count);
      setNotificationPage(data.page);
      setNotificationTotalPages(data.total_pages);
      setNotificationTotalItems(data.total_items);
      setNotificationsError(null);
    } catch (error) {
      setNotificationsError(
        getUserFacingErrorMessage(error, 'Could not load notification history. Check the API connection and try again.')
      );
    } finally {
      setNotificationsLoading(false);
    }
  };

  const handleOpenNotifications = async () => {
    setNotificationsOpen(true);
    try {
      await markNotificationsRead();
      setUnreadCount(0);
      await loadNotificationPage(1);
    } catch (error) {
      setNotificationsLoading(false);
      setNotificationsError(
        getUserFacingErrorMessage(error, 'Could not load notification history. Check the API connection and try again.')
      );
    }
  };

  const handleChangeNotificationPage = async (page: number) => {
    if (page < 1 || page > notificationTotalPages || page === notificationPage) {
      return;
    }
    await loadNotificationPage(page);
  };

  const notificationRangeStart = notificationTotalItems === 0 ? 0 : (notificationPage - 1) * notificationPageSize + 1;
  const notificationRangeEnd = Math.min(notificationPage * notificationPageSize, notificationTotalItems);
  const getSeverityBadgeClass = (severity: string) => {
    if (severity === 'critical') {
      return 'bg-danger';
    }
    if (severity === 'warn') {
      return 'bg-warning text-dark';
    }
    if (severity === 'ok') {
      return 'bg-success';
    }
    return 'bg-secondary';
  };

  const adminVersionBadges = useMemo(
    () => (
      <div className="d-flex flex-wrap align-items-center gap-2">
        <span className="badge bg-secondary">Frontend v{FRONTEND_VERSION}</span>
        <span className="badge bg-secondary">Backend v{backendVersion}</span>
      </div>
    ),
    [backendVersion]
  );

  return (
    <div className="app-shell">
      {menuOpen && (
        <button
          type="button"
          className="app-shell__backdrop"
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside className={`app-sidebar ${menuOpen ? 'app-sidebar--open' : ''}`}>
        <div className="app-sidebar__brand">
          <div className="app-sidebar__eyebrow">Virgilio</div>
          <div className="app-sidebar__title">System Monitoring</div>
          <div className="app-sidebar__quote">Lasciate ogni speranza, voi che entrate</div>
        </div>

        <nav className="app-sidebar__nav" aria-label="Primary">
          {(Object.entries(VIEW_META) as Array<[AppView, (typeof VIEW_META)[AppView]]>).map(([view, meta]) => {
            const Icon = meta.icon;
            const isActive = activeView === view;
            const isDisabled = view === 'admin' && !canOpenAdmin;
            return (
              <button
                key={view}
                type="button"
                className={`app-nav-link ${isActive ? 'app-nav-link--active' : ''}`}
                onClick={() => onSwitch(view)}
                disabled={isDisabled}
              >
                <span className="app-nav-link__icon">
                  <Icon size={18} />
                </span>
                <span className="app-nav-link__copy">
                  <span className="app-nav-link__title">{meta.label}</span>
                  <span className="app-nav-link__description">{meta.navDescription}</span>
                </span>
                {view === 'monitoring' && unreadCount > 0 && (
                  <span className="app-nav-link__badge">{unreadCount > 99 ? '99+' : unreadCount}</span>
                )}
              </button>
            );
          })}
        </nav>

        <div className="app-sidebar__footer">
          <div className="d-flex flex-wrap align-items-center gap-2">
            <span className="badge bg-secondary text-uppercase">{currentUser.role}</span>
            <span className="small text-secondary">Signed in as {currentUser.username}</span>
          </div>
          <div className="d-flex flex-wrap gap-2">
            <span className="badge bg-secondary">Frontend v{FRONTEND_VERSION}</span>
            <span className="badge bg-secondary">Backend v{backendVersion}</span>
          </div>
          <button className="btn btn-sm btn-outline-light app-sidebar__signout" type="button" onClick={onLogout}>
            <FiLogOut size={16} />
            <span>Sign out</span>
          </button>
        </div>
      </aside>

      <div className="app-content">
        <header className="app-header">
          <div className="d-flex align-items-start gap-3">
            <button
              className="btn btn-sm btn-outline-light d-xl-none"
              type="button"
              aria-label="Open navigation"
              onClick={() => setMenuOpen(true)}
            >
              <FiMenu size={18} />
            </button>
            <div>
              <div className="app-header__eyebrow">Workspace</div>
              <h1 className="app-header__title">{viewMeta.label}</h1>
              <p className="app-header__description mb-0">{viewMeta.description}</p>
            </div>
          </div>
          <div className="d-flex flex-wrap align-items-center justify-content-end gap-2">
            {activeView === 'monitoring' && (
              <button
                className="btn btn-sm btn-outline-light position-relative notification-trigger"
                type="button"
                aria-label="Open notification center"
                onClick={() => {
                  void handleOpenNotifications();
                }}
              >
                <FiMessageSquare size={16} />
                <span>Notifications</span>
                {unreadCount > 0 && (
                  <span className="notification-trigger__badge">
                    {unreadCount > 99 ? '99+' : unreadCount}
                  </span>
                )}
              </button>
            )}
            {activeView === 'admin' && adminVersionBadges}
          </div>
        </header>

        <main className="app-main">{children}</main>
      </div>

      {notificationsOpen && (
        <div className="notification-center-backdrop" role="presentation" onClick={() => setNotificationsOpen(false)}>
          <div
            className="notification-center-modal card shadow-lg"
            role="dialog"
            aria-modal="true"
            aria-label="Notification center"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="card-header d-flex align-items-center justify-content-between">
              <div>
                <div className="text-uppercase small fw-semibold">Notification Center</div>
                <div className="text-secondary small">Notification history across Telegram and local quick-status events.</div>
              </div>
              <button className="btn btn-sm btn-outline-light" type="button" onClick={() => setNotificationsOpen(false)}>
                <FiX size={16} />
              </button>
            </div>
            <div className="card-body d-flex flex-column gap-3 notification-center-body">
              {notificationsLoading && <div className="text-secondary small">Loading notifications…</div>}
              {notificationsError && <div className="alert alert-danger mb-0">{notificationsError}</div>}
              {!notificationsLoading && !notificationsError && notificationTotalItems > 0 && (
                <div className="d-flex align-items-center justify-content-between gap-2 small text-secondary">
                  <span>
                    Showing {notificationRangeStart}-{notificationRangeEnd} of {notificationTotalItems}
                  </span>
                  <span>
                    Page {notificationPage} / {notificationTotalPages}
                  </span>
                </div>
              )}
              {!notificationsLoading && !notifications.length && !notificationsError && (
                <div className="text-secondary small">No notifications yet.</div>
              )}
              {!notificationsLoading && notifications.length > 0 && (
                <div className="d-flex flex-column gap-2 notification-center-list">
                  {notifications.map((item) => {
                    const deliveryError = formatNotificationDeliveryError(item.error_message);
                    return (
                      <article key={item.id} className="notification-entry">
                        <div className="d-flex flex-wrap align-items-center gap-2 mb-2">
                          <span
                            className={`badge ${
                              item.delivery_status === 'failed'
                                ? 'bg-danger'
                                : item.delivery_status === 'local'
                                  ? 'bg-secondary'
                                  : 'bg-success'
                            }`}
                          >
                            {item.delivery_status}
                          </span>
                          <span className={`badge text-uppercase ${getSeverityBadgeClass(item.severity)}`}>{item.severity}</span>
                          {item.backend_name && <span className="badge bg-dark border border-secondary">{item.backend_name}</span>}
                          <span className="small text-secondary">{new Date(item.created_at).toLocaleString()}</span>
                        </div>
                        <div className="fw-semibold mb-1">{item.title}</div>
                        <pre className="notification-entry__body">{item.body}</pre>
                        {deliveryError && <div className="small text-danger">Delivery error: {deliveryError}</div>}
                      </article>
                    );
                  })}
                </div>
              )}
              {!notificationsLoading && !notificationsError && notificationTotalPages > 1 && (
                <div className="d-flex align-items-center justify-content-between gap-2">
                  <button
                    className="btn btn-sm btn-outline-light"
                    type="button"
                    disabled={notificationPage <= 1}
                    onClick={() => {
                      void handleChangeNotificationPage(notificationPage - 1);
                    }}
                  >
                    Previous
                  </button>
                  <button
                    className="btn btn-sm btn-outline-light"
                    type="button"
                    disabled={notificationPage >= notificationTotalPages}
                    onClick={() => {
                      void handleChangeNotificationPage(notificationPage + 1);
                    }}
                  >
                    Next
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
