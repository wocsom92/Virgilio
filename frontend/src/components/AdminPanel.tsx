import { FormEvent, useEffect, useMemo, useState } from 'react';
import {
  BackendCreatePayload,
  MountDisplayEntry,
  MountMetricSelection,
  MonitoredBackend,
  SelectedMetrics,
  WarningThresholds,
  TelegramSettings,
  AuthUser,
  AuthRole,
  fetchDatabaseSize,
  createBackend,
  createUser,
  deleteBackend,
  deleteUser,
  fetchBackendMounts,
  fetchSystemSettings,
  getTelegramSettings,
  listBackends,
  listUsers,
  sendTelegramStats,
  sendTelegramWarnings,
  updateBackend,
  updateTelegramSettings,
  updateSystemSettings,
  requestReboot,
  rebootBackendHost,
} from '../api/client';
import { normalizeMountMetricSelection } from '../utils/mountMetrics';

interface AdminPanelProps {
  currentUser: AuthUser | null;
}

const MOUNTED_USAGE_KEY = 'mounted_usage' as const;

const metricOptions = [
  { key: 'cpu_temperature_c', label: 'CPU temperature' },
  { key: 'ram_used_percent', label: 'RAM usage' },
  { key: 'disk_usage_percent', label: 'Disk usage' },
  { key: MOUNTED_USAGE_KEY, label: 'Mounted volumes' },
  { key: 'cpu_load', label: 'CPU load' },
  { key: 'uptime_seconds', label: 'Uptime' },
] as const;

const metricOptionKeys = new Set(metricOptions.map((option) => option.key));
const extraSelectedKeys = ['network_interfaces'];
const ADMIN_SECTIONS = [
  { id: 'manage-backends', label: 'Manage backends', description: 'Create, edit, and order monitored servers.' },
  { id: 'telegram-bots', label: 'Telegram bots', description: 'Configure notifications and test commands.' },
  { id: 'administration', label: 'Administration', description: 'Access control and host-level controls.' },
];
type AdminSectionId = (typeof ADMIN_SECTIONS)[number]['id'];
type BackendSectionId = `backend-${number}`;
type SectionId = AdminSectionId | BackendSectionId;

const createDefaultSelectedMetrics = (): SelectedMetrics =>
  metricOptions.reduce<SelectedMetrics>((acc, option) => {
    if (option.key === MOUNTED_USAGE_KEY) {
      acc[option.key] = {
        enabled: true,
        mounts: [{ path: '/', label: '/' }],
      };
    } else {
      acc[option.key] = true;
    }
    return acc;
  }, {});

const normalizeSelectedMetrics = (
  raw: SelectedMetrics | null | undefined
): SelectedMetrics => {
  const base = createDefaultSelectedMetrics();
  if (!raw) {
    return base;
  }
  const normalized: SelectedMetrics = { ...base };
  for (const option of metricOptions) {
    const value = raw[option.key];
    if (value === undefined) {
      continue;
    }
    if (option.key === MOUNTED_USAGE_KEY) {
      if (typeof value === 'boolean') {
        if (!value) {
          normalized[option.key] = { enabled: false, mounts: [] };
        }
      } else {
        normalized[option.key] = normalizeMountMetricSelection(value);
      }
    } else if (typeof value === 'boolean') {
      normalized[option.key] = value;
    } else {
      normalized[option.key] = Boolean(value);
    }
  }
  for (const key of extraSelectedKeys) {
    const value = raw[key];
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      normalized[key] = value.map((entry) => String(entry).trim()).filter((entry) => entry.length > 0);
    } else if (typeof value === 'string') {
      normalized[key] = value
        .split(',')
        .map((entry) => entry.trim())
        .filter((entry) => entry.length > 0);
    }
  }
  return normalized;
};

const isMetricEnabled = (
  selected: SelectedMetrics | null | undefined,
  key: string
): boolean => {
  const value = selected?.[key];
  if (typeof value === 'boolean') {
    return value;
  }
  if (value && typeof value === 'object' && 'enabled' in value) {
    return Boolean((value as MountMetricSelection).enabled);
  }
  return false;
};

const sanitizeSelectedMetrics = (selected: SelectedMetrics): SelectedMetrics => {
  const sanitized: SelectedMetrics = createDefaultSelectedMetrics();
  for (const [key, value] of Object.entries(selected)) {
    if (metricOptionKeys.has(key)) {
      if (key === MOUNTED_USAGE_KEY) {
        const mountSelection = normalizeMountMetricSelection(value);
        sanitized[key] = {
          enabled: mountSelection.enabled,
          mounts: mountSelection.mounts
            .map((entry) => {
              const path = entry.path.trim();
              if (!path) {
                return null;
              }
              const label = entry.label.trim() || path;
              return { path, label };
            })
            .filter((entry): entry is MountDisplayEntry => entry !== null),
        };
      } else {
        sanitized[key] = typeof value === 'boolean' ? value : Boolean(value);
      }
    } else if (extraSelectedKeys.includes(key)) {
      if (Array.isArray(value)) {
        sanitized[key] = value
          .map((entry) => String(entry).trim())
          .filter((entry) => entry.length > 0);
      } else if (typeof value === 'string') {
        sanitized[key] = value
          .split(',')
          .map((entry) => entry.trim())
          .filter((entry) => entry.length > 0);
      }
    }
  }
  for (const key of extraSelectedKeys) {
    if (!(key in sanitized)) {
      sanitized[key] = [];
    }
  }
  return sanitized;
};

const WARN_THRESHOLD_DEFAULTS: WarningThresholds = {
  cpu_temperature_c: 80,
  ram_used_percent: 90,
  disk_usage_percent: 90,
  mounted_usage_percent: 90,
};

const normalizeWarnThresholds = (raw: WarningThresholds | null | undefined): WarningThresholds => ({
  ...WARN_THRESHOLD_DEFAULTS,
  ...(raw ?? {}),
});

