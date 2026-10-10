import { ReactNode, useCallback, useEffect, useState } from 'react';
import type { IconType } from 'react-icons';
import {
  FiActivity,
  FiBarChart2,
  FiBell,
  FiGlobe,
  FiLogOut,
  FiMoon,
  FiSettings,
  FiSun,
} from 'react-icons/fi';
import { AuthUser, fetchBackendVersion, fetchNotificationsUnreadCount } from '../api/client';
import { DEFAULT_BACKEND_VERSION, FRONTEND_VERSION } from '../constants/versions';
import { usePolling } from '../hooks/usePolling';
import { useTheme } from '../hooks/useTheme';
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

const VIEW_META: Record<AppView, { label: string; short: string; description: string; icon: IconType }> = {
  monitoring: {
    label: 'Monitoring',
    short: 'Monitor',
    description: 'Quick tiles for the current fleet state.',
    icon: FiActivity,
  },
  'site-monitoring': {
    label: 'Site Monitoring',
    short: 'Sites',
    description: 'Ping and HTTP checks for public sites and external services.',
    icon: FiGlobe,
  },
  graphs: {
    label: 'Graphs',
    short: 'Graphs',
    description: 'Backend charts with time-window navigation.',
    icon: FiBarChart2,
  },
  notifications: {
    label: 'Notifications',
    short: 'Alerts',
    description: 'Alert history across Telegram and local quick-status events.',
    icon: FiBell,
  },
  admin: {
    label: 'Settings',
    short: 'Settings',
    description: 'Backends, dashboard items, notifications, and system controls.',
    icon: FiSettings,
  },
};

const NAV_ORDER: AppView[] = ['monitoring', 'site-monitoring', 'graphs', 'notifications', 'admin'];

function formatBadge(count: number): string {
  return count > 99 ? '99+' : String(count);
}

export function Layout({ activeView, onSwitch, currentUser, onLogout, children }: LayoutProps) {
  const [backendVersion, setBackendVersion] = useState(DEFAULT_BACKEND_VERSION);
  const [unreadCount, setUnreadCount] = useState(0);
  const theme = useTheme();
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

  const handleMarkedRead = useCallback(() => {
    setUnreadCount(0);
  }, []);

  const renderNavItems = (variant: 'sidebar' | 'bottom') =>
    NAV_ORDER.map((view) => {
      const meta = VIEW_META[view];
      const Icon = meta.icon;
      const isActive = activeView === view;
      const isDisabled = view === 'admin' && !canOpenAdmin;
      const badge = view === 'notifications' && unreadCount > 0 ? formatBadge(unreadCount) : null;
      if (variant === 'bottom') {
        return (
          <button
            key={view}
            type="button"
            className={`bottom-nav__item ${isActive ? 'bottom-nav__item--active' : ''}`}
            onClick={() => onSwitch(view)}
            disabled={isDisabled}
            aria-current={isActive ? 'page' : undefined}
          >
            <Icon size={20} aria-hidden="true" />
            <span>{meta.short}</span>
            {badge ? <span className="bottom-nav__badge">{badge}</span> : null}
          </button>
        );
      }
      return (
        <button
          key={view}
          type="button"
          className={`app-nav-link ${isActive ? 'app-nav-link--active' : ''}`}
          onClick={() => onSwitch(view)}
          disabled={isDisabled}
          aria-current={isActive ? 'page' : undefined}
        >
          <span className="app-nav-link__icon">
            <Icon size={18} aria-hidden="true" />
          </span>
          <span className="app-nav-link__copy">
            <span className="app-nav-link__title">{meta.label}</span>
          </span>
          {badge ? <span className="app-nav-link__badge">{badge}</span> : null}
        </button>
      );
    });

  const themeLabel = theme.resolved === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-sidebar__brand">
          <span className="app-sidebar__mark" aria-hidden="true">
            V
          </span>
          <div>
            <div className="app-sidebar__eyebrow">Virgilio</div>
            <div className="app-sidebar__title">Server Monitor</div>
          </div>
        </div>

        <nav className="app-sidebar__nav" aria-label="Primary">
          {renderNavItems('sidebar')}
        </nav>

        <div className="app-sidebar__footer">
          <div>
            Signed in as <strong>{currentUser.username}</strong> · {currentUser.role}
          </div>
          <div>
            Frontend v{FRONTEND_VERSION} · Backend v{backendVersion}
          </div>
          <button className="btn btn-sm btn-ghost" type="button" onClick={onLogout}>
            <FiLogOut size={14} aria-hidden="true" />
            <span>Sign out</span>
          </button>
        </div>
      </aside>

      <div className="app-content">
        <header className="app-header">
          <div>
            <h1 className="app-header__title">{viewMeta.label}</h1>
            <p className="app-header__description">{viewMeta.description}</p>
          </div>
          <div className="app-header__actions">
            {activeView !== 'notifications' && (
              <button
                className="icon-button notification-trigger"
                type="button"
                aria-label={unreadCount > 0 ? `Open notifications, ${unreadCount} unread` : 'Open notifications'}
                onClick={() => onSwitch('notifications')}
              >
                <FiBell size={18} aria-hidden="true" />
                {unreadCount > 0 && <span className="notification-trigger__badge">{formatBadge(unreadCount)}</span>}
              </button>
            )}
            <button className="icon-button" type="button" aria-label={themeLabel} title={themeLabel} onClick={theme.toggle}>
              {theme.resolved === 'dark' ? <FiSun size={18} aria-hidden="true" /> : <FiMoon size={18} aria-hidden="true" />}
            </button>
            <button className="icon-button d-xl-none" type="button" aria-label="Sign out" title="Sign out" onClick={onLogout}>
              <FiLogOut size={18} aria-hidden="true" />
            </button>
          </div>
        </header>

        <main className="app-main">
          {activeView === 'notifications' ? <NotificationCenter onMarkedRead={handleMarkedRead} /> : children}
        </main>
      </div>

      <nav className="bottom-nav" aria-label="Primary">
        {renderNavItems('bottom')}
      </nav>
    </div>
  );
}
