import { ReactNode, useEffect, useState } from 'react';
import { FiMessageSquare, FiX } from 'react-icons/fi';
import {
  AuthUser,
  NotificationEvent,
  fetchBackendVersion,
  fetchNotifications,
  markNotificationsRead,
} from '../api/client';
import { DEFAULT_BACKEND_VERSION, FRONTEND_VERSION } from '../constants/versions';

interface LayoutProps {
  activeView: 'dashboard' | 'admin';
  onSwitch: (view: 'dashboard' | 'admin') => void;
  currentUser: AuthUser;
  onLogout: () => void;
  children: ReactNode;
}

export function Layout({ activeView, onSwitch, currentUser, onLogout, children }: LayoutProps) {
  const [backendVersion, setBackendVersion] = useState(DEFAULT_BACKEND_VERSION);
  const [menuOpen, setMenuOpen] = useState(false);
  const [notifications, setNotifications] = useState<NotificationEvent[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [notificationsLoading, setNotificationsLoading] = useState(false);
  const [notificationsError, setNotificationsError] = useState<string | null>(null);

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

    const loadNotifications = async () => {
      try {
        const data = await fetchNotifications(50);
        if (cancelled) return;
        setNotifications(data.items);
        setUnreadCount(data.unread_count);
        setNotificationsError(null);
      } catch (error) {
        if (!cancelled) {
          setNotificationsError(error instanceof Error ? error.message : 'Unable to load notifications.');
        }
      }
    };

    void loadNotifications();
    const timer = window.setInterval(() => {
      void loadNotifications();
    }, 30000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const handleOpenNotifications = async () => {
    setNotificationsOpen(true);
    setNotificationsLoading(true);
    try {
      const [center] = await Promise.all([fetchNotifications(100), markNotificationsRead()]);
      setNotifications(center.items);
      setUnreadCount(0);
      setNotificationsError(null);
    } catch (error) {
      setNotificationsError(error instanceof Error ? error.message : 'Unable to load notifications.');
    } finally {
      setNotificationsLoading(false);
    }
  };

  const handleCloseNotifications = () => {
    setNotificationsOpen(false);
  };

  return (
    <div className="min-vh-100 bg-dark text-light">
      <nav className="navbar navbar-expand-lg navbar-dark border-bottom border-secondary">
        <div className="container-fluid">
          <div className="d-flex align-items-center justify-content-between w-100 gap-3">
            <span className="navbar-brand brand-title fw-semibold text-uppercase mb-0">
              Virgilio - System Monitoring
              <small
                className="d-block text-secondary fw-normal fst-italic"
                style={{ fontSize: '0.58rem', lineHeight: 1.05, letterSpacing: '0.02em' }}
              >
                Lasciate ogni speranza, voi che entrate
              </small>
            </span>
            <div className="d-flex align-items-center gap-2">
              <button
                className="btn btn-sm btn-outline-light position-relative notification-trigger"
                type="button"
                aria-label="Open notification center"
                onClick={() => {
                  void handleOpenNotifications();
                }}
              >
                <FiMessageSquare size={16} />
                {unreadCount > 0 && (
                  <span className="notification-trigger__badge">
                    {unreadCount > 99 ? '99+' : unreadCount}
                  </span>
                )}
              </button>
              <button
                className="navbar-toggler"
                type="button"
                aria-expanded={menuOpen}
                aria-label="Toggle navigation"
                onClick={() => setMenuOpen((open) => !open)}
              >
                <span className="navbar-toggler-icon" />
              </button>
            </div>
          </div>
          <div className={`collapse navbar-collapse ${menuOpen ? 'show' : ''}`}>
            <div className="d-flex flex-column flex-lg-row align-items-lg-center gap-3 w-100">
              {activeView === 'admin' ? (
                <div className="d-flex flex-wrap align-items-center gap-2">
                  <span className="badge bg-secondary">Frontend v{FRONTEND_VERSION}</span>
                  <span className="badge bg-secondary">Backend v{backendVersion}</span>
                </div>
              ) : (
                <div />
              )}
              <div className="d-flex flex-column flex-lg-row align-items-lg-center gap-2 ms-lg-auto">
                <div className="btn-group" role="group">
                  <button
                    className={`btn btn-sm ${activeView === 'dashboard' ? 'btn-light text-dark' : 'btn-outline-light'}`}
                    onClick={() => onSwitch('dashboard')}
                  >
                    Dashboard
                  </button>
                  <button
                    className={`btn btn-sm ${activeView === 'admin' ? 'btn-light text-dark' : 'btn-outline-light'}`}
                    onClick={() => onSwitch('admin')}
                  >
                    Admin
                  </button>
                </div>
                <div className="d-flex flex-wrap align-items-center gap-2">
                  <span className="badge bg-secondary text-uppercase">{currentUser.role}</span>
                  <span className="small text-secondary">Signed in as {currentUser.username}</span>
                  <button className="btn btn-sm btn-outline-light" type="button" onClick={onLogout}>
                    Sign out
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </nav>
      <main className="container py-4">{children}</main>
      {notificationsOpen && (
        <div className="notification-center-backdrop" role="presentation" onClick={handleCloseNotifications}>
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
                <div className="text-secondary small">Telegram delivery log and fallback inbox.</div>
              </div>
              <button className="btn btn-sm btn-outline-light" type="button" onClick={handleCloseNotifications}>
                <FiX size={16} />
              </button>
            </div>
            <div className="card-body d-flex flex-column gap-3 notification-center-body">
              {notificationsLoading && <div className="text-secondary small">Loading notifications…</div>}
              {notificationsError && <div className="alert alert-danger mb-0">{notificationsError}</div>}
              {!notificationsLoading && !notifications.length && !notificationsError && (
                <div className="text-secondary small">No notifications yet.</div>
              )}
              {!notificationsLoading && notifications.length > 0 && (
                <div className="d-flex flex-column gap-2 notification-center-list">
                  {notifications.map((item) => (
                    <article key={item.id} className="notification-entry">
                      <div className="d-flex flex-wrap align-items-center gap-2 mb-2">
                        <span className={`badge ${item.delivery_status === 'failed' ? 'bg-danger' : 'bg-success'}`}>
                          {item.delivery_status}
                        </span>
                        <span className="badge bg-secondary text-uppercase">{item.severity}</span>
                        {item.backend_name && <span className="badge bg-dark border border-secondary">{item.backend_name}</span>}
                        <span className="small text-secondary">{new Date(item.created_at).toLocaleString()}</span>
                      </div>
                      <div className="fw-semibold mb-1">{item.title}</div>
                      <pre className="notification-entry__body">{item.body}</pre>
                      {item.error_message && <div className="small text-danger">Telegram error: {item.error_message}</div>}
                    </article>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
