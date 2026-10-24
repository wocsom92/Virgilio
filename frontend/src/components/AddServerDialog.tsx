import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { BackendCreatePayload, MonitoredBackend, SelectedMetrics, createBackend } from '../api/client';
import { getUserFacingErrorMessage } from '../utils/errors';
import {
  DEFAULT_MONITOR_PORT,
  DEFAULT_POLL_INTERVAL_SECONDS,
  MIN_POLL_INTERVAL_SECONDS,
  buildAgentInstallCommand,
  copyToClipboard,
  generateMonitorToken,
  normalizeMonitorUrl,
  validateMonitorUrl,
  validateServerName,
  validateToken,
} from '../utils/agentSetup';
import { ConnectionTestPanel, ConnectionTestState } from './ConnectionTestPanel';

export interface MetricOption {
  key: string;
  label: string;
}

interface AddServerDialogProps {
  open: boolean;
  onClose: () => void;
  /** Called after the server was created; the dialog stays open on its "done" screen. */
  onSaved: (backend: MonitoredBackend) => void;
  onOpenDashboard?: () => void;
  existingNames: readonly string[];
  nextDisplayOrder: number;
  metricOptions: readonly MetricOption[];
  createDefaultSelectedMetrics: () => SelectedMetrics;
  /** Key of the metric whose value is an object with `enabled` (mounted volumes). */
  mountedUsageKey: string;
}

type Step = 1 | 2 | 3;

const STEPS: Array<{ id: Step; label: string }> = [
  { id: 1, label: 'Install agent' },
  { id: 2, label: 'Connect' },
  { id: 3, label: 'Options' },
];

const isEnabled = (selected: SelectedMetrics, key: string): boolean => {
  const value = selected[key];
  if (typeof value === 'boolean') {
    return value;
  }
  if (value && typeof value === 'object' && 'enabled' in value) {
    return Boolean((value as { enabled?: boolean }).enabled);
  }
  return false;
};