const formatBytes = (bytes: number | null | undefined): string => {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return 'N/A';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(2)} ${units[unitIndex]}`;
};

const createEmptyTelegramSettings = (): TelegramSettings => ({
  id: 0,
  bot_token: '',
  default_chat_id: '',
  is_active: false,
  warn_thresholds: normalizeWarnThresholds(null),
});

const normalizeTelegramSettings = (settings: TelegramSettings): TelegramSettings => ({
  ...settings,
  warn_thresholds: normalizeWarnThresholds(settings.warn_thresholds),
});

const ensureTelegramState = (state: TelegramSettings | null): TelegramSettings => {
  if (!state) {
    return createEmptyTelegramSettings();
  }
  if (!state.warn_thresholds) {
    return { ...state, warn_thresholds: normalizeWarnThresholds(null) };
  }
  return state;
};

const sanitizeWarnThresholdsForSave = (
  thresholds: WarningThresholds | null | undefined
): WarningThresholds => normalizeWarnThresholds(thresholds);

const warnThresholdFields: Array<{
  key: keyof WarningThresholds;
  label: string;
  helper: string;
  min: number;
  max: number;
  step: number;
  suffix?: string;
}> = [
  {
    key: 'cpu_temperature_c',
    label: 'CPU temperature (°C)',
    helper: 'Default 80°C',
    min: 0,
    max: 150,
    step: 0.5,
    suffix: '°C',
  },
  {
    key: 'ram_used_percent',
    label: 'RAM usage (%)',
    helper: 'Default 90% of installed RAM',
    min: 0,
    max: 100,
    step: 1,
    suffix: '%',
  },
  {
    key: 'disk_usage_percent',
    label: 'Root disk usage (%)',
    helper: 'Default 90% of / usage',
    min: 0,
    max: 100,
    step: 1,
    suffix: '%',
  },
  {
    key: 'mounted_usage_percent',
    label: 'Mounted volume usage (%)',
    helper: 'Default 90% across mounted volumes',
    min: 0,
    max: 100,
    step: 1,
    suffix: '%',
  },
];

type BackendFormState = BackendCreatePayload & { selected_metrics: SelectedMetrics };

const sortBackends = (items: MonitoredBackend[]): MonitoredBackend[] =>
  [...items].sort((a, b) => {
    if (a.display_order !== b.display_order) {
      return a.display_order - b.display_order;
    }
    return a.name.localeCompare(b.name);
  });

const createInitialForm = (): BackendFormState => ({
  name: '',
  base_url: '',
  api_token: '',
  is_active: true,
  display_order: 0,
  poll_interval_seconds: 60,
  notes: '',
  selected_metrics: createDefaultSelectedMetrics(),
});

export function AdminPanel({ currentUser }: AdminPanelProps) {
  const extractErrorMessage = (err: unknown, fallback: string): string => {
    if (err && typeof err === 'object') {
      const data = (err as { response?: { data?: Record<string, unknown> } }).response?.data;
      const detail = data?.detail ?? data?.error;
      if (typeof detail === 'string' && detail.trim()) {
        return detail.trim();
      }
    }
    if (err instanceof Error && err.message.trim()) {
      return err.message;
    }
    return fallback;
  };

  const [backends, setBackends] = useState<MonitoredBackend[]>([]);
  const [form, setForm] = useState<BackendFormState>(() => createInitialForm());
  const [editingId, setEditingId] = useState<number | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [telegram, setTelegram] = useState<TelegramSettings | null>(null);
  const [telegramStatus, setTelegramStatus] = useState<string | null>(null);
  const [rebootStatus, setRebootStatus] = useState<string | null>(null);
  const [rebootPending, setRebootPending] = useState(false);
  const [dbSizeBytes, setDbSizeBytes] = useState<number | null>(null);
  const [dbSizeStatus, setDbSizeStatus] = useState<string | null>(null);
  const [retentionDays, setRetentionDays] = useState<number | ''>('');
  const [retentionStatus, setRetentionStatus] = useState<string | null>(null);
  const [mountOptions, setMountOptions] = useState<string[]>([]);
  const [loadingMountOptions, setLoadingMountOptions] = useState(false);
  const [mountOptionsError, setMountOptionsError] = useState<string | null>(null);
  const [isOrdering, setIsOrdering] = useState(false);
  const [rebootingBackendId, setRebootingBackendId] = useState<number | null>(null);
  const [activeSection, setActiveSection] = useState<SectionId>(ADMIN_SECTIONS[0].id);
  const [showNewForm, setShowNewForm] = useState(false);
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [userStatus, setUserStatus] = useState<string | null>(null);
  const [userForm, setUserForm] = useState<{ username: string; password: string; role: AuthRole }>({
    username: '',
    password: '',
    role: 'viewer',
  });

  const asBackendSectionId = (id: number): BackendSectionId => `backend-${id}` as const;
  const extractBackendIdFromSection = (section: SectionId): number | null => {
    if (typeof section === 'string' && section.startsWith('backend-')) {
      const parsed = Number(section.replace('backend-', ''));
      return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
  };

  const updateSelectedMetrics = (updater: (selected: SelectedMetrics) => SelectedMetrics) => {
    setForm((prev) => {
      const current = prev.selected_metrics ?? createDefaultSelectedMetrics();
      return {
        ...prev,
        selected_metrics: updater(current),
      };
    });
  };

  const loadMountOptions = async (backendId: number) => {
    setLoadingMountOptions(true);
    setMountOptionsError(null);
    try {
      const data = await fetchBackendMounts(backendId);
      const unique = Array.from(
        new Set(
          (data ?? [])
            .map((item) => (typeof item === 'string' ? item.trim() : ''))
            .filter((item) => item.length > 0)
        )
      ).sort();
      setMountOptions(unique);
      if (unique.length === 0) {
        setMountOptionsError('Monitor did not return any mount points.');
      }
    } catch (err) {
      setMountOptions([]);
      setMountOptionsError(err instanceof Error ? err.message : 'Unable to load mount points');
    } finally {
      setLoadingMountOptions(false);
    }
  };

  const isAuthenticated = Boolean(currentUser);
  const isAdmin = currentUser?.role === 'admin';
  const canFetchMountOptions = Boolean(editingId && isAdmin);

  useEffect(() => {
    if (!isAuthenticated) {
      setBackends([]);
      return;
    }
    void (async () => {
      try {
        const data = await listBackends();
        setBackends(sortBackends(data));
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unable to fetch backends');
      }
    })();
  }, [isAuthenticated]);

  useEffect(() => {
    if (!isAdmin) {
      setTelegram(null);
      setDbSizeBytes(null);
      return;
    }
    void (async () => {
      try {
        const settings = await getTelegramSettings();
        setTelegram(normalizeTelegramSettings(settings));
      } catch (err) {
        setTelegram(null);
        setTelegramStatus('Unable to load Telegram settings - admin access required.');
      }
    })();
  }, [isAdmin]);

  useEffect(() => {
    if (!isAdmin) {
      setDbSizeBytes(null);
      setDbSizeStatus(null);
      return;
    }
    setDbSizeStatus('Fetching database size…');
    void (async () => {
      try {
        const size = await fetchDatabaseSize();
        setDbSizeBytes(size);
        setDbSizeStatus(null);
      } catch (err) {
        setDbSizeBytes(null);
        setDbSizeStatus(extractErrorMessage(err, 'Unable to load database size'));
      }
    })();
  }, [isAdmin]);

  useEffect(() => {
    if (!isAdmin) {
      setRetentionDays('');
      setRetentionStatus(null);
      return;
    }
    setRetentionStatus('Loading retention…');
    void (async () => {
      try {
        const settings = await fetchSystemSettings();
        setRetentionDays(settings.retention_days);
        setRetentionStatus(null);
      } catch (err) {
        setRetentionDays('');
        setRetentionStatus(extractErrorMessage(err, 'Unable to load retention settings'));
      }
    })();
  }, [isAdmin]);

  useEffect(() => {
    if (!isAdmin) {
      setUsers([]);
      return;
    }
    setUserStatus('Loading users…');
    void (async () => {
      try {
        const data = await listUsers();
        setUsers(data);
        setUserStatus(null);
      } catch (err) {
        setUsers([]);
        setUserStatus(extractErrorMessage(err, 'Unable to load users'));
      }
    })();
  }, [isAdmin]);

  useEffect(() => {
    if (editingId && isAdmin) {
      void loadMountOptions(editingId);
    }
  }, [editingId, isAdmin]);

  const resetForm = () => {
    setForm(createInitialForm());
    setEditingId(null);
    setMountOptions([]);
    setMountOptionsError(null);
    setLoadingMountOptions(false);
    setStatus(null);
  };

  const prepareBackendEdit = (backendId: number) => {
    setEditingId(backendId);
    setForm(createInitialForm());
    setMountOptions([]);
    setMountOptionsError(null);
    setLoadingMountOptions(false);
    setStatus(null);
    setError(null);
  };

  const handleSectionChange = (sectionId: SectionId) => {
    const backendId = extractBackendIdFromSection(sectionId);
    if (backendId !== null) {
      prepareBackendEdit(backendId);
      setShowNewForm(false);
    } else {
      resetForm();
      setShowNewForm(false);
    }
    setActiveSection(sectionId);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!isAdmin) {
      setError('Admin privileges are required for changes.');
      return;
    }
    try {
      setStatus('Saving…');
      const payload: BackendCreatePayload = {
        ...form,
        selected_metrics: sanitizeSelectedMetrics(form.selected_metrics),
      };
      if (editingId) {
        await updateBackend(editingId, payload);
      } else {
        await createBackend(payload);
      }
      const updated = await listBackends();
      setBackends(sortBackends(updated));
      resetForm();
      setStatus('Saved successfully.');
      setShowNewForm(false);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to save backend');
      setStatus(null);
    }
  };

  const handleEdit = (backend: MonitoredBackend) => {
    handleSectionChange(asBackendSectionId(backend.id));
  };

  const withUpdatedMountSelection = (
    updater: (selection: MountMetricSelection) => MountMetricSelection
  ) => {
    updateSelectedMetrics((selected) => {
      const current = normalizeMountMetricSelection(selected[MOUNTED_USAGE_KEY]);
      return {
        ...selected,
        [MOUNTED_USAGE_KEY]: updater(current),
      };
    });
  };

  const updateMountEntry = (
    index: number,
    updater: (entry: MountDisplayEntry) => MountDisplayEntry
  ) => {
    withUpdatedMountSelection((selection) => ({
      ...selection,
      mounts: selection.mounts.map((entry, idx) =>
        idx === index ? updater(entry) : entry
      ),
    }));
  };

  const handleAddMountPoint = () => {
    const defaultPath =
      (mountOptions[0] ?? mountSettings.mounts[0]?.path ?? '').trim();
    withUpdatedMountSelection((selection) => ({
      ...selection,
      mounts: [
        ...selection.mounts,
        { path: defaultPath, label: defaultPath || '' },
      ],
    }));
  };

  const handleMountChange = (index: number, field: 'path' | 'label', value: string) => {
    updateMountEntry(index, (entry) => ({ ...entry, [field]: value }));
  };

  const handleMountSelectChange = (index: number, value: string) => {
    if (value === '__custom__') {
      updateMountEntry(index, (entry) => ({ ...entry, path: '' }));
      return;
    }
    updateMountEntry(index, (entry) => ({
      ...entry,
      path: value,
      label: entry.label?.trim().length ? entry.label : value,
    }));
  };

  const handleRemoveMount = (index: number) => {
    withUpdatedMountSelection((selection) => ({
      ...selection,
      mounts: selection.mounts.filter((_, idx) => idx !== index),
    }));
  };

  const renderBackendSection = ({
    title,
    description,
    cardTitle,
  }: {
    title: string;
    description: string;
    cardTitle: string;
  }) => (
    <section className="d-flex flex-column gap-3">
      <div className="d-flex justify-content-between align-items-center flex-wrap gap-2">
        <div>
          <h5 className="mb-0">{title}</h5>
          <p className="text-secondary small mb-0">{description}</p>
        </div>
        {!isAdmin && <span className="badge bg-warning text-dark">Admin access required to save changes</span>}
      </div>
      <div className="card bg-dark border border-secondary">
        <div className="card-header border-secondary text-uppercase fw-semibold">{cardTitle}</div>
        <div className="card-body">
          {error && <div className="alert alert-danger">{error}</div>}
          <form className="d-flex flex-column gap-3" onSubmit={handleSubmit}>
            {editingId && (
              <div className="alert alert-warning small py-2 mb-0">
                Editing backend #{editingId}. Values are prefilled; adjust the fields you want to change.
              </div>
            )}
            <div className="row g-3">
              <div className="col-md-6">
                <label className="form-label">Name</label>
                <input
                  className="form-control bg-dark text-light border-secondary"
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                  required
                />
              </div>
              <div className="col-md-6">
                <label className="form-label">Base URL</label>
                <input
                  className="form-control bg-dark text-light border-secondary"
                  value={form.base_url}
                  onChange={(event) => setForm({ ...form, base_url: event.target.value })}
                  placeholder="http://server:9000"
                  required
                />
              </div>
              <div className="col-md-6">
                <label className="form-label">Monitor Token</label>
                <input
                  className="form-control bg-dark text-light border-secondary"
                  value={form.api_token}
                  onChange={(event) => setForm({ ...form, api_token: event.target.value })}
                  required
                />
              </div>
              <div className="col-md-3">
                <label className="form-label">Display Order</label>
                <input
                  type="number"
                  className="form-control bg-dark text-light border-secondary"
                  value={form.display_order ?? 0}
                  onChange={(event) => setForm({ ...form, display_order: Number(event.target.value) })}
                />
              </div>
              <div className="col-md-3 d-flex align-items-end">
                <div className="form-check">
                  <input
                    className="form-check-input"
                    type="checkbox"
                    checked={form.is_active ?? true}
                    onChange={(event) => setForm({ ...form, is_active: event.target.checked })}
                    id="backend-active"
                  />
                  <label className="form-check-label" htmlFor="backend-active">
                    Active
                  </label>
                </div>
              </div>
            </div>
            <div className="row g-3">
              <div className="col-md-4">
                <label className="form-label">Polling Interval (sec)</label>
                <input
                  type="number"
                  min={30}
                  className="form-control bg-dark text-light border-secondary"
                  value={form.poll_interval_seconds ?? 60}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      poll_interval_seconds: Math.max(30, Number(event.target.value)),
                    })
                  }
                  required
                />
                <div className="form-text text-secondary">Minimum 30 seconds.</div>
              </div>
            </div>
            <div>
              <label className="form-label">Notes</label>
              <textarea
                className="form-control bg-dark text-light border-secondary"
                rows={2}
                value={form.notes}
                onChange={(event) => setForm({ ...form, notes: event.target.value })}
              />
            </div>
            <div className="card-panel rounded-3 p-3">
              <div className="d-flex justify-content-between align-items-center mb-2">
                <h6 className="text-uppercase card-panel__heading fw-semibold mb-0">Dashboard Metrics</h6>
                <span className="badge bg-light text-dark">{metricsSelectedCount} selected</span>
              </div>
              <div className="row g-2">
                {metricOptions.map((option) => (
                  <div className="col-6 col-md-4" key={option.key}>
                    <div className="form-check">
                      <input
                        className="form-check-input"
                        type="checkbox"
                        id={`metric-${option.key}`}
                        checked={isMetricEnabled(form.selected_metrics, option.key)}
                        onChange={(event) => {
                          const checked = event.target.checked;
                          if (option.key === MOUNTED_USAGE_KEY) {
                            withUpdatedMountSelection((selection) => ({
                              ...selection,
                              enabled: checked,
                            }));
                          } else {
                            updateSelectedMetrics((selected) => ({
                              ...selected,
                              [option.key]: checked,
                            }));
                          }
                        }}
                      />
                      <label className="form-check-label" htmlFor={`metric-${option.key}`}>
                        {option.label}
                      </label>
                    </div>
                  </div>
                ))}
              </div>
              <div className="row g-3 mt-2">
                <div className="col-md-6">
                  <label className="form-label">Network interfaces to chart (comma-separated)</label>
                  <input
                    className="form-control bg-dark text-light border-secondary"
                    value={networkInterfacesText}
                    onChange={(event) =>
                      setForm((prev) => ({
                        ...prev,
                        selected_metrics: {
                          ...(prev.selected_metrics ?? {}),
                          network_interfaces: event.target.value,
                        },
                      }))
                    }
                    placeholder="e.g. eth0, wlan0 (leave blank for all)"
                  />
                </div>
              </div>
            </div>
            {isMetricEnabled(form.selected_metrics, MOUNTED_USAGE_KEY) && (
              <div className="card-panel rounded-3 p-3">
                <div className="d-flex justify-content-between align-items-center mb-2 flex-wrap gap-2">
                  <h6 className="text-uppercase card-panel__heading fw-semibold mb-0">Mounted Volume Labels</h6>
                  <div className="d-flex gap-2">
                    {canFetchMountOptions && (
                      <button
                        className="btn btn-sm btn-outline-light"
                        type="button"
                        onClick={() => editingId && loadMountOptions(editingId)}
                        disabled={loadingMountOptions}
                      >
                        {loadingMountOptions ? 'Fetching…' : 'Refresh from monitor'}
                      </button>
                    )}
                    <button className="btn btn-sm btn-outline-light" type="button" onClick={handleAddMountPoint}>
                      Add mount point
                    </button>
                  </div>
                </div>
                {loadingMountOptions && (
                  <p className="card-panel__muted small mb-2">Fetching available mount points…</p>
                )}
                {mountOptionsError && (
                  <div className="alert alert-warning small py-2 mb-2">
                    {mountOptionsError}
                  </div>
                )}
                {!mountOptionsError &&
                  !loadingMountOptions &&
                  canFetchMountOptions &&
                  availableMountOptions.length > 0 && (
                    <p className="card-panel__muted small mb-2">
                      Available mount points: {availableMountOptions.join(', ')}
                    </p>
                  )}
                {mountSettings.mounts.length === 0 ? (
                  <p className="card-panel__muted small mb-0">
                    Add mount points to display their usage on the dashboard.
                  </p>
                ) : (
                  <div className="d-flex flex-column gap-3">
                    {mountSettings.mounts.map((mount, index) => (
                      <div className="row g-2 align-items-end" key={`${mount.path || 'mount'}-${index}`}>
                        <div className="col-12 col-md-5">
                          <label className="form-label small mb-1">Mount Path</label>
                          {availableMountOptions.length > 0 ? (
                            <>
                              <select
                                className="form-select bg-dark text-light border-secondary"
                                value={
                                  mount.path && availableMountOptions.includes(mount.path)
                                    ? mount.path
                                    : '__custom__'
                                }
                                onChange={(event) => handleMountSelectChange(index, event.target.value)}
                              >
                                <option value="">Select mount…</option>
                                {availableMountOptions.map((option) => (
                                  <option key={option} value={option}>
                                    {option}
                                  </option>
                                ))}
                                <option value="__custom__">Custom path…</option>
                              </select>
                              {(mount.path === '' || !availableMountOptions.includes(mount.path)) && (
                                <input
                                  className="form-control bg-dark text-light border-secondary mt-2"
                                  value={mount.path}
                                  onChange={(event) => handleMountChange(index, 'path', event.target.value)}
                                  placeholder="/mnt/storage"
                                />
                              )}
                            </>
                          ) : (
                            <input
                              className="form-control bg-dark text-light border-secondary"
                              value={mount.path}
                              onChange={(event) => handleMountChange(index, 'path', event.target.value)}
                              placeholder="/mnt/storage"
                            />
                          )}
                        </div>
                        <div className="col-12 col-md-5">
                          <label className="form-label small mb-1">Label</label>
                          <input
                            className="form-control bg-dark text-light border-secondary"
                            value={mount.label}
                            onChange={(event) => handleMountChange(index, 'label', event.target.value)}
                            placeholder="Storage"
                          />
                        </div>
                        <div className="col-12 col-md-2 d-grid">
                          <button
                            className="btn btn-sm btn-outline-danger"
                            type="button"
                            onClick={() => handleRemoveMount(index)}
                          >
                            Remove
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            <div className="d-flex gap-2">
              <button className="btn btn-light text-dark" type="submit" disabled={!isAdmin}>
                {editingId ? 'Update Backend' : 'Add Backend'}
              </button>
              <button className="btn btn-outline-light" type="button" onClick={resetForm}>
                Reset
              </button>
              <span className="text-secondary align-self-center">{status}</span>
            </div>
          </form>
        </div>
      </div>
    </section>
  );

  async function handleBackendReboot(backend: MonitoredBackend) {
    if (!isAdmin) return;
    try {
      setRebootingBackendId(backend.id);
      await rebootBackendHost(backend.id);
      setStatus(`Requested reboot for ${backend.name}`);
    } catch (err) {
      setStatus(extractErrorMessage(err, 'Unable to request reboot'));
    } finally {
      setRebootingBackendId(null);
    }
  }

  async function handleDelete(backend: MonitoredBackend) {
    if (!isAdmin) {
      setError('Admin privileges are required for changes.');
      return;
    }
    if (!window.confirm(`Remove backend ${backend.name}?`)) return;
    try {
      await deleteBackend(backend.id);
      setBackends((prev) => prev.filter((item) => item.id !== backend.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to delete backend');
    }
  }

  async function handleReorder(backendId: number, direction: 'up' | 'down') {
    if (!isAdmin) {
      setError('Admin privileges are required for changes.');
      return;
    }
    if (isOrdering) {
      return;
    }
    const delta = direction === 'up' ? -1 : 1;
    const previous = backends.map((item) => ({ ...item }));
    const current = [...backends];
    const index = current.findIndex((item) => item.id === backendId);
    if (index === -1) {
      return;
    }
    const targetIndex = index + delta;
    if (targetIndex < 0 || targetIndex >= current.length) {
      return;
    }
    const [moved] = current.splice(index, 1);
    current.splice(targetIndex, 0, moved);
    const reindexed = current.map((item, idx) => ({ ...item, display_order: idx }));
    setBackends(reindexed);
    setIsOrdering(true);
    setStatus('Updating order…');
    setError(null);
    try {
      await Promise.all(
        reindexed.map((backend) =>
          updateBackend(backend.id, { display_order: backend.display_order })
        )
      );
      const refreshed = await listBackends();
      setBackends(sortBackends(refreshed));
      setStatus('Order updated.');
    } catch (err) {
      setBackends(previous);
      setStatus(null);
      setError(err instanceof Error ? err.message : 'Unable to update order');
    } finally {
      setIsOrdering(false);
    }
  }

  const mountSettings = useMemo(
    () => normalizeMountMetricSelection(form.selected_metrics?.[MOUNTED_USAGE_KEY]),
    [form.selected_metrics]
  );

  const networkInterfacesText = useMemo(() => {
    const raw = form.selected_metrics?.network_interfaces;
    if (Array.isArray(raw)) return raw.join(', ');
    if (typeof raw === 'string') return raw;
    return '';
  }, [form.selected_metrics]);

  const metricsSelectedCount = useMemo(
    () =>
      metricOptions.reduce(
        (count, option) => (isMetricEnabled(form.selected_metrics, option.key) ? count + 1 : count),
        0
      ),
    [form.selected_metrics]
  );

  const availableMountOptions = useMemo(() => {
    const fromSettings = mountSettings.mounts
      .map((mount) => mount.path.trim())
      .filter((path) => path.length > 0);
    const combined = [...mountOptions, ...fromSettings];
    return Array.from(new Set(combined)).sort();
  }, [mountOptions, mountSettings.mounts]);

  const sortedUsers = useMemo(
    () => [...users].sort((a, b) => a.username.localeCompare(b.username)),
    [users]
  );

  const handleTelegramSave = async (event: FormEvent) => {
    event.preventDefault();
    if (!isAdmin || !telegram) return;
    try {
      setTelegramStatus('Saving Telegram settings…');
      const updated = await updateTelegramSettings({
        bot_token: telegram.bot_token,
        default_chat_id: telegram.default_chat_id,
        is_active: telegram.is_active,
        warn_thresholds: sanitizeWarnThresholdsForSave(telegram.warn_thresholds),
      });
      setTelegram(normalizeTelegramSettings(updated));
      setTelegramStatus('Telegram settings saved.');
    } catch (err) {
      setTelegramStatus(err instanceof Error ? err.message : 'Unable to save Telegram settings');
    }
  };

  const handleTelegramCommand = async (command: 'stats' | 'warn') => {
    if (!isAdmin) return;
    try {
      setTelegramStatus(`Sending /${command}…`);
      const result = command === 'stats' ? await sendTelegramStats() : await sendTelegramWarnings();
      setTelegramStatus(`Sent: ${result.message.slice(0, 160)}${result.message.length > 160 ? '…' : ''}`);
    } catch (err) {
      setTelegramStatus(err instanceof Error ? err.message : 'Unable to send Telegram message');
    }
  };

  const handleRetentionSave = async () => {
    if (!isAdmin) return;
    if (retentionDays === '') {
      setRetentionStatus('Please enter a retention value.');
      return;
    }
    try {
      setRetentionStatus('Saving retention…');
      const clamped = Math.min(90, Math.max(1, Number(retentionDays)));
      const updated = await updateSystemSettings({ retention_days: clamped });
      setRetentionDays(updated.retention_days);
      setRetentionStatus('Retention saved.');
    } catch (err) {
      setRetentionStatus(extractErrorMessage(err, 'Unable to update retention'));
    }
  };

  const handleRebootRequest = async () => {
    if (!isAdmin) return;
    try {
      setRebootPending(true);
      setRebootStatus('Requesting restart…');
      const result = await requestReboot('Admin UI');
      const requestedAt = result?.requested_at ? new Date(result.requested_at).toLocaleString() : '';
      setRebootStatus(
        `Restart command sent.${requestedAt ? ` Requested at ${requestedAt}.` : ''} You will receive a Telegram notice when back online.`
      );
    } catch (err) {
      setRebootStatus(extractErrorMessage(err, 'Unable to request restart'));
    } finally {
      setRebootPending(false);
    }
  };

  const handleUserSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!isAdmin) return;
    try {
      setUserStatus('Saving user…');
      const newUser = await createUser(userForm);
      setUsers((prev) => {
        const filtered = prev.filter((user) => user.username !== newUser.username);
        return [...filtered, newUser].sort((a, b) => a.username.localeCompare(b.username));
      });
      setUserForm((prev) => ({ ...prev, username: '', password: '' }));
      setUserStatus('User saved.');
    } catch (err) {
      setUserStatus(extractErrorMessage(err, 'Unable to save user'));
    }
  };

  const handleUserDelete = async (user: AuthUser) => {
    if (!isAdmin) return;
    if (user.id === currentUser?.id) {
      setUserStatus("You can't delete your own account.");
      return;
    }
    if (!window.confirm(`Remove user ${user.username}?`)) return;
    try {
      setUserStatus('Removing user…');
      await deleteUser(user.id);
      setUsers((prev) => prev.filter((item) => item.id !== user.id));
      setUserStatus('User removed.');
    } catch (err) {
      setUserStatus(extractErrorMessage(err, 'Unable to remove user'));
    }
  };

  const handleThresholdChange = (key: keyof WarningThresholds, rawValue: string) => {
    const numericValue = rawValue === '' ? null : Number(rawValue);
    if (rawValue !== '' && Number.isNaN(numericValue)) {
      return;
    }
    setTelegram((prev) => {
      const base = ensureTelegramState(prev);
      const thresholds = base.warn_thresholds ?? normalizeWarnThresholds(null);
      return {
        ...base,
        warn_thresholds: {
          ...thresholds,
          [key]: numericValue,
        },
      };
    });
  };

  const activeBackendId = extractBackendIdFromSection(activeSection);
  const activeBackend = activeBackendId ? backends.find((backend) => backend.id === activeBackendId) : null;

  useEffect(() => {
    if (!isAdmin) {
      return;
    }
    if (activeBackend && editingId === activeBackend.id) {
      setForm({
        name: activeBackend.name || '',
        base_url: activeBackend.base_url || '',
        api_token: activeBackend.api_token || '',
        is_active: activeBackend.is_active ?? true,
        display_order: activeBackend.display_order ?? 0,
        poll_interval_seconds: activeBackend.poll_interval_seconds ?? 60,
        notes: activeBackend.notes || '',
        selected_metrics: normalizeSelectedMetrics(activeBackend.selected_metrics),
      });
    }
  }, [activeBackend, editingId, isAdmin]);

  if (!isAuthenticated) {
    return <div className="alert alert-secondary text-dark">Sign in to manage backends and settings.</div>;
  }

  if (!isAdmin) {
    return (
      <div className="alert alert-warning text-dark">
        You are signed in as a viewer. Admin access is required to manage backends or settings.
      </div>
    );
  }

  const renderBackendList = () => (
    <>
      <h6 className="text-uppercase text-secondary">Existing Backends</h6>
      <div className="list-group">
        {backends.length === 0 ? (
          <div className="list-group-item bg-dark text-secondary border-secondary">No backends yet.</div>
        ) : (
          backends.map((backend, index) => (
            <div
              key={backend.id}
              className="list-group-item bg-dark text-light border-secondary d-flex flex-column flex-md-row justify-content-between gap-3"
            >
              <div>
                <div className="fw-semibold d-flex align-items-center gap-2">
                  {backend.name}
                  <span className="badge bg-secondary text-uppercase">#{index + 1}</span>
                </div>
                <div className="small text-secondary">{backend.base_url}</div>
              </div>
              <div className="d-flex flex-wrap gap-2 align-items-center justify-content-start justify-content-md-end">
                <div className="btn-group" role="group" aria-label="Reorder backend">
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-light"
                    onClick={() => void handleReorder(backend.id, 'up')}
                    disabled={isOrdering || !isAdmin || index === 0}
                  >
                    Move up
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-light"
                    onClick={() => void handleReorder(backend.id, 'down')}
                    disabled={isOrdering || !isAdmin || index === backends.length - 1}
                  >
                    Move down
                  </button>
                </div>
                <button className="btn btn-sm btn-outline-light" onClick={() => handleEdit(backend)} disabled={!isAdmin}>
                  Edit
                </button>
                <button
                  className="btn btn-sm btn-outline-warning"
                  onClick={() => void handleBackendReboot(backend)}
                  disabled={rebootingBackendId === backend.id || !isAdmin}
                >
                  {rebootingBackendId === backend.id ? 'Rebooting…' : 'Reboot host'}
                </button>
                <button className="btn btn-sm btn-outline-danger" onClick={() => void handleDelete(backend)} disabled={!isAdmin}>
                  Delete
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </>
  );

  return (
    <div className="row g-4">
      <div className="col-12 col-lg-3 order-lg-1">
        <div className="card bg-dark border border-secondary position-sticky" style={{ top: '1rem' }}>
          <div className="card-header border-secondary text-uppercase fw-semibold">Admin Menu</div>
          <div className="card-body d-flex flex-column gap-2">
            {ADMIN_SECTIONS.map((section) => {
              const isActive = activeSection === section.id;
              return (
                <button
                  key={section.id}
                  type="button"
                  className={`btn w-100 text-start ${isActive ? 'btn-light text-dark' : 'btn-outline-light'}`}
                  onClick={() => handleSectionChange(section.id)}
                >
                  <div className="fw-semibold">{section.label}</div>
                </button>
              );
            })}
            <hr className="border-secondary my-2" />
            <div className="text-uppercase small text-secondary">Backends</div>
            {backends.length === 0 ? (
              <span className="text-secondary small">No backends yet.</span>
            ) : (
              backends.map((backend) => {
                const sectionId = asBackendSectionId(backend.id);
                const isActive = activeSection === sectionId;
                return (
                  <button
                    key={backend.id}
                    type="button"
                    className={`btn w-100 text-start ${isActive ? 'btn-light text-dark' : 'btn-outline-light'}`}
                    onClick={() => handleSectionChange(sectionId)}
                  >
                    <div className="fw-semibold">{backend.name}</div>
                  </button>
                );
              })
            )}
          </div>
        </div>
      </div>
      <div className="col-12 col-lg-9 order-lg-2 d-flex flex-column gap-4">
        {activeSection === 'manage-backends' && (
          <section id="manage-backends" className="d-flex flex-column gap-3">
            <div className="d-flex justify-content-between align-items-center flex-wrap gap-2">
              <div>
                <h5 className="mb-0">Manage backends</h5>
                <p className="text-secondary small mb-0">Create, edit, and organize monitored servers.</p>
              </div>
              {!isAdmin && <span className="badge bg-warning text-dark">Admin access required to save changes</span>}
            </div>
            <div className="card bg-dark border border-secondary">
              <div className="card-body d-flex flex-column gap-3">
                {renderBackendList()}
                <div className="d-flex justify-content-between align-items-center flex-wrap gap-2">
                  <h6 className="text-uppercase text-secondary mb-0">New backend</h6>
                  <button
                    className={`btn btn-sm ${showNewForm ? 'btn-outline-light' : 'btn-light text-dark'}`}
                    type="button"
                    onClick={() => {
                      resetForm();
                      setShowNewForm((prev) => !prev);
                    }}
                  >
                    {showNewForm ? 'Hide form' : 'Add new backend'}
                  </button>
                </div>
                {showNewForm &&
                  renderBackendSection({
                    title: 'Add backend',
                    description: 'Enter details to add a new monitored server.',
                    cardTitle: 'New Backend',
                  })}
              </div>
            </div>
          </section>
        )}
        {activeBackend &&
          renderBackendSection({
            title: `Backend settings: ${activeBackend.name}`,
            description: activeBackend.base_url || 'Enter new values to update this backend.',
            cardTitle: activeBackend.name,
          })}
        {activeSection === 'telegram-bots' && (
          <section id="telegram-bots" className="d-flex flex-column gap-3">
            <div className="d-flex justify-content-between align-items-center flex-wrap gap-2">
              <div>
                <h5 className="mb-0">Telegram bots</h5>
                <p className="text-secondary small mb-0">Configure notifications and test outgoing messages.</p>
              </div>
            </div>
            <div className="card bg-dark border border-secondary">
              <div className="card-header border-secondary text-uppercase fw-semibold">Telegram Bot</div>
              <div className="card-body">
                {!isAdmin ? (
                  <div className="alert alert-secondary text-dark">Admin access is required to configure Telegram.</div>
                ) : (
                  <form className="d-flex flex-column gap-3" onSubmit={handleTelegramSave}>
                    <div>
                      <label className="form-label">Bot Token</label>
                      <input
                        className="form-control bg-dark text-light border-secondary"
                        value={telegram?.bot_token ?? ''}
                        onChange={(event) =>
                          setTelegram((prev) => {
                            const base = ensureTelegramState(prev);
                            return { ...base, bot_token: event.target.value };
                          })
                        }
                        required
                      />
                    </div>
                    <div>
                      <label className="form-label">Default Chat ID</label>
                      <input
                        className="form-control bg-dark text-light border-secondary"
                        value={telegram?.default_chat_id ?? ''}
                        onChange={(event) =>
                          setTelegram((prev) => {
                            const base = ensureTelegramState(prev);
                            return { ...base, default_chat_id: event.target.value };
                          })
                        }
                        required
                      />
                    </div>
                    <div className="form-check form-switch">
                      <input
                        className="form-check-input"
                        type="checkbox"
                        id="telegram-active"
                        checked={telegram?.is_active ?? false}
                        onChange={(event) =>
                          setTelegram((prev) => {
                            const base = ensureTelegramState(prev);
                            return { ...base, is_active: event.target.checked };
                          })
                        }
                      />
                      <label className="form-check-label" htmlFor="telegram-active">
                        Enable Telegram notifications
                      </label>
                    </div>
                    <div>
                      <label className="form-label fw-semibold">Warning thresholds</label>
                      <div className="row g-3">
                        {warnThresholdFields.map((field) => (
                          <div className="col-sm-6" key={field.key}>
                            <label className="form-label text-uppercase small text-secondary">
                              {field.label}
                            </label>
                            <input
                              type="number"
                              min={field.min}
                              max={field.max}
                              step={field.step}
                              className="form-control bg-dark text-light border-secondary"
                              value={telegram?.warn_thresholds?.[field.key] ?? ''}
                              onChange={(event) => handleThresholdChange(field.key, event.target.value)}
                            />
                            <div className="form-text text-secondary small">{field.helper}</div>
                          </div>
                        ))}
                      </div>
                      <div className="form-text text-secondary small">
                        Used for dashboard warnings and Telegram alerts. Leave blank to keep defaults.
                      </div>
                    </div>
                    <div className="d-flex gap-2">
                      <button className="btn btn-light text-dark" type="submit">
                        Save Telegram Settings
                      </button>
                      <button
                        className="btn btn-outline-light"
                        type="button"
                        onClick={() => void handleTelegramCommand('stats')}
                      >
                        Test /stats
                      </button>
                      <button
                        className="btn btn-outline-danger"
                        type="button"
                        onClick={() => void handleTelegramCommand('warn')}
                      >
                        Test /warn
                      </button>
                    </div>
                    {telegramStatus && <div className="text-secondary small">{telegramStatus}</div>}
                  </form>
                )}
              </div>
            </div>
          </section>
        )}
        {activeSection === 'administration' && (
          <section id="administration" className="d-flex flex-column gap-3">
            <div className="d-flex justify-content-between align-items-center flex-wrap gap-2">
              <div>
                <h5 className="mb-0">Administration</h5>
                <p className="text-secondary small mb-0">Handle credentials and host-level controls.</p>
              </div>
            </div>
            <div className="card bg-dark border border-secondary">
              <div className="card-header border-secondary text-uppercase fw-semibold">Access control</div>
              <div className="card-body d-flex flex-column gap-3">
                <div className="d-flex flex-wrap align-items-center gap-2">
                  <span className="badge bg-secondary">Signed in as {currentUser?.username}</span>
                  <span className="badge bg-light text-dark text-uppercase">{currentUser?.role}</span>
                </div>
                <div>
                  <h6 className="text-uppercase text-secondary mb-2">Existing users</h6>
                  {userStatus && <div className="text-secondary small mb-2">{userStatus}</div>}
                  {sortedUsers.length === 0 ? (
                    <div className="text-secondary small">No users found.</div>
                  ) : (
                    <div className="d-flex flex-column gap-2">
                      {sortedUsers.map((user) => (
                        <div
                          key={user.id}
                          className="d-flex flex-wrap align-items-center gap-2 bg-secondary bg-opacity-10 px-2 py-1 rounded"
                        >
                          <span className={`badge ${user.role === 'admin' ? 'bg-danger' : 'bg-secondary'}`}>
                            {user.role}
                          </span>
                          <span className="text-light fw-semibold">{user.username}</span>
                          <button
                            type="button"
                            className="btn btn-sm btn-outline-danger"
                            onClick={() => void handleUserDelete(user)}
                            disabled={user.id === currentUser?.id}
                          >
                            Remove
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <form className="row g-3 align-items-end" onSubmit={handleUserSubmit}>
                  <div className="col-md-4">
                    <label className="form-label">Username</label>
                    <input
                      className="form-control bg-dark text-light border-secondary"
                      value={userForm.username}
                      onChange={(event) => setUserForm((prev) => ({ ...prev, username: event.target.value }))}
                      required
                      minLength={3}
                    />
                  </div>
                  <div className="col-md-4">
                    <label className="form-label">Password</label>
                    <input
                      type="password"
                      className="form-control bg-dark text-light border-secondary"
                      value={userForm.password}
                      onChange={(event) => setUserForm((prev) => ({ ...prev, password: event.target.value }))}
                      required
                      minLength={8}
                    />
                  </div>
                  <div className="col-md-2">
                    <label className="form-label">Role</label>
                    <select
                      className="form-select bg-dark text-light border-secondary"
                      value={userForm.role}
                      onChange={(event) => setUserForm((prev) => ({ ...prev, role: event.target.value as AuthRole }))}
                    >
                      <option value="admin">admin</option>
                      <option value="viewer">viewer</option>
                    </select>
                  </div>
                  <div className="col-md-2 d-grid">
                    <button className="btn btn-light text-dark" type="submit">
                      Add user
                    </button>
                  </div>
                </form>
                <div className="form-text text-secondary">
                  Create viewer accounts for read-only dashboard access. Admin users can manage settings and backends.
                </div>
              </div>
            </div>
            <div className="card bg-dark border border-secondary">
              <div className="card-header border-secondary text-uppercase fw-semibold">Server Controls</div>
              <div className="card-body">
                <>
                  <p className="small text-secondary mb-3">
                    Restart the underlying host (not just Docker). Ensure `SERVER_MONITOR_ALLOW_HOST_REBOOT=true` is set on the
                    API host and the service user is permitted to run the configured reboot command.
                  </p>
                  <div className="row g-3 align-items-end mb-2">
                    <div className="col-sm-6 col-md-4">
                      <label className="form-label">Data retention (days)</label>
                      <input
                        type="number"
                        min={1}
                        max={90}
                        className="form-control bg-dark text-light border-secondary"
                        value={retentionDays}
                        onChange={(event) =>
                          setRetentionDays(event.target.value === '' ? '' : Number(event.target.value))
                        }
                      />
                      <div className="form-text text-secondary small">Keep metrics between 1 and 90 days.</div>
                    </div>
                    <div className="col-auto d-flex align-items-end">
                      <button
                        type="button"
                        className="btn btn-light text-dark"
                        onClick={() => void handleRetentionSave()}
                        disabled={retentionDays === '' || !isAdmin}
                      >
                        Save retention
                      </button>
                    </div>
                    {retentionStatus && <div className="col-12 text-secondary small">{retentionStatus}</div>}
                  </div>
                  <div className="small text-secondary mb-2 d-flex flex-column">
                    <span>
                      DB size: <span className="text-light fw-semibold">{formatBytes(dbSizeBytes)}</span>
                    </span>
                    {dbSizeStatus && <span className="text-warning">{dbSizeStatus}</span>}
                  </div>
                  <button
                    type="button"
                    className="btn btn-outline-danger"
                    onClick={() => void handleRebootRequest()}
                    disabled={rebootPending}
                  >
                    {rebootPending ? 'Requesting restart…' : 'Restart server'}
                  </button>
                  {rebootStatus && <div className="text-secondary small mt-2">{rebootStatus}</div>}
                </>
              </div>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
