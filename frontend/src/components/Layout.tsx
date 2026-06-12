import { ReactNode, useEffect, useMemo, useState } from 'react';
import type { IconType } from 'react-icons';
import { FiActivity, FiBarChart2, FiChevronDown, FiChevronUp, FiFolder, FiGlobe, FiLogOut, FiMenu, FiMessageSquare, FiSettings } from 'react-icons/fi';
import {
  AuthUser,
  NotificationEvent,
  fetchBackendVersion,
  fetchNotifications,
  fetchProjects,
  markNotificationsRead,
  Project,
} from '../api/client';
import { DEFAULT_BACKEND_VERSION, FRONTEND_VERSION } from '../constants/versions';
import { formatNotificationDeliveryError, getUserFacingErrorMessage } from '../utils/errors';

export type AppView = 'monitoring' | 'site-monitoring' | 'graphs' | 'notifications' | 'admin' | `project:${number}`;

interface LayoutProps {
  activeView: AppView;
  onSwitch: (view: AppView) => void;
  currentUser: AuthUser;
  onLogout: () => void;
  children: ReactNode;
}

const VIEW_META: Record<
  Exclude<AppView, `project:${number}`>,
  { label: string; description: string; navDescription: string; icon: IconType }
> = {
  monitoring: {
    label: 'Monitoring',
    description: 'Quick tiles and notifications for the current fleet state.',
    navDescription: 'Quick tiles',
    icon: FiActivity,
  },
  'site-monitoring': {
    label: 'Site Monitoring',
    description: 'Ping and HTTP checks for public sites and external services.',
    navDescription: 'Sites and history',
    icon: FiGlobe,
  },
  graphs: {
    label: 'Graphs',
    description: 'All backend charts and time-window navigation.',
    navDescription: 'All graphs',
    icon: FiBarChart2,
  },
  notifications: {
    label: 'Notification Center',
    description: 'Notification history across Telegram and local quick-status events.',
    navDescription: 'History and unread alerts',
    icon: FiMessageSquare,
  },
  admin: {
    label: 'Settings',
    description: 'Backends, dashboard items, notifications, and system controls.',
    navDescription: 'Configuration',
    icon: FiSettings,
  },
};

