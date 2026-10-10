import { ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import type { IconType } from 'react-icons';
import { FiActivity, FiBarChart2, FiGlobe, FiLogOut, FiMenu, FiMessageSquare, FiSettings } from 'react-icons/fi';
import { AuthUser, fetchBackendVersion, fetchNotificationsUnreadCount } from '../api/client';
import { DEFAULT_BACKEND_VERSION, FRONTEND_VERSION } from '../constants/versions';
import { usePolling } from '../hooks/usePolling';
import { NotificationCenter } from './NotificationCenter';

const UNREAD_INTERVAL_MS = 60_000;

export type AppView = 'monitoring' | 'site-monitoring' | 'graphs' | 'notifications' | 'admin';

interface LayoutProps {
  activeView: AppView;
  onSwitch: (view: AppView) => void;
  currentUser: AuthUser;
  onLogout: () => void;
  children: ReactNode;
}

const VIEW_META: Record<AppView, { label: string; description: string; navDescription: string; icon: IconType }> = {
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

export function Layout({ activeView, onSwitch, currentUser, onLogout, children }: LayoutProps) {
  const [backendVersion, setBackendVersion] = useState(DEFAULT_BACKEND_VERSION);
  const [menuOpen, setMenuOpen] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);
  const viewMeta = VIEW_META[activeView];
  const canOpenAdmin = currentUser.role !== 'viewer';

  // Badge only: one tiny COUNT request per minute instead of a full notification page.
  const unread = usePolling(fetchNotificationsUnreadCount, { intervalMs: UNREAD_INTERVAL_MS });
  useEffect(() => {
    if (unread.data !== null) {
      setUnreadCount(unread.data);
    }
  }, [unread.data]);

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

  const handleOpenNotifications = () => {
    onSwitch('notifications');
  };

  const handleMarkedRead = useCallback(() => {
    setUnreadCount(0);
  }, []);

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
          <div className="app-sidebar__title">Server Monitor</div>
          <div className="app-sidebar__quote">Fleet health, alerts, and configuration.</div>
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
                onClick={() => {
                  if (view === 'notifications') {
                    handleOpenNotifications();
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
                onClick={handleOpenNotifications}
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

        <main className="app-main">
          {activeView === 'notifications' ? <NotificationCenter onMarkedRead={handleMarkedRead} /> : children}
        </main>
      </div>
    </div>
  );
}
