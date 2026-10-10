import { FormEvent, lazy, Suspense, useEffect, useMemo, useState } from 'react';
import {
  AuthStatus,
  AuthUser,
  TokenResponse,
  bootstrapAdmin,
  fetchAuthStatus,
  fetchCurrentUser,
  login,
  setAccessToken,
  setUnauthorizedHandler,
} from './api/client';
import { Dashboard } from './components/Dashboard';
import { AppView, Layout } from './components/Layout';
import { SiteMonitoring } from './components/SiteMonitoring';
import { NowProvider } from './contexts/NowContext';
import { useLocalStorage } from './hooks/useLocalStorage';
import { getUserFacingErrorMessage } from './utils/errors';

// The admin console is by far the largest screen; load it only when an admin opens it.
const AdminPanel = lazy(() => import('./components/AdminPanel').then((module) => ({ default: module.AdminPanel })));

export default function App() {
  const [view, setView] = useState<AppView>('monitoring');
  const [storedToken, setStoredToken] = useLocalStorage<string>('server-monitor-auth-token', '');
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authSubmitting, setAuthSubmitting] = useState(false);
  const [authForm, setAuthForm] = useState({ username: '', password: '' });

  useEffect(() => {
    setAccessToken(storedToken || null);
  }, [storedToken]);

  // A 401 from any poller means the session is gone: sign out once instead of N error banners.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setStoredToken('');
      setAccessToken(null);
      setCurrentUser(null);
      setAuthError('Your session expired. Sign in again.');
    });
    return () => setUnauthorizedHandler(null);
  }, [setStoredToken]);

  useEffect(() => {
    let cancelled = false;
    setAuthLoading(true);
    setAuthError(null);
    void (async () => {
      try {
        const status = await fetchAuthStatus();
        if (cancelled) return;
        setAuthStatus(status);

        if (storedToken) {
          setAccessToken(storedToken);
          const user = await fetchCurrentUser();
          if (cancelled) return;
          setCurrentUser(user);
        } else {
          setCurrentUser(null);
        }
      } catch (err) {
        if (!cancelled) {
          setAuthError(getUserFacingErrorMessage(err, 'Could not verify the current session. Try signing in again.'));
          setCurrentUser(null);
          const status = (err as { response?: { status?: number } })?.response?.status;
          if (status === 401) {
            setStoredToken('');
            setAccessToken(null);
          }
        }
      } finally {
        if (!cancelled) {
          setAuthLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [storedToken]);

  const needsBootstrap = authStatus?.needs_bootstrap ?? false;

  const applyAuthToken = (token: TokenResponse) => {
    setStoredToken(token.access_token);
    setAccessToken(token.access_token);
    setCurrentUser({ id: token.user_id, username: token.username, role: token.role });
    setAuthStatus({ needs_bootstrap: false });
  };

  const handleAuthSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setAuthError(null);
    setAuthSubmitting(true);
    try {
      const { username, password } = authForm;
      const token = needsBootstrap
        ? await bootstrapAdmin(username, password)
        : await login(username, password);
      applyAuthToken(token);
    } catch (err) {
      const message = getUserFacingErrorMessage(err, 'Could not sign in. Check your credentials and try again.');
      setAuthError(message);
    } finally {
      setAuthSubmitting(false);
    }
  };

  const handleLogout = () => {
    setStoredToken('');
    setAccessToken(null);
    setCurrentUser(null);
    setView('monitoring');
  };

  useEffect(() => {
    if (view === 'admin' && currentUser?.role === 'viewer') {
      setView('monitoring');
    }
  }, [view, currentUser]);

  const authTitle = useMemo(
    () => (needsBootstrap ? 'Create admin account' : 'Sign in'),
    [needsBootstrap]
  );
  if (authLoading) {
    return (
      <div className="app-auth-shell">
        <div className="text-center">
          <div className="fw-semibold mb-1">Loading…</div>
          <div className="text-secondary small">Checking authentication status.</div>
        </div>
      </div>
    );
  }

  if (!currentUser) {
    return (
      <div className="app-auth-shell">
        <div className="card app-auth-card">
              <div className="card-header">{authTitle}</div>
              <div className="card-body d-flex flex-column gap-3">
                <p className="text-secondary mb-0">
                  {needsBootstrap
                    ? 'Set the first admin credentials for this Virgilio instance.'
                    : 'Sign in to access monitoring, site monitoring, graphs, and settings.'}
                </p>
                {authError && <div className="alert alert-danger mb-0">{authError}</div>}
                <form className="d-flex flex-column gap-3" onSubmit={handleAuthSubmit}>
                  <div>
                    <label className="form-label">Username</label>
                    <input
                      className="form-control"
                      value={authForm.username}
                      onChange={(event) =>
                        setAuthForm((prev) => ({ ...prev, username: event.target.value }))
                      }
                      autoComplete="username"
                      required
                    />
                  </div>
                  <div>
                    <label className="form-label">Password</label>
                    <input
                      type="password"
                      className="form-control"
                      value={authForm.password}
                      onChange={(event) =>
                        setAuthForm((prev) => ({ ...prev, password: event.target.value }))
                      }
                      autoComplete={needsBootstrap ? 'new-password' : 'current-password'}
                      required
                      minLength={needsBootstrap ? 8 : 6}
                    />
                  </div>
                  <button className="btn btn-primary" type="submit" disabled={authSubmitting}>
                    {authSubmitting ? 'Saving…' : authTitle}
                  </button>
                </form>
              </div>
        </div>
      </div>
    );
  }

  return (
    <NowProvider>
      <Layout activeView={view} onSwitch={setView} currentUser={currentUser} onLogout={handleLogout}>
        {view === 'admin' ? (
          <Suspense fallback={<div className="text-secondary">Loading settings…</div>}>
            <AdminPanel currentUser={currentUser} />
          </Suspense>
        ) : view === 'site-monitoring' ? (
          <SiteMonitoring />
        ) : view === 'notifications' ? null : (
          <Dashboard canRefresh={currentUser.role === 'admin'} mode={view === 'graphs' ? 'graphs' : 'monitoring'} />
        )}
      </Layout>
    </NowProvider>
  );
}
