import { useEffect, useRef, useState } from 'react';
import { MonitorConnectionTestResult, testMonitorConnection } from '../api/client';
import { getUserFacingErrorMessage } from '../utils/errors';
import {
  buildAgentInstallCommand,
  copyToClipboard,
  formatUptime,
  normalizeMonitorUrl,
  portFromUrl,
  validateMonitorUrl,
  validateToken,
} from '../utils/agentSetup';

const ERROR_TITLES: Record<string, string> = {
  dns: 'Hostname not found',
  refused: 'Connection refused',
  unreachable: 'Host unreachable',
  timeout: 'No answer (timed out)',
  tls: 'TLS handshake failed',
  unauthorized: 'Token rejected',
  not_found: 'No /metrics at this address',
  http: 'Unexpected response status',
  invalid_response: 'Not a monitor agent',
};

/** Error kinds where the most likely cause is that the agent is not running yet. */
const AGENT_MISSING_KINDS = new Set(['refused', 'timeout', 'unreachable', 'invalid_response', 'not_found']);

export type ConnectionTestState =
  | { phase: 'idle' }
  | { phase: 'testing' }
  | { phase: 'done'; result: MonitorConnectionTestResult }
  | { phase: 'failed'; message: string };

interface ConnectionTestPanelProps {
  baseUrl: string;
  apiToken: string;
  /** When set and the token is blank, the backend reuses the stored token of this server. */
  backendId?: number;
  disabled?: boolean;
  /** Called with every state change so the parent can gate "Next"/"Save" on a successful test. */
  onStateChange?: (state: ConnectionTestState) => void;
  /** Shown as the agent install hint when the connection fails. */
  showInstallHint?: boolean;
  compact?: boolean;
}

export function formatProbeTitle(result: MonitorConnectionTestResult): string {
  return ERROR_TITLES[result.error_kind ?? ''] ?? 'Connection failed';
}

export function ConnectionTestPanel({
  baseUrl,
  apiToken,
  backendId,
  disabled = false,
  onStateChange,
  showInstallHint = true,
  compact = false,
}: ConnectionTestPanelProps) {
  const [state, setState] = useState<ConnectionTestState>({ phase: 'idle' });
  const [copied, setCopied] = useState(false);
  const lastTested = useRef<{ url: string; token: string } | null>(null);
  const stateChangeRef = useRef(onStateChange);
  stateChangeRef.current = onStateChange;

  const update = (next: ConnectionTestState) => {
    setState(next);
    stateChangeRef.current?.(next);
  };

  // A result only describes the inputs it was produced with; forget it once they change.
  useEffect(() => {
    if (state.phase === 'idle' || state.phase === 'testing') {
      return;
    }
    const current = { url: normalizeMonitorUrl(baseUrl), token: apiToken.trim() };
    if (lastTested.current && (lastTested.current.url !== current.url || lastTested.current.token !== current.token)) {
      update({ phase: 'idle' });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, apiToken]);

  const urlError = validateMonitorUrl(baseUrl);
  const tokenError = validateToken(apiToken, { required: backendId === undefined });
  const canTest = !disabled && !urlError && !tokenError && state.phase !== 'testing';

  const runTest = async () => {
    if (!canTest) {
      return;
    }
    const url = normalizeMonitorUrl(baseUrl);
    const token = apiToken.trim();
    lastTested.current = { url, token };
    update({ phase: 'testing' });
    try {
      const result = await testMonitorConnection({
        base_url: url,
        api_token: token || undefined,
        backend_id: token ? undefined : backendId,
      });
      update({ phase: 'done', result });
    } catch (err) {
      update({ phase: 'failed', message: getUserFacingErrorMessage(err, 'The connection test could not be run.') });
    }
  };

  const installCommand = buildAgentInstallCommand(apiToken, portFromUrl(baseUrl));
  const handleCopy = async () => {
    const ok = await copyToClipboard(installCommand);
    setCopied(ok);
    if (ok) {
      window.setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className={`connection-test${compact ? ' connection-test--compact' : ''}`}>
      <div className="d-flex align-items-center gap-2 flex-wrap">
        <button
          type="button"
          className={`btn ${compact ? 'btn-sm ' : ''}${state.phase === 'done' && state.result.ok ? 'btn-ghost' : 'btn-primary'}`}
          onClick={() => void runTest()}
          disabled={!canTest}
          data-testid="test-connection"
        >
          {state.phase === 'testing' ? 'Contacting agent…' : state.phase === 'idle' ? 'Test connection' : 'Test again'}
        </button>
        {state.phase === 'idle' && (urlError || tokenError) && (
          <span className="form-text mb-0 mt-0">
            {disabled ? '' : urlError ? 'Enter the address first.' : 'Enter the token first.'}
          </span>
        )}
        {state.phase === 'done' && state.result.ok && (
          <span className="badge badge-ok" data-testid="probe-ok">
            Connected
            {typeof state.result.latency_ms === 'number' ? ` · ${state.result.latency_ms} ms` : ''}
          </span>
        )}
        {state.phase === 'done' && !state.result.ok && (
          <span className="badge badge-critical" data-testid="probe-error-badge">
            {formatProbeTitle(state.result)}
          </span>
        )}
      </div>

      {state.phase === 'done' && state.result.ok && (
        <dl className="kv-grid mt-2" data-testid="probe-details">
          <dt>Hostname</dt>
          <dd>{state.result.hostname ?? '—'}</dd>
          <dt>Agent version</dt>
          <dd>{state.result.monitor_version ?? '—'}</dd>
          {state.result.os_version && (
            <>
              <dt>OS</dt>
              <dd>{state.result.os_version}</dd>
            </>
          )}
          <dt>CPU temperature</dt>
          <dd>
            {typeof state.result.cpu_temperature_c === 'number'
              ? `${state.result.cpu_temperature_c.toFixed(1)} °C`
              : 'not reported'}
          </dd>
          {typeof state.result.ram_used_percent === 'number' && (
            <>
              <dt>RAM used</dt>
              <dd>{state.result.ram_used_percent.toFixed(0)} %</dd>
            </>
          )}
          {formatUptime(state.result.uptime_seconds) && (
            <>
              <dt>Uptime</dt>
              <dd>{formatUptime(state.result.uptime_seconds)}</dd>
            </>
          )}
        </dl>
      )}

      {state.phase === 'done' && !state.result.ok && (
        <div className="alert alert-danger mt-2" role="alert" data-testid="probe-error">
          <div className="fw-semibold">{formatProbeTitle(state.result)}</div>
          {state.result.hint && <div className="mt-1">{state.result.hint}</div>}
          {state.result.error && (
            <details className="mt-2 connection-test__details">
              <summary>Technical details</summary>
              <code>{state.result.error}</code>
            </details>
          )}
          {showInstallHint && AGENT_MISSING_KINDS.has(state.result.error_kind ?? '') && (
            <details className="mt-2 connection-test__details">
              <summary>Agent not installed yet? Show the install command</summary>
              <p className="mb-2 mt-2">
                Run this on the server from a checkout of the Virgilio repository (or deploy with{' '}
                <code>scripts/deploy.sh --profile monitor-only</code>). It uses the token entered above.
              </p>
              <pre className="code-block">
                <code>{installCommand}</code>
              </pre>
              <button type="button" className="btn btn-sm btn-ghost mt-2" onClick={() => void handleCopy()}>
                {copied ? 'Copied' : 'Copy command'}
              </button>
            </details>
          )}
        </div>
      )}

      {state.phase === 'failed' && (
        <div className="alert alert-warning mt-2" role="alert">
          {state.message}
        </div>
      )}
    </div>
  );
}
