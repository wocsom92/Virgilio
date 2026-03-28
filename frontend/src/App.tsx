import { FormEvent, useEffect, useMemo, useState } from 'react';
import {
  AuthStatus,
  AuthUser,
  TokenResponse,
  bootstrapAdmin,
  fetchAuthStatus,
  fetchCurrentUser,
  login,
  setAccessToken,
} from './api/client';
import { Dashboard } from './components/Dashboard';
import { AdminPanel } from './components/AdminPanel';
import { AppView, Layout } from './components/Layout';
import { SiteMonitoring } from './components/SiteMonitoring';
import { useLocalStorage } from './hooks/useLocalStorage';
import { getUserFacingErrorMessage } from './utils/errors';

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
    if (typeof window === 'undefined') {
      return;
    }
    try {
      const persistedToken = window.localStorage.getItem('server-monitor-auth-token');
      const sessionToken = window.sessionStorage.getItem('server-monitor-auth-token');
      if (sessionToken && !persistedToken) {
        window.localStorage.setItem('server-monitor-auth-token', sessionToken);
        setStoredToken(JSON.parse(sessionToken) as string);
      }
      if (sessionToken) {
        window.sessionStorage.removeItem('server-monitor-auth-token');
      }
    } catch {
      // Ignore storage migration failures and continue with the current auth state.
    }
  }, [setStoredToken]);

  useEffect(() => {
    setAccessToken(storedToken || null);
  }, [storedToken]);

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
      <div className="container py-5 text-light app-auth-shell">
        <div className="h4">Loading…</div>
        <div className="text-secondary small">Checking authentication status.</div>
      </div>
    );
  }

  if (!currentUser) {
    return (
      <div className="container py-5 text-light app-auth-shell">
        <div className="row justify-content-center">
          <div className="col-12 col-md-8 col-lg-6">
            <div className="card app-auth-card shadow-sm">
              <div className="card-header text-uppercase fw-semibold app-auth-card__header">
                {authTitle}
              </div>
              <div className="card-body d-flex flex-column gap-3">
                <p className="text-secondary mb-0">
                  {needsBootstrap
                    ? 'Set the first admin credentials for this Virgilio instance.'
                    : 'Sign in to access monitoring, site monitoring, graphs, and the admin console.'}
                </p>
                {authError && <div className="alert alert-danger mb-0">{authError}</div>}
                <form className="d-flex flex-column gap-3" onSubmit={handleAuthSubmit}>
                  <div>
                    <label className="form-label">Username</label>
                    <input
                      className="form-control bg-dark text-light border-secondary"
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
                      className="form-control bg-dark text-light border-secondary"
                      value={authForm.password}
                      onChange={(event) =>
                        setAuthForm((prev) => ({ ...prev, password: event.target.value }))
                      }
                      autoComplete={needsBootstrap ? 'new-password' : 'current-password'}
                      required
                      minLength={needsBootstrap ? 8 : 6}
                    />
                  </div>
                  <button className="btn btn-light text-dark" type="submit" disabled={authSubmitting}>
                    {authSubmitting ? 'Saving…' : authTitle}
                  </button>
                </form>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <Layout activeView={view} onSwitch={setView} currentUser={currentUser} onLogout={handleLogout}>
      {view === 'admin' ? (
        <AdminPanel currentUser={currentUser} />
      ) : view === 'site-monitoring' ? (
        <SiteMonitoring />
      ) : (
        <Dashboard canRefresh={currentUser.role === 'admin'} mode={view} />
      )}
    </Layout>
  );
}
