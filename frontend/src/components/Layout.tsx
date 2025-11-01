import { ReactNode, useEffect, useState } from 'react';
import { AuthUser, backendApiBaseUrl, fetchBackendVersion } from '../api/client';
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

  return (
    <div className="min-vh-100 bg-dark text-light">
      <nav className="navbar navbar-expand-lg navbar-dark border-bottom border-secondary">
        <div className="container-fluid">
          <div className="d-flex align-items-center justify-content-between w-100 gap-3">
            <span className="navbar-brand fw-semibold text-uppercase mb-0">
              Virgilio - System Monitoring
              <small
                className="d-block text-secondary fw-normal fst-italic"
                style={{ fontSize: '0.58rem', lineHeight: 1.05, letterSpacing: '0.02em' }}
              >
                Lasciate ogni speranza, voi che entrate
              </small>
            </span>
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
          <div className={`collapse navbar-collapse ${menuOpen ? 'show' : ''}`}>
            <div className="d-flex flex-column flex-lg-row align-items-lg-center gap-3 w-100">
              <div className="d-flex flex-wrap align-items-center gap-2">
                <span className="badge bg-secondary">Frontend v{FRONTEND_VERSION}</span>
                <span className="badge bg-secondary">Backend v{backendVersion}</span>
              </div>
              <span className="small text-secondary text-break">API: {backendApiBaseUrl()}</span>
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
    </div>
  );
}