export function AddServerDialog({
  open,
  onClose,
  onSaved,
  onOpenDashboard,
  existingNames,
  nextDisplayOrder,
  metricOptions,
  createDefaultSelectedMetrics,
  mountedUsageKey,
}: AddServerDialogProps) {
  const [step, setStep] = useState<Step>(1);
  const [generatedToken, setGeneratedToken] = useState(() => generateMonitorToken());
  const [agentPort, setAgentPort] = useState<number | ''>(DEFAULT_MONITOR_PORT);
  const [name, setName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [token, setToken] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [pollInterval, setPollInterval] = useState<number | ''>(DEFAULT_POLL_INTERVAL_SECONDS);
  const [notes, setNotes] = useState('');
  const [selectedMetrics, setSelectedMetrics] = useState<SelectedMetrics>(() => createDefaultSelectedMetrics());
  const [touched, setTouched] = useState<{ name?: boolean; url?: boolean; token?: boolean }>({});
  const [testState, setTestState] = useState<ConnectionTestState>({ phase: 'idle' });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState<MonitoredBackend | null>(null);
  const [copied, setCopied] = useState<'token' | 'command' | null>(null);
  const firstFieldRef = useRef<HTMLInputElement | null>(null);

  const reset = () => {
    setStep(1);
    setGeneratedToken(generateMonitorToken());
    setAgentPort(DEFAULT_MONITOR_PORT);
    setName('');
    setBaseUrl('');
    setToken('');
    setShowToken(false);
    setPollInterval(DEFAULT_POLL_INTERVAL_SECONDS);
    setNotes('');
    setSelectedMetrics(createDefaultSelectedMetrics());
    setTouched({});
    setTestState({ phase: 'idle' });
    setSaving(false);
    setSaveError(null);
    setSaved(null);
    setCopied(null);
  };

  useEffect(() => {
    if (open) {
      reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !saving) {
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose, saving]);

  useEffect(() => {
    if (open && step === 2) {
      firstFieldRef.current?.focus();
    }
  }, [open, step]);

  const nameError = validateServerName(name, existingNames);
  const urlError = validateMonitorUrl(baseUrl);
  const tokenError = validateToken(token);
  const connectValid = !nameError && !urlError && !tokenError;
  const pollValue = pollInterval === '' ? DEFAULT_POLL_INTERVAL_SECONDS : Number(pollInterval);
  const pollError =
    !Number.isFinite(pollValue) || pollValue < MIN_POLL_INTERVAL_SECONDS
      ? `Poll at least every ${MIN_POLL_INTERVAL_SECONDS} seconds.`
      : null;

  const installPort = agentPort === '' ? DEFAULT_MONITOR_PORT : Number(agentPort);
  const installCommand = useMemo(
    () => buildAgentInstallCommand(generatedToken, installPort),
    [generatedToken, installPort]
  );

  const metricsSelected = metricOptions.filter((option) => isEnabled(selectedMetrics, option.key)).length;
  const testSucceeded = testState.phase === 'done' && testState.result.ok;
  const probeHostname = testSucceeded ? testState.result.hostname : null;

  const copy = async (what: 'token' | 'command', text: string) => {
    const ok = await copyToClipboard(text);
    setCopied(ok ? what : null);
    if (ok) {
      window.setTimeout(() => setCopied(null), 2000);
    }
  };

  const goToConnect = (carryToken: boolean) => {
    if (carryToken) {
      setToken(generatedToken);
    }
    setStep(2);
  };

  const toggleMetric = (key: string, checked: boolean) => {
    setSelectedMetrics((prev) => {
      const current = prev[key];
      if (key === mountedUsageKey) {
        const mounts =
          current && typeof current === 'object' && 'mounts' in current
            ? (current as { mounts: Array<{ path: string; label: string }> }).mounts
            : [{ path: '/', label: '/' }];
        return { ...prev, [key]: { enabled: checked, mounts } };
      }
      return { ...prev, [key]: checked };
    });
  };

  const handleSave = async (event: FormEvent) => {
    event.preventDefault();
    if (!connectValid || pollError || saving) {
      return;
    }
    setSaving(true);
    setSaveError(null);
    const payload: BackendCreatePayload = {
      name: name.trim(),
      base_url: normalizeMonitorUrl(baseUrl),
      api_token: token.trim(),
      is_active: true,
      display_order: nextDisplayOrder,
      poll_interval_seconds: pollValue,
      notes: notes.trim() || undefined,
      selected_metrics: selectedMetrics,
    };
    try {
      const created = await createBackend(payload);
      setSaved(created);
      onSaved(created);
    } catch (err) {
      setSaveError(getUserFacingErrorMessage(err, 'The server could not be saved. Check the details and try again.'));
    } finally {
      setSaving(false);
    }
  };

  if (!open) {
    return null;
  }

  const renderStepper = () => (
    <ol className="stepper" aria-label="Add server steps">
      {STEPS.map((item) => {
        const status = saved ? 'done' : item.id < step ? 'done' : item.id === step ? 'active' : 'todo';
        return (
          <li key={item.id} className={`stepper__step stepper__step--${status}`} aria-current={status === 'active' ? 'step' : undefined}>
            <span className="stepper__index">{status === 'done' ? '✓' : item.id}</span>
            <span className="stepper__label">{item.label}</span>
          </li>
        );
      })}
    </ol>
  );

  const renderInstallStep = () => (
    <div className="d-flex flex-column gap-3">
      <p className="mb-0 text-secondary small">
        Virgilio polls a small agent that runs on each server. If the agent is already running there, skip ahead and
        enter its address and token. Otherwise start it now:
      </p>
      <ol className="dialog-steps-list">
        <li>
          <div className="fw-semibold">Pick a token</div>
          <p className="form-text mt-1 mb-2">
            The same secret goes into the agent as <code>MONITOR_API_TOKEN</code> and into Virgilio on the next step.
            We generated one for you.
          </p>
          <div className="d-flex gap-2 align-items-center flex-wrap">
            <input
              className="form-control connection-test__token"
              value={generatedToken}
              onChange={(event) => setGeneratedToken(event.target.value.trim())}
              aria-label="Agent token"
              spellCheck={false}
            />
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => void copy('token', generatedToken)}>
              {copied === 'token' ? 'Copied' : 'Copy'}
            </button>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setGeneratedToken(generateMonitorToken())}>
              Regenerate
            </button>
          </div>
        </li>
        <li>
          <div className="fw-semibold">Start the agent on the server</div>
          <p className="form-text mt-1 mb-2">
            Run this from a checkout of the Virgilio repository on that machine (or use{' '}
            <code>scripts/deploy.sh --target &lt;name&gt; --profile monitor-only</code>). The agent listens on port{' '}
            <input
              type="number"
              className="form-control form-control--inline"
              min={1}
              max={65535}
              value={agentPort}
              onChange={(event) => {
                const next = event.target.value;
                setAgentPort(next === '' ? '' : Number(next));
              }}
              aria-label="Agent port"
            />{' '}
            and reads host metrics through <code>/hostfs</code>.
          </p>
          <pre className="code-block" data-testid="install-command">
            <code>{installCommand}</code>
          </pre>
          <button type="button" className="btn btn-sm btn-ghost mt-2" onClick={() => void copy('command', installCommand)}>
            {copied === 'command' ? 'Copied' : 'Copy command'}
          </button>
        </li>
        <li>
          <div className="fw-semibold">Open the port</div>
          <p className="form-text mt-1 mb-0">
            The Virgilio backend must reach <code>http://&lt;server&gt;:{installPort}</code>. Allow it through the
            server's firewall or VPN if needed.
          </p>
        </li>
      </ol>
    </div>
  );

  const renderConnectStep = () => (
    <div className="d-flex flex-column gap-3">
      <div>
        <label className="form-label" htmlFor="add-server-name">
          Name
        </label>
        <input
          id="add-server-name"
          ref={firstFieldRef}
          className={`form-control${touched.name && nameError ? ' is-invalid' : ''}`}
          value={name}
          onChange={(event) => setName(event.target.value)}
          onBlur={() => setTouched((prev) => ({ ...prev, name: true }))}
          placeholder="e.g. Living room Pi"
          maxLength={100}
          autoComplete="off"
        />
        {touched.name && nameError ? (
          <div className="form-text text-danger">{nameError}</div>
        ) : (
          <div className="form-text">Shown on the dashboard and in alerts.</div>
        )}
      </div>
      <div>
        <label className="form-label" htmlFor="add-server-url">
          Agent address
        </label>
        <input
          id="add-server-url"
          className={`form-control${touched.url && urlError ? ' is-invalid' : ''}`}
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
          onBlur={() => {
            setTouched((prev) => ({ ...prev, url: true }));
            if (baseUrl.trim()) {
              setBaseUrl(normalizeMonitorUrl(baseUrl));
            }
          }}
          placeholder={`http://192.168.1.20:${DEFAULT_MONITOR_PORT}`}
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
        />
        {touched.url && urlError ? (
          <div className="form-text text-danger">{urlError}</div>
        ) : (
          <div className="form-text">
            The agent's URL as seen from the Virgilio backend: <code>http://&lt;host&gt;:{DEFAULT_MONITOR_PORT}</code>.
            Inside the same Docker Compose stack use <code>http://monitor:9000</code>. No path after the port.
          </div>
        )}
      </div>
      <div>
        <label className="form-label" htmlFor="add-server-token">
          Agent token
        </label>
        <div className="d-flex gap-2">
          <input
            id="add-server-token"
            type={showToken ? 'text' : 'password'}
            className={`form-control${touched.token && tokenError ? ' is-invalid' : ''}`}
            value={token}
            onChange={(event) => setToken(event.target.value)}
            onBlur={() => setTouched((prev) => ({ ...prev, token: true }))}
            autoComplete="new-password"
            spellCheck={false}
          />
          <button type="button" className="btn btn-ghost" onClick={() => setShowToken((prev) => !prev)}>
            {showToken ? 'Hide' : 'Show'}
          </button>
        </div>
        {touched.token && tokenError ? (
          <div className="form-text text-danger">{tokenError}</div>
        ) : (
          <div className="form-text">
            The value of <code>MONITOR_API_TOKEN</code> in the agent's environment (at least 10 characters).
          </div>
        )}
      </div>
      <div className="card-panel rounded-3 p-3">
        <div className="fw-semibold mb-1">Check the connection</div>
        <p className="form-text mt-0 mb-2">
          Virgilio asks the agent for one metrics sample using the address and token above.
        </p>
        <ConnectionTestPanel
          baseUrl={baseUrl}
          apiToken={token}
          onStateChange={setTestState}
          disabled={Boolean(urlError || tokenError)}
        />
      </div>
    </div>
  );

  const renderOptionsStep = () => (
    <div className="d-flex flex-column gap-3">
      <div className="card-panel rounded-3 p-3">
        <div className="d-flex justify-content-between align-items-center flex-wrap gap-2">
          <div>
            <div className="fw-semibold">{name.trim()}</div>
            <div className="small text-secondary">{normalizeMonitorUrl(baseUrl)}</div>
          </div>
          {testSucceeded ? (
            <span className="badge badge-ok">Connected{probeHostname ? ` · ${probeHostname}` : ''}</span>
          ) : (
            <span className="badge badge-warn">Connection not verified</span>
          )}
        </div>
        {!testSucceeded && (
          <p className="form-text mb-0 mt-2">
            You can still save. The dashboard shows the server as unreachable until the agent answers, and you can
            test again from its settings page.
          </p>
        )}
      </div>
      <div>
        <label className="form-label" htmlFor="add-server-poll">
          Poll every
        </label>
        <div className="d-flex align-items-center gap-2">
          <input
            id="add-server-poll"
            type="number"
            className={`form-control form-control--short${pollError ? ' is-invalid' : ''}`}
            min={MIN_POLL_INTERVAL_SECONDS}
            step={10}
            value={pollInterval}
            onChange={(event) => {
              const next = event.target.value;
              setPollInterval(next === '' ? '' : Number(next));
            }}
          />
          <span className="small">seconds</span>
        </div>
        {pollError ? (
          <div className="form-text text-danger">{pollError}</div>
        ) : (
          <div className="form-text">
            How often Virgilio fetches metrics. 60 s is a good default; the minimum is {MIN_POLL_INTERVAL_SECONDS} s.
          </div>
        )}
      </div>
      <div>
        <div className="d-flex justify-content-between align-items-center mb-1">
          <span className="form-label mb-0">Dashboard charts</span>
          <span className="badge">{metricsSelected} of {metricOptions.length}</span>
        </div>
        <div className="form-text mt-0 mb-2">
          All charts are on by default. Mounted volumes start with <code>/</code>; add more paths later from the
          server's settings.
        </div>
        <div className="metric-grid">
          {metricOptions.map((option) => (
            <label key={option.key} className="form-check">
              <input
                className="form-check-input"
                type="checkbox"
                checked={isEnabled(selectedMetrics, option.key)}
                onChange={(event) => toggleMetric(option.key, event.target.checked)}
              />
              <span className="form-check-label">{option.label}</span>
            </label>
          ))}
        </div>
      </div>
      <div>
        <label className="form-label" htmlFor="add-server-notes">
          Notes <span className="text-secondary fw-semibold">(optional)</span>
        </label>
        <textarea
          id="add-server-notes"
          className="form-control"
          rows={2}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          placeholder="Where it is, what runs on it…"
        />
      </div>
      {saveError && (
        <div className="alert alert-danger" role="alert">
          {saveError}
        </div>
      )}
    </div>
  );

  const renderDone = () => (
    <div className="d-flex flex-column gap-3 align-items-start" data-testid="add-server-done">
      <span className="badge badge-ok">Server added</span>
      <h4 className="mb-0">{saved?.name} is now monitored</h4>
      <p className="mb-0 text-secondary">
        Virgilio polls {normalizeMonitorUrl(baseUrl)} every {pollValue} seconds. The first charts appear on the
        dashboard after the first sample{testSucceeded ? '' : ', once the agent answers'}.
      </p>
      <ul className="dialog-next-steps">
        <li>Open the dashboard to see the new card.</li>
        <li>Add quick tiles (disk, RAM, ping…) under Dashboard items.</li>
        <li>Enable Telegram alerts under Telegram if you want notifications.</li>
      </ul>
    </div>
  );

  const titleId = 'add-server-title';

  return (
    <div className="dialog-backdrop" onMouseDown={(event) => event.target === event.currentTarget && !saving && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <form onSubmit={handleSave} className="dialog__form">
          <header className="dialog__header">
            <div>
              <h3 className="dialog__title" id={titleId}>
                {saved ? 'Done' : 'Add server'}
              </h3>
              {!saved && (
                <p className="dialog__subtitle">
                  {step === 1 && 'Step 1 of 3 · Start the monitor agent on the server you want to watch.'}
                  {step === 2 && 'Step 2 of 3 · Tell Virgilio where the agent is and check it answers.'}
                  {step === 3 && 'Step 3 of 3 · Polling and chart options, then save.'}
                </p>
              )}
            </div>
            <button type="button" className="icon-button" aria-label="Close" onClick={onClose} disabled={saving}>
              ×
            </button>
          </header>
          {!saved && renderStepper()}
          <div className="dialog__body">
            {saved ? renderDone() : step === 1 ? renderInstallStep() : step === 2 ? renderConnectStep() : renderOptionsStep()}
          </div>
          <footer className="dialog__footer">
            {saved ? (
              <>
                <button type="button" className="btn btn-ghost" onClick={reset}>
                  Add another server
                </button>
                <span className="flex-grow-1" />
                <button type="button" className="btn btn-ghost" onClick={onClose}>
                  Close
                </button>
                {onOpenDashboard && (
                  <button type="button" className="btn btn-primary" onClick={onOpenDashboard}>
                    Open dashboard
                  </button>
                )}
              </>
            ) : step === 1 ? (
              <>
                <button type="button" className="btn btn-ghost" onClick={() => goToConnect(false)}>
                  Agent already running
                </button>
                <span className="flex-grow-1" />
                <button type="button" className="btn btn-primary" onClick={() => goToConnect(true)}>
                  Next: connect
                </button>
              </>
            ) : step === 2 ? (
              <>
                <button type="button" className="btn btn-ghost" onClick={() => setStep(1)}>
                  Back
                </button>
                <span className="flex-grow-1" />
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => {
                    setTouched({ name: true, url: true, token: true });
                    if (connectValid) {
                      setStep(3);
                    }
                  }}
                >
                  Next: options
                </button>
              </>
            ) : (
              <>
                <button type="button" className="btn btn-ghost" onClick={() => setStep(2)} disabled={saving}>
                  Back
                </button>
                <span className="flex-grow-1" />
                <button type="submit" className="btn btn-primary" disabled={saving || Boolean(pollError)}>
                  {saving ? 'Saving…' : 'Add server'}
                </button>
              </>
            )}
          </footer>
        </form>
      </div>
    </div>
  );
}
