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
import { Layout } from './components/Layout';
import { useLocalStorage } from './hooks/useLocalStorage';

type View = 'dashboard' | 'admin';

export default function App() {
  const [view, setView] = useState<View>('dashboard');
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
          setAuthError(err instanceof Error ? err.message : 'Unable to check authentication status.');
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
      const message = err instanceof Error ? err.message : 'Unable to sign in';
      setAuthError(message);
    } finally {
      setAuthSubmitting(false);
    }
  };

  const handleLogout = () => {
    setStoredToken('');
    setAccessToken(null);
    setCurrentUser(null);
  };

  useEffect(() => {
    if (view === 'admin' && currentUser?.role === 'viewer') {
      setView('dashboard');
    }
  }, [view, currentUser]);

  const authTitle = useMemo(
    () => (needsBootstrap ? 'Create admin account' : 'Sign in'),
    [needsBootstrap]
  );

  if (authLoading) {
    return (
      <div className="container py-5 text-light">
        <div className="h4">Loading…</div>
        <div className="text-secondary small">Checking authentication status.</div>
      </div>
    );
  }

  if (!currentUser) {
    return (
      <div className="container py-5 text-light">
        <div className="row justify-content-center">
          <div className="col-12 col-md-8 col-lg-6">
            <div className="card bg-dark border border-secondary shadow-sm">
              <div className="card-header text-uppercase fw-semibold border-secondary">
                {authTitle}
              </div>
              <div className="card-body d-flex flex-column gap-3">
                <p className="text-secondary mb-0">
                  {needsBootstrap
                    ? 'Set the first admin credentials for this Virgilio instance.'
                    : 'Sign in to access the dashboard and admin console.'}
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
      {view === 'dashboard' ? (
        <Dashboard canRefresh={currentUser.role === 'admin'} />
      ) : (
        <AdminPanel currentUser={currentUser} />
      )}
    </Layout>
  );
}
