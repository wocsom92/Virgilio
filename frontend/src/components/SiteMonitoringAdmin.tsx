import { FormEvent, useEffect, useMemo, useState } from 'react';

import {
  SiteMonitor,
  SiteMonitorCheckType,
  SiteMonitorConfig,
  createSiteMonitor,
  deleteSiteMonitor,
  listSiteMonitors,
  updateSiteMonitor,
} from '../api/client';
import { getUserFacingErrorMessage } from '../utils/errors';

const SITE_MONITOR_INTERVAL_SECONDS = 1800;

type SiteMonitorFormState = {
  name: string;
  check_type: SiteMonitorCheckType;
  target: string;
  expected_status_codes: string;
  expected_response_substring: string;
  timeout_ms: number | '';
  warning_consecutive_failures: number | '';
  critical_consecutive_failures: number | '';
  check_interval_seconds: number | '';
  display_order: number | '';
  is_active: boolean;
};

function createInitialForm(): SiteMonitorFormState {
  return {
    name: '',
    check_type: 'ping',
    target: '',
    expected_status_codes: '200',
    expected_response_substring: '',
    timeout_ms: 3000,
    warning_consecutive_failures: 3,
    critical_consecutive_failures: 5,
    check_interval_seconds: SITE_MONITOR_INTERVAL_SECONDS,
    display_order: 0,
    is_active: true,
  };
}

function normalizeSiteMonitors(items: SiteMonitor[]): SiteMonitor[] {
  return [...items].sort((a, b) => {
    if (a.display_order !== b.display_order) {
      return a.display_order - b.display_order;
    }
    return a.name.localeCompare(b.name);
  });
}

function parseExpectedStatusCodes(value: string): number[] {
  return Array.from(
    new Set(
      value
        .split(',')
        .map((entry) => Number(entry.trim()))
        .filter((entry) => Number.isInteger(entry) && entry >= 100 && entry <= 599)
    )
  ).sort((a, b) => a - b);
}