export function getProjectViewId(view: AppView): number | null {
  if (!view.startsWith('project:')) {
    return null;
  }
  const parsed = Number(view.replace('project:', ''));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function Layout({ activeView, onSwitch, currentUser, onLogout, children }: LayoutProps) {
  const notificationPageSize = 20;
  const [backendVersion, setBackendVersion] = useState(DEFAULT_BACKEND_VERSION);
  const [menuOpen, setMenuOpen] = useState(false);
  const [notifications, setNotifications] = useState<NotificationEvent[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [notificationsLoading, setNotificationsLoading] = useState(false);
  const [notificationsError, setNotificationsError] = useState<string | null>(null);
  const [notificationPage, setNotificationPage] = useState(1);
  const [notificationTotalPages, setNotificationTotalPages] = useState(1);
  const [notificationTotalItems, setNotificationTotalItems] = useState(0);
  const [expandedNotificationIds, setExpandedNotificationIds] = useState<Set<number>>(new Set());
  const [projects, setProjects] = useState<Project[]>([]);

  const activeProjectId = getProjectViewId(activeView);
  const activeProject = activeProjectId !== null ? projects.find((project) => project.id === activeProjectId) ?? null : null;
  const viewMeta =
    activeProject && activeProjectId !== null
      ? {
          label: activeProject.name,
          description: 'Custom quick tiles for this project.',
          navDescription: 'Project tiles',
          icon: FiFolder,
        }
      : VIEW_META[(activeView in VIEW_META ? activeView : 'monitoring') as Exclude<AppView, `project:${number}`>];
  const canOpenAdmin = currentUser.role !== 'viewer';
  const loadProjectsMenu = async () => {
    try {
      const data = await fetchProjects();
      setProjects(
        [...data].sort((a, b) => {
          if (a.display_order !== b.display_order) {
            return a.display_order - b.display_order;
          }
          return a.name.localeCompare(b.name);
        })
      );
    } catch {
      setProjects([]);
    }
  };

  useEffect(() => {
    let cancelled = false;
    const handleProjectsUpdated = () => {
      void loadProjectsMenu();
    };
    void (async () => {
      try {
        const data = await fetchProjects();
        if (!cancelled) {
          setProjects(
            [...data].sort((a, b) => {
              if (a.display_order !== b.display_order) {
                return a.display_order - b.display_order;
              }
              return a.name.localeCompare(b.name);
            })
          );
        }
      } catch {
        if (!cancelled) {
          setProjects([]);
        }
      }
    })();
    window.addEventListener('projects-updated', handleProjectsUpdated);
    return () => {
      cancelled = true;
      window.removeEventListener('projects-updated', handleProjectsUpdated);
    };
  }, []);

  useEffect(() => {
    if (activeProjectId !== null && activeProject === null && projects.length > 0) {
      onSwitch('monitoring');
    }
  }, [activeProject, activeProjectId, onSwitch, projects.length]);

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
    onSwitch('notifications');
    setNotificationsLoading(true);
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

  const toggleNotificationExpanded = (id: number) => {
    setExpandedNotificationIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
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

  const notificationCenterContent = (
    <section className="notification-center-page card-panel rounded-4 p-3 p-md-4">
      <div className="notification-center-page__header">
        <div>
          <h2 className="fw-semibold text-uppercase mb-1">History</h2>
          <p className="text-secondary small mb-0">
            Alerts, recoveries, Telegram delivery status, and local quick-status events.
          </p>
        </div>
        <button
          className="btn btn-outline-light"
          type="button"
          onClick={() => {
            void loadNotificationPage(notificationPage);
          }}
          disabled={notificationsLoading}
        >
          Reload
        </button>
      </div>
      <div className="d-flex flex-column gap-3 notification-center-body">
        {notificationsLoading && <div className="text-secondary small">Loading notifications...</div>}
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
              const isExpanded = expandedNotificationIds.has(item.id);
              return (
                <article
                  key={item.id}
                  className={`notification-entry${isExpanded ? ' notification-entry--expanded' : ''}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => toggleNotificationExpanded(item.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      toggleNotificationExpanded(item.id);
                    }
                  }}
                >
                  <div className="notification-entry__summary">
                    <div className="notification-entry__meta">
                      <span className={`badge text-uppercase ${getSeverityBadgeClass(item.severity)}`}>{item.severity}</span>
                      {item.delivery_status === 'failed' && <span className="badge bg-danger">failed</span>}
                      {item.backend_name && <span className="badge bg-dark border border-secondary">{item.backend_name}</span>}
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
    </section>
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
          <div className="app-sidebar__title">Server Monitor</div>
          <div className="app-sidebar__quote">Fleet health, projects, alerts, and configuration.</div>
        </div>

        <nav className="app-sidebar__nav" aria-label="Primary">
          {(Object.entries(VIEW_META) as Array<[Exclude<AppView, `project:${number}`>, (typeof VIEW_META)[Exclude<AppView, `project:${number}`>]]>).map(([view, meta]) => {
            const Icon = meta.icon;
            const isActive = activeView === view;
            const isDisabled = view === 'admin' && !canOpenAdmin;
            return (
              <button
                key={view}
                type="button"
                className={`app-nav-link ${isActive ? 'app-nav-link--active' : ''}`}
                onClick={() => {
                  if (view === 'notifications') {
                    void handleOpenNotifications();
                    return;
                  }
                  onSwitch(view);
                }}
                disabled={isDisabled}
              >
                <span className="app-nav-link__icon">
                  <Icon size={18} />
                </span>
                <span className="app-nav-link__copy">
                  <span className="app-nav-link__title">{meta.label}</span>
                  <span className="app-nav-link__description">{meta.navDescription}</span>
                </span>
                {view === 'notifications' && unreadCount > 0 && (
                  <span className="app-nav-link__badge">{unreadCount > 99 ? '99+' : unreadCount}</span>
                )}
              </button>
            );
          })}
          {projects.map((project) => {
            const view = `project:${project.id}` as AppView;
            const isActive = activeView === view;
            return (
              <button
                key={view}
                type="button"
                className={`app-nav-link ${isActive ? 'app-nav-link--active' : ''}`}
                onClick={() => onSwitch(view)}
              >
                <span className="app-nav-link__icon">
                  <FiFolder size={18} />
                </span>
                <span className="app-nav-link__copy">
                  <span className="app-nav-link__title">{project.name}</span>
                  <span className="app-nav-link__description">Project tiles</span>
                </span>
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
              <div className="app-header__eyebrow">Control Center</div>
              <h1 className="app-header__title">{viewMeta.label}</h1>
              <p className="app-header__description mb-0">{viewMeta.description}</p>
            </div>
          </div>
          <div className="d-flex flex-wrap align-items-center justify-content-end gap-2">
            {activeView !== 'notifications' && (
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

        <main className="app-main">{activeView === 'notifications' ? notificationCenterContent : children}</main>
      </div>
    </div>
  );
}