export function SiteMonitoringAdmin() {
  const [items, setItems] = useState<SiteMonitor[]>([]);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<SiteMonitorFormState>(() => createInitialForm());

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await listSiteMonitors();
        if (!cancelled) {
          setItems(normalizeSiteMonitors(data));
          setStatus(null);
        }
      } catch (err) {
        if (!cancelled) {
          setStatus(getUserFacingErrorMessage(err, 'Could not load site-monitor definitions. Check the API connection and try again.'));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const isHttpCheck = form.check_type === 'http';
  const parsedStatusCodes = useMemo(() => parseExpectedStatusCodes(form.expected_status_codes), [form.expected_status_codes]);

  const resetForm = (options: { keepStatus?: boolean } = {}) => {
    setForm(createInitialForm());
    setEditingId(null);
    if (!options.keepStatus) {
      setStatus(null);
    }
  };

  const handleEdit = (item: SiteMonitor) => {
    setEditingId(item.id);
    setForm({
      name: item.name,
      check_type: item.check_type,
      target: item.target,
      expected_status_codes: item.expected_status_codes.length ? item.expected_status_codes.join(', ') : '200',
      expected_response_substring: item.expected_response_substring ?? '',
      timeout_ms: item.timeout_ms,
      warning_consecutive_failures: item.warning_consecutive_failures,
      critical_consecutive_failures: item.critical_consecutive_failures,
      check_interval_seconds: SITE_MONITOR_INTERVAL_SECONDS,
      display_order: item.display_order,
      is_active: item.is_active,
    });
    setStatus(null);
  };

  const handleDelete = async (item: SiteMonitor) => {
    if (!window.confirm(`Remove site monitor ${item.name}?`)) {
      return;
    }
    try {
      setStatus('Removing site monitor…');
      await deleteSiteMonitor(item.id);
      setItems((prev) => prev.filter((entry) => entry.id !== item.id));
      if (editingId === item.id) {
        resetForm({ keepStatus: true });
      }
      setStatus(`Removed ${item.name}.`);
    } catch (err) {
      setStatus(getUserFacingErrorMessage(err, 'Could not remove the site monitor. Try again.'));
    }
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!form.name.trim()) {
      setStatus('Enter a site-monitor name.');
      return;
    }
    if (!form.target.trim()) {
      setStatus('Enter a target host or URL.');
      return;
    }
    if (form.timeout_ms === '') {
      setStatus('Enter a success timeout.');
      return;
    }
    if (form.warning_consecutive_failures === '' || form.critical_consecutive_failures === '') {
      setStatus('Enter both failure thresholds.');
      return;
    }
    if (Number(form.critical_consecutive_failures) <= Number(form.warning_consecutive_failures)) {
      setStatus('Error failures must be greater than warning failures.');
      return;
    }
    if (form.check_interval_seconds === '') {
      setStatus('Enter a check interval.');
      return;
    }
    if (isHttpCheck && parsedStatusCodes.length === 0) {
      setStatus('Enter at least one valid HTTP status code.');
      return;
    }

    const payload: SiteMonitorConfig = {
      name: form.name.trim(),
      check_type: form.check_type,
      target: form.target.trim(),
      expected_status_codes: isHttpCheck ? parsedStatusCodes : [],
      expected_response_substring: isHttpCheck ? form.expected_response_substring.trim() || null : null,
      timeout_ms: Number(form.timeout_ms),
      warning_consecutive_failures: Number(form.warning_consecutive_failures),
      critical_consecutive_failures: Number(form.critical_consecutive_failures),
      check_interval_seconds: Number(form.check_interval_seconds),
      display_order: Number(form.display_order || 0),
      is_active: form.is_active,
    };

    try {
      setStatus(editingId === null ? 'Saving site monitor…' : 'Updating site monitor…');
      const saved = editingId === null ? await createSiteMonitor(payload) : await updateSiteMonitor(editingId, payload);
      setItems((prev) => normalizeSiteMonitors([...prev.filter((entry) => entry.id !== saved.id), saved]));
      setStatus(editingId === null ? 'Site monitor added.' : 'Site monitor updated.');
      resetForm({ keepStatus: true });
    } catch (err) {
      setStatus(getUserFacingErrorMessage(err, 'Could not save the site monitor. Review the form and try again.'));
    }
  };

  return (
    <section id="site-monitoring-admin" className="d-flex flex-column gap-3">
      <div className="d-flex justify-content-between align-items-center flex-wrap gap-2">
        <div>
          <h5 className="mb-0">Site monitoring</h5>
          <p className="text-secondary small mb-0">Configure ping and HTTP checks for websites and external endpoints.</p>
        </div>
      </div>

      <div className="card bg-dark border border-secondary">
        <div className="card-header border-secondary text-uppercase fw-semibold">Configured Checks</div>
        <div className="card-body d-flex flex-column gap-3">
          {loading ? (
            <div className="text-secondary small">Loading site monitors…</div>
          ) : items.length === 0 ? (
            <div className="text-secondary small">No site monitors configured yet.</div>
          ) : (
            <div className="list-group">
              {items.map((item) => (
                <div
                  className="list-group-item bg-dark text-light border-secondary d-flex flex-column flex-md-row justify-content-between gap-3"
                  key={item.id}
                >
                  <div>
                    <div className="fw-semibold d-flex align-items-center gap-2 flex-wrap">
                      {item.name}
                      <span className="badge bg-secondary text-uppercase">{item.check_type}</span>
                      {!item.is_active && <span className="badge bg-warning text-dark">Inactive</span>}
                    </div>
                    <div className="small text-secondary">{item.target}</div>
                    <div className="small text-secondary mt-1">
                      Timeout {item.timeout_ms} ms, warn after {item.warning_consecutive_failures} failures, error after{' '}
                      {item.critical_consecutive_failures} failures, every {SITE_MONITOR_INTERVAL_SECONDS / 60} minutes
                      {item.check_type === 'http' && item.expected_status_codes.length > 0
                        ? `, HTTP ${item.expected_status_codes.join(', ')}`
                        : ''}
                    </div>
                  </div>
                  <div className="d-flex gap-2 flex-wrap align-items-start">
                    <button className="btn btn-sm btn-outline-light" type="button" onClick={() => handleEdit(item)}>
                      Edit
                    </button>
                    <button className="btn btn-sm btn-outline-danger" type="button" onClick={() => void handleDelete(item)}>
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="card bg-dark border border-secondary">
        <div className="card-header border-secondary text-uppercase fw-semibold">
          {editingId === null ? 'Add Site Monitor' : 'Edit Site Monitor'}
        </div>
        <div className="card-body d-flex flex-column gap-3">
          {status && (
            <div
              className={`alert mb-0 ${status.toLowerCase().includes('could not') || status.toLowerCase().includes('enter ') ? 'alert-warning' : 'alert-secondary'}`}
            >
              {status}
            </div>
          )}
          <form className="d-flex flex-column gap-3" onSubmit={handleSubmit}>
            <div className="row g-3">
              <div className="col-12 col-lg-6">
                <label className="form-label">Name</label>
                <input
                  className="form-control bg-dark text-light border-secondary"
                  value={form.name}
                  onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                  placeholder="Homepage"
                />
              </div>
              <div className="col-12 col-lg-6">
                <label className="form-label">Check Type</label>
                <select
                  className="form-select bg-dark text-light border-secondary"
                  value={form.check_type}
                  onChange={(event) =>
                    setForm((prev) => ({
                      ...prev,
                      check_type: event.target.value as SiteMonitorCheckType,
                      expected_status_codes: event.target.value === 'http' ? prev.expected_status_codes || '200' : '200',
                      expected_response_substring: event.target.value === 'http' ? prev.expected_response_substring : '',
                    }))
                  }
                >
                  <option value="ping">Ping</option>
                  <option value="http">HTTP GET</option>
                </select>
              </div>
              <div className="col-12">
                <label className="form-label">{isHttpCheck ? 'URL' : 'Host / IP'}</label>
                <input
                  className="form-control bg-dark text-light border-secondary"
                  value={form.target}
                  onChange={(event) => setForm((prev) => ({ ...prev, target: event.target.value }))}
                  placeholder={isHttpCheck ? 'https://example.com/healthz' : 'example.com'}
                />
              </div>
              {isHttpCheck && (
                <>
                  <div className="col-12 col-lg-6">
                    <label className="form-label">Valid Status Codes</label>
                    <input
                      className="form-control bg-dark text-light border-secondary"
                      value={form.expected_status_codes}
                      onChange={(event) =>
                        setForm((prev) => ({ ...prev, expected_status_codes: event.target.value }))
                      }
                      placeholder="200, 201"
                    />
                    <div className="form-text">Comma-separated list. Example: `200, 204`.</div>
                  </div>
                  <div className="col-12 col-lg-6">
                    <label className="form-label">Expected Response Text</label>
                    <input
                      className="form-control bg-dark text-light border-secondary"
                      value={form.expected_response_substring}
                      onChange={(event) =>
                        setForm((prev) => ({ ...prev, expected_response_substring: event.target.value }))
                      }
                      placeholder="optional substring"
                    />
                    <div className="form-text">Optional substring that must appear in the response body.</div>
                  </div>
                </>
              )}
              <div className="col-12 col-lg-4">
                <label className="form-label">Success Timeout (ms)</label>
                <input
                  type="number"
                  min={100}
                  className="form-control bg-dark text-light border-secondary"
                  value={form.timeout_ms}
                  onChange={(event) =>
                    setForm((prev) => ({
                      ...prev,
                      timeout_ms: event.target.value === '' ? '' : Number(event.target.value),
                    }))
                  }
                />
                <div className="form-text">A response slower than this counts as a failed check.</div>
              </div>
              <div className="col-12 col-lg-4">
                <label className="form-label">Warning After N Failures</label>
                <input
                  type="number"
                  min={1}
                  className="form-control bg-dark text-light border-secondary"
                  value={form.warning_consecutive_failures}
                  onChange={(event) =>
                    setForm((prev) => ({
                      ...prev,
                      warning_consecutive_failures: event.target.value === '' ? '' : Number(event.target.value),
                    }))
                  }
                />
              </div>
              <div className="col-12 col-lg-4">
                <label className="form-label">Error After N Failures</label>
                <input
                  type="number"
                  min={1}
                  className="form-control bg-dark text-light border-secondary"
                  value={form.critical_consecutive_failures}
                  onChange={(event) =>
                    setForm((prev) => ({
                      ...prev,
                      critical_consecutive_failures: event.target.value === '' ? '' : Number(event.target.value),
                    }))
                  }
                />
              </div>
              <div className="col-12 col-lg-6">
                <label className="form-label">Check Interval (s)</label>
                <input
                  type="number"
                  min={SITE_MONITOR_INTERVAL_SECONDS}
                  max={SITE_MONITOR_INTERVAL_SECONDS}
                  className="form-control bg-dark text-light border-secondary"
                  value={form.check_interval_seconds}
                  disabled
                  onChange={(event) =>
                    setForm((prev) => ({
                      ...prev,
                      check_interval_seconds: event.target.value === '' ? '' : Number(event.target.value),
                    }))
                  }
                />
                <div className="form-text">Fixed at 1800 seconds so 48 rectangles represent one full day.</div>
              </div>
              <div className="col-12 col-lg-6">
                <label className="form-label">Display Order</label>
                <input
                  type="number"
                  min={0}
                  className="form-control bg-dark text-light border-secondary"
                  value={form.display_order}
                  onChange={(event) =>
                    setForm((prev) => ({
                      ...prev,
                      display_order: event.target.value === '' ? '' : Number(event.target.value),
                    }))
                  }
                />
              </div>
              <div className="col-12 col-lg-6 d-flex align-items-end">
                <div className="form-check">
                  <input
                    id="site-monitor-active"
                    type="checkbox"
                    className="form-check-input"
                    checked={form.is_active}
                    onChange={(event) => setForm((prev) => ({ ...prev, is_active: event.target.checked }))}
                  />
                  <label className="form-check-label" htmlFor="site-monitor-active">
                    Check is enabled
                  </label>
                </div>
              </div>
            </div>

            <div className="d-flex gap-2 flex-wrap">
              <button className="btn btn-light text-dark" type="submit">
                {editingId === null ? 'Add site monitor' : 'Save changes'}
              </button>
              {editingId !== null && (
                <button className="btn btn-outline-light" type="button" onClick={() => resetForm()}>
                  Cancel edit
                </button>
              )}
            </div>
          </form>
        </div>
      </div>
    </section>
  );
}
