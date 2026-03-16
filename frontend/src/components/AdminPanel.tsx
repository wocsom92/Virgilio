import { FormEvent, useEffect, useMemo, useState } from 'react';
import {
  BackendCreatePayload,
  MountDisplayEntry,
  MountMetricSelection,
  MonitoredBackend,
  SelectedMetrics,
  TelegramSettings,
  AuthUser,
  AuthRole,
  QuickStatusItem,
  QuickStatusMetricKey,
  QuickStatusTile,
  fetchBackendVersion,
  fetchDatabaseSize,
  fetchQuickStatusTiles,
  createQuickStatusItem,
  createBackend,
  createUser,
  deleteQuickStatusItem,
  deleteBackend,
  deleteUser,
  fetchBackendMounts,
  fetchAuthSessionSettings,
  fetchSystemSettings,
  getTelegramSettings,
  listQuickStatusItems,
  listBackends,
  listBackendsWithLatest,
  listUsers,
  sendTelegramStats,
  sendTelegramWarnings,
  updateAuthSessionSettings,
  updateBackend,
  updateQuickStatusItem,
  updateTelegramSettings,
  updateSystemSettings,
  requestReboot,
  rebootBackendHost,
} from '../api/client';
import { DEFAULT_BACKEND_VERSION, DEFAULT_MONITOR_VERSION, FRONTEND_VERSION } from '../constants/versions';
import { QuickStatusTileCard } from './QuickStatusTileCard';
import { formatBinaryBytes } from '../utils/formatting';
import { normalizeMountMetricSelection } from '../utils/mountMetrics';

interface AdminPanelProps {
  currentUser: AuthUser | null;
}

const MOUNTED_USAGE_KEY = 'mounted_usage' as const;
const AUTH_SESSION_MINUTES_MIN = 15;
const AUTH_SESSION_MINUTES_MAX = 60 * 24 * 30;
const QUICK_STATUS_PAGE_SIZE = 5;
const metricOptions = [
  { key: 'cpu_temperature_c', label: 'CPU temperature' },
  { key: 'ram_used_percent', label: 'RAM usage' },
  { key: 'memory_available_gb', label: 'Memory available' },
  { key: 'swap_used_percent', label: 'Swap usage' },
  { key: 'disk_usage_percent', label: 'Disk usage' },
  { key: MOUNTED_USAGE_KEY, label: 'Mounted volumes' },
  { key: 'cpu_load', label: 'CPU load' },
  { key: 'uptime_seconds', label: 'Uptime' },
] as const;

const quickStatusMetricOptions: Array<{
  key: QuickStatusMetricKey;
  label: string;
  defaultWarning: number;
  defaultCritical: number;
  helper: string;
  requiresThresholds: boolean;
  thresholdDirection: 'higher' | 'lower' | 'range';
  requiresPing: boolean;
}> = [
  {
    key: 'disk_usage_percent',
    label: 'Disk usage (%)',
    defaultWarning: 80,
    defaultCritical: 90,
    helper: 'Percent used',
    requiresThresholds: true,
    thresholdDirection: 'higher',
    requiresPing: false,
  },
  {
    key: 'ram_used_percent',
    label: 'RAM usage (%)',
    defaultWarning: 80,
    defaultCritical: 90,
    helper: 'Percent used',
    requiresThresholds: true,
    thresholdDirection: 'higher',
    requiresPing: false,
  },
  {
    key: 'memory_available_gb',
    label: 'Memory available (GiB)',
    defaultWarning: 2,
    defaultCritical: 1,
    helper: 'Available RAM in GiB (warn/critical when below thresholds)',
    requiresThresholds: true,
    thresholdDirection: 'lower',
    requiresPing: false,
  },
  {
    key: 'swap_used_percent',
    label: 'Swap usage (%)',
    defaultWarning: 50,
    defaultCritical: 80,
    helper: 'Percent used. Informational only; this tile does not generate warning or error states.',
    requiresThresholds: false,
    thresholdDirection: 'higher',
    requiresPing: false,
  },
  {
    key: 'docker_container_count',
    label: 'Running containers (#)',
    defaultWarning: 1,
    defaultCritical: 1,
    helper: 'Target running container range [X..Y]. Critical when outside this interval.',
    requiresThresholds: true,
    thresholdDirection: 'range',
    requiresPing: false,
  },
  {
    key: 'cpu_temperature_c',
    label: 'CPU temperature (°C)',
    defaultWarning: 75,
    defaultCritical: 85,
    helper: 'Degrees Celsius',
    requiresThresholds: true,
    thresholdDirection: 'higher',
    requiresPing: false,
  },
  {
    key: 'cpu_load_one',
    label: 'CPU load (1m)',
    defaultWarning: 1.0,
    defaultCritical: 2.0,
    helper: '1-minute load avg',
    requiresThresholds: true,
    thresholdDirection: 'higher',
    requiresPing: false,
  },
  {
    key: 'cpu_load_five',
    label: 'CPU load (5m)',
    defaultWarning: 1.0,
    defaultCritical: 2.0,
    helper: '5-minute load avg',
    requiresThresholds: true,
    thresholdDirection: 'higher',
    requiresPing: false,
  },
  {
    key: 'cpu_load_fifteen',
    label: 'CPU load (15m)',
    defaultWarning: 1.0,
    defaultCritical: 2.0,
    helper: '15-minute load avg',
    requiresThresholds: true,
    thresholdDirection: 'higher',
    requiresPing: false,
  },
  {
    key: 'mount_used_percent',
    label: 'Mounted volume usage (%)',
    defaultWarning: 80,
    defaultCritical: 90,
    helper: 'Percent used',
    requiresThresholds: true,
    thresholdDirection: 'higher',
    requiresPing: false,
  },
  {
    key: 'mount_available_gb',
    label: 'Mounted volume free space',
    defaultWarning: 5,
    defaultCritical: 1,
    helper: 'Free space left on a mounted volume. Displays KiB, MiB, GiB, or TiB; thresholds are configured in GiB.',
    requiresThresholds: true,
    thresholdDirection: 'lower',
    requiresPing: false,
  },
  {
    key: 'last_restart',
    label: 'Last restart (hours)',
    defaultWarning: 24,
    defaultCritical: 2,
    helper: 'Hours since last restart (green if above warning)',
    requiresThresholds: true,
    thresholdDirection: 'lower',
    requiresPing: false,
  },
  {
    key: 'ping_result',
    label: 'Ping result',
    defaultWarning: 0,
    defaultCritical: 1,
    helper: 'ICMP ping; green if host responds, red if not',
    requiresThresholds: false,
    thresholdDirection: 'higher',
    requiresPing: true,
  },
  {
    key: 'ping_delay_ms',
    label: 'Ping delay (ms)',
    defaultWarning: 150,
    defaultCritical: 300,
    helper: 'ICMP latency in milliseconds',
    requiresThresholds: true,
    thresholdDirection: 'higher',
    requiresPing: true,
  },
  {
    key: 'ssh_last_successful_login',
    label: 'SSH last successful login',
    defaultWarning: 0,
    defaultCritical: 0,
    helper: 'Elapsed time since last successful SSH login (DD:HH:MM:SS), always green',
    requiresThresholds: false,
    thresholdDirection: 'higher',
    requiresPing: false,
  },
  {
    key: 'ssh_last_unsuccessful_attempt',
    label: 'SSH last failed attempt',
    defaultWarning: 168,
    defaultCritical: 24,
    helper: 'Elapsed hours since last failed SSH login (warn under 7d, critical under 24h)',
    requiresThresholds: true,
    thresholdDirection: 'lower',
    requiresPing: false,
  },
  {
    key: 'ssh_status',
    label: 'SSH status',
    defaultWarning: 0,
    defaultCritical: 0,
    helper: 'Green: key auth enabled + root password login disabled; warn/critical otherwise',
    requiresThresholds: false,
    thresholdDirection: 'higher',
    requiresPing: false,
  },
];

const metricOptionKeys = new Set(metricOptions.map((option) => option.key));
const extraSelectedKeys = ['network_interfaces'];
const ADMIN_SECTIONS = [
  { id: 'manage-backends', label: 'Manage backends', description: 'Create, edit, and order monitored servers.' },
  {
    id: 'quick-status',
    label: 'Quick status tiles',
    description: 'Configure the overview status tiles shown on the dashboard.',
  },
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

const createEmptyTelegramSettings = (): TelegramSettings => ({
  id: 0,
  bot_token: '',
  default_chat_id: '',
  notification_batch_window_seconds: 60,
  notification_cooldown_minutes: 15,
  is_active: false,
});

const normalizeTelegramSettings = (settings: TelegramSettings): TelegramSettings => settings;

const ensureTelegramState = (state: TelegramSettings | null): TelegramSettings => {
  if (!state) {
    return createEmptyTelegramSettings();
  }
  return state;
};

type BackendFormState = Omit<BackendCreatePayload, 'display_order'> & {
  display_order: number | '';
  selected_metrics: SelectedMetrics;
};

type QuickStatusFormState = {
  backend_id: number | '';
  label: string;
  metric_key: QuickStatusMetricKey;
  mount_path: string;
  warning_threshold: number | '';
  critical_threshold: number | '';
  ping_endpoint: string;
  ping_interval_seconds: number | '';
  display_order: number | '';
};

const defaultQuickStatusThresholds = (metric: QuickStatusMetricKey) => {
  const match = quickStatusMetricOptions.find((option) => option.key === metric);
  return match
    ? { warning: match.defaultWarning, critical: match.defaultCritical }
    : { warning: 80, critical: 90 };
};

const defaultQuickStatusLabel = (metric: QuickStatusMetricKey): string =>
  quickStatusMetricOptions.find((option) => option.key === metric)?.label ?? metric;

const sortBackends = (items: MonitoredBackend[]): MonitoredBackend[] =>
  [...items].sort((a, b) => {
    if (a.display_order !== b.display_order) {
      return a.display_order - b.display_order;
    }
    return a.name.localeCompare(b.name);
  });

const sortQuickStatusItems = (items: QuickStatusItem[]): QuickStatusItem[] =>
  [...items].sort((a, b) => {
    if (a.display_order !== b.display_order) {
      return a.display_order - b.display_order;
    }
    return a.label.localeCompare(b.label);
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

const createQuickStatusForm = (): QuickStatusFormState => {
  const metricKey: QuickStatusMetricKey = 'disk_usage_percent';
  const defaults = defaultQuickStatusThresholds(metricKey);
  return {
    backend_id: '',
    label: defaultQuickStatusLabel(metricKey),
    metric_key: metricKey,
    mount_path: '',
    warning_threshold: defaults.warning,
    critical_threshold: defaults.critical,
    ping_endpoint: '',
    ping_interval_seconds: 60,
    display_order: 0,
  };
};

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

  const [backendVersion, setBackendVersion] = useState(DEFAULT_BACKEND_VERSION);
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
  const [authSessionMinutes, setAuthSessionMinutes] = useState<number | ''>('');
  const [authSessionStatus, setAuthSessionStatus] = useState<string | null>(null);
  const [mountOptions, setMountOptions] = useState<string[]>([]);
  const [loadingMountOptions, setLoadingMountOptions] = useState(false);
  const [mountOptionsError, setMountOptionsError] = useState<string | null>(null);
  const [isOrdering, setIsOrdering] = useState(false);
  const [rebootingBackendId, setRebootingBackendId] = useState<number | null>(null);
  const [activeSection, setActiveSection] = useState<SectionId>(ADMIN_SECTIONS[0].id);
  const [showNewForm, setShowNewForm] = useState(false);
  const [quickStatusItems, setQuickStatusItems] = useState<QuickStatusItem[]>([]);
  const [quickStatusTiles, setQuickStatusTiles] = useState<QuickStatusTile[]>([]);
  const [quickStatusForm, setQuickStatusForm] = useState<QuickStatusFormState>(() => createQuickStatusForm());
  const [quickStatusEditingId, setQuickStatusEditingId] = useState<number | null>(null);
  const [quickStatusStatus, setQuickStatusStatus] = useState<string | null>(null);
  const [quickStatusDraggingId, setQuickStatusDraggingId] = useState<number | null>(null);
  const [quickStatusDetailId, setQuickStatusDetailId] = useState<number | null>(null);
  const [quickStatusOrdering, setQuickStatusOrdering] = useState(false);
  const [quickStatusOrderingBackendId, setQuickStatusOrderingBackendId] = useState<number | null>(null);
  const [quickStatusSelectedMoveId, setQuickStatusSelectedMoveId] = useState<number | null>(null);
  const [quickStatusListSearch, setQuickStatusListSearch] = useState('');
  const [quickStatusCollapsedByBackend, setQuickStatusCollapsedByBackend] = useState<Record<number, boolean>>({});
  const [quickStatusPageByBackend, setQuickStatusPageByBackend] = useState<Record<number, number>>({});
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
  const getQuickStatusMetricLabel = (key: QuickStatusMetricKey) =>
    quickStatusMetricOptions.find((option) => option.key === key)?.label ?? key;
  const monitorVersionsByBackend = useMemo(
    () =>
      backends.map((backend) => ({
        backendId: backend.id,
        backendName: backend.name,
        version: backend.latest_snapshot?.monitor_version ?? backend.latest_snapshot?.backend_version ?? DEFAULT_MONITOR_VERSION,
      })),
    [backends]
  );

  useEffect(() => {
    if (!isAuthenticated) {
      setBackendVersion(DEFAULT_BACKEND_VERSION);
      return;
    }
    void (async () => {
      try {
        const version = await fetchBackendVersion();
        setBackendVersion(version || DEFAULT_BACKEND_VERSION);
      } catch {
        setBackendVersion(DEFAULT_BACKEND_VERSION);
      }
    })();
  }, [isAuthenticated]);

  useEffect(() => {
    if (!isAuthenticated) {
      setBackends([]);
      return;
    }
    void (async () => {
      try {
        const data = await listBackendsWithLatest();
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
      setQuickStatusItems([]);
      setQuickStatusTiles([]);
      return;
    }
    void (async () => {
      try {
        const [items, tiles] = await Promise.all([listQuickStatusItems(), fetchQuickStatusTiles()]);
        setQuickStatusItems(sortQuickStatusItems(items));
        setQuickStatusTiles(tiles);
      } catch (err) {
        setQuickStatusItems([]);
        setQuickStatusTiles([]);
        setQuickStatusStatus(err instanceof Error ? err.message : 'Unable to load quick status tiles');
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
      setAuthSessionMinutes('');
      setAuthSessionStatus(null);
      return;
    }
    setAuthSessionStatus('Loading session settings…');
    void (async () => {
      try {
        const settings = await fetchAuthSessionSettings();
        setAuthSessionMinutes(settings.auth_session_minutes);
        setAuthSessionStatus(null);
      } catch (err) {
        setAuthSessionMinutes('');
        setAuthSessionStatus(extractErrorMessage(err, 'Unable to load session settings'));
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
        display_order: form.display_order === '' ? 0 : Number(form.display_order),
        selected_metrics: sanitizeSelectedMetrics(form.selected_metrics),
      };
      if (editingId) {
        await updateBackend(editingId, payload);
      } else {
        await createBackend(payload);
      }
      const updated = await listBackendsWithLatest();
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

  const resetQuickStatusForm = (options: { keepStatus?: boolean } = {}) => {
    setQuickStatusForm(createQuickStatusForm());
    setQuickStatusEditingId(null);
    if (!options.keepStatus) {
      setQuickStatusStatus(null);
    }
  };

  const handleQuickStatusMetricChange = (metric: QuickStatusMetricKey) => {
    const defaults = defaultQuickStatusThresholds(metric);
    const option = quickStatusMetricOptions.find((item) => item.key === metric);
    setQuickStatusForm((prev) => {
      const previousDefaultLabel = defaultQuickStatusLabel(prev.metric_key);
      const nextDefaultLabel = defaultQuickStatusLabel(metric);
      const shouldAutoFillLabel = !prev.label.trim() || prev.label === previousDefaultLabel;
      return {
        ...prev,
        label: shouldAutoFillLabel ? nextDefaultLabel : prev.label,
        metric_key: metric,
        mount_path: '',
        warning_threshold: defaults.warning,
        critical_threshold: defaults.critical,
        ping_endpoint: option?.requiresPing ? prev.ping_endpoint : '',
        ping_interval_seconds: option?.requiresPing ? prev.ping_interval_seconds : 60,
      };
    });
  };

  const handleQuickStatusSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!isAdmin) return;

    const metricMeta = quickStatusMetricOptions.find((option) => option.key === quickStatusForm.metric_key);

    if (quickStatusForm.backend_id === '') {
      setQuickStatusStatus('Select a backend.');
      return;
    }
    if (!quickStatusForm.label.trim()) {
      setQuickStatusStatus('Enter a label.');
      return;
    }
    if (metricMeta?.requiresThresholds) {
      if (quickStatusForm.warning_threshold === '' || quickStatusForm.critical_threshold === '') {
        setQuickStatusStatus('Enter both warning and critical thresholds.');
        return;
      }
      if (metricMeta.thresholdDirection === 'range') {
        if (Number(quickStatusForm.warning_threshold) > Number(quickStatusForm.critical_threshold)) {
          setQuickStatusStatus('Minimum containers must be less than or equal to maximum containers.');
          return;
        }
      } else if (metricMeta.thresholdDirection === 'lower') {
        if (Number(quickStatusForm.warning_threshold) <= Number(quickStatusForm.critical_threshold)) {
          setQuickStatusStatus('Warning threshold must be greater than critical threshold.');
          return;
        }
      } else if (Number(quickStatusForm.warning_threshold) >= Number(quickStatusForm.critical_threshold)) {
        setQuickStatusStatus('Warning threshold must be less than critical threshold.');
        return;
      }
    }
    if (
      (quickStatusForm.metric_key === 'mount_used_percent' || quickStatusForm.metric_key === 'mount_available_gb') &&
      !quickStatusForm.mount_path.trim()
    ) {
      setQuickStatusStatus('Enter a mount path for mounted volume tiles.');
      return;
    }
    if (metricMeta?.requiresPing && !quickStatusForm.ping_endpoint.trim()) {
      setQuickStatusStatus('Enter a ping endpoint.');
      return;
    }
    if (metricMeta?.requiresPing && quickStatusForm.ping_interval_seconds === '') {
      setQuickStatusStatus('Enter a ping interval.');
      return;
    }

    const payload = {
      backend_id: Number(quickStatusForm.backend_id),
      label: quickStatusForm.label.trim(),
      metric_key: quickStatusForm.metric_key,
      mount_path:
        quickStatusForm.metric_key === 'mount_used_percent' || quickStatusForm.metric_key === 'mount_available_gb'
          ? quickStatusForm.mount_path.trim()
          : null,
      warning_threshold: Number(quickStatusForm.warning_threshold || 0),
      critical_threshold: Number(quickStatusForm.critical_threshold || 0),
      ping_endpoint: metricMeta?.requiresPing ? quickStatusForm.ping_endpoint.trim() : null,
      ping_interval_seconds: metricMeta?.requiresPing ? Number(quickStatusForm.ping_interval_seconds || 60) : 60,
      display_order: Number(quickStatusForm.display_order) || 0,
    };

    try {
      if (quickStatusEditingId) {
        const updated = await updateQuickStatusItem(quickStatusEditingId, payload);
        setQuickStatusItems((prev) =>
          sortQuickStatusItems(prev.map((item) => (item.id === updated.id ? updated : item)))
        );
        setQuickStatusStatus('Quick status tile updated.');
      } else {
        const created = await createQuickStatusItem(payload);
        setQuickStatusItems((prev) => sortQuickStatusItems([...prev, created]));
        setQuickStatusStatus('Quick status tile added.');
      }
      setQuickStatusTiles(await fetchQuickStatusTiles());
      resetQuickStatusForm({ keepStatus: true });
    } catch (err) {
      setQuickStatusStatus(extractErrorMessage(err, 'Unable to save quick status tile'));
    }
  };

  const handleQuickStatusEdit = (item: QuickStatusItem) => {
    setQuickStatusEditingId(item.id);
    setQuickStatusForm({
      backend_id: item.backend_id,
      label: item.label,
      metric_key: item.metric_key,
      mount_path: item.mount_path ?? '',
      warning_threshold: item.warning_threshold,
      critical_threshold: item.critical_threshold,
      ping_endpoint: item.ping_endpoint ?? '',
      ping_interval_seconds: item.ping_interval_seconds ?? 60,
      display_order: item.display_order,
    });
    setQuickStatusStatus(null);
    setActiveSection('quick-status');
  };

  const handleQuickStatusDelete = async (item: QuickStatusItem) => {
    if (!isAdmin) return;
    try {
      await deleteQuickStatusItem(item.id);
      setQuickStatusItems((prev) => prev.filter((entry) => entry.id !== item.id));
      setQuickStatusTiles((prev) => prev.filter((entry) => entry.id !== item.id));
      setQuickStatusStatus(`Removed ${item.label}.`);
      if (quickStatusEditingId === item.id) {
        resetQuickStatusForm();
      }
    } catch (err) {
      setQuickStatusStatus(extractErrorMessage(err, 'Unable to delete quick status tile'));
    }
  };

  const toQuickStatusPayload = (item: QuickStatusItem): Omit<QuickStatusItem, 'id'> => ({
    backend_id: item.backend_id,
    label: item.label,
    metric_key: item.metric_key,
    mount_path: item.mount_path,
    warning_threshold: item.warning_threshold,
    critical_threshold: item.critical_threshold,
    ping_endpoint: item.ping_endpoint,
    ping_interval_seconds: item.ping_interval_seconds,
    display_order: item.display_order,
  });

  const reorderQuickStatusItems = (
    items: QuickStatusItem[],
    sourceId: number,
    targetId: number
  ): QuickStatusItem[] => {
    const sourceIndex = items.findIndex((item) => item.id === sourceId);
    const targetIndex = items.findIndex((item) => item.id === targetId);
    if (sourceIndex === -1 || targetIndex === -1 || sourceIndex === targetIndex) {
      return items;
    }
    const sourceItem = items[sourceIndex];
    const backendItems = items.filter((item) => item.backend_id === sourceItem.backend_id);
    const backendSourceIndex = backendItems.findIndex((item) => item.id === sourceId);
    const backendTargetIndex = backendItems.findIndex((item) => item.id === targetId);
    if (backendSourceIndex === -1 || backendTargetIndex === -1 || backendSourceIndex === backendTargetIndex) {
      return items;
    }
    const reorderedBackendItems = [...backendItems];
    const [moved] = reorderedBackendItems.splice(backendSourceIndex, 1);
    reorderedBackendItems.splice(backendTargetIndex, 0, moved);
    const reindexedBackendItems = reorderedBackendItems.map((item, idx) => ({ ...item, display_order: idx }));
    const byId = new Map(reindexedBackendItems.map((item) => [item.id, item]));
    return items.map((item) => byId.get(item.id) ?? item);
  };

  const persistQuickStatusOrder = async (backendId: number, sourceId: number, targetId: number) => {
    if (!isAdmin || quickStatusOrdering) {
      setQuickStatusDraggingId(null);
      return;
    }
    setQuickStatusDraggingId(null);
    if (sourceId === null || sourceId === targetId) {
      return;
    }
    const sourceItem = quickStatusItems.find((item) => item.id === sourceId);
    const targetItem = quickStatusItems.find((item) => item.id === targetId);
    if (!sourceItem || !targetItem) {
      return;
    }
    if (sourceItem.backend_id !== targetItem.backend_id) {
      setQuickStatusStatus('Reordering is limited to tiles from the same backend.');
      return;
    }
    if (sourceItem.backend_id !== backendId || targetItem.backend_id !== backendId) {
      setQuickStatusStatus('Finish reordering the active server section first.');
      return;
    }
    const previous = quickStatusItems;
    const reordered = reorderQuickStatusItems(previous, sourceId, targetId);
    if (reordered === previous) {
      return;
    }
    setQuickStatusItems(reordered);
    setQuickStatusOrdering(true);
    setQuickStatusStatus('Updating order…');
    try {
      await Promise.all(
        reordered
          .filter((item) => item.backend_id === backendId)
          .map((item) => updateQuickStatusItem(item.id, toQuickStatusPayload(item)))
      );
      const [refreshed, tiles] = await Promise.all([listQuickStatusItems(), fetchQuickStatusTiles()]);
      setQuickStatusItems(sortQuickStatusItems(refreshed));
      setQuickStatusTiles(tiles);
      setQuickStatusStatus('Order updated.');
    } catch (err) {
      setQuickStatusItems(previous);
      setQuickStatusStatus(extractErrorMessage(err, 'Unable to update order'));
    } finally {
      setQuickStatusOrdering(false);
    }
  };

  const handleQuickStatusDrop = async (backendId: number, targetId: number) => {
    await persistQuickStatusOrder(backendId, quickStatusDraggingId, targetId);
  };

  const handleQuickStatusTileTap = async (backendId: number, item: QuickStatusItem, tile: QuickStatusTile) => {
    if (quickStatusOrderingBackendId === backendId) {
      if (quickStatusSelectedMoveId === null) {
        setQuickStatusSelectedMoveId(item.id);
        return;
      }
      if (quickStatusSelectedMoveId === item.id) {
        setQuickStatusSelectedMoveId(null);
        return;
      }
      const sourceId = quickStatusSelectedMoveId;
      setQuickStatusSelectedMoveId(null);
      await persistQuickStatusOrder(backendId, sourceId, item.id);
      return;
    }
    if (tile.details && tile.details.length > 0) {
      setQuickStatusDetailId((current) => (current === item.id ? null : item.id));
    }
  };

  const handleQuickStatusShift = async (
    backendId: number,
    items: Array<{ item: QuickStatusItem; tile: QuickStatusTile }>,
    index: number,
    direction: 'earlier' | 'later'
  ) => {
    const targetIndex = direction === 'earlier' ? index - 1 : index + 1;
    if (targetIndex < 0 || targetIndex >= items.length) {
      return;
    }
    await persistQuickStatusOrder(backendId, items[index].item.id, items[targetIndex].item.id);
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
                  value={form.display_order}
                  onChange={(event) => {
                    const next = event.target.value;
                    const parsed = next === '' ? '' : Number(next);
                    if (parsed === '' || Number.isFinite(parsed)) {
                      setForm({ ...form, display_order: parsed });
                    }
                  }}
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
      const refreshed = await listBackendsWithLatest();
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

  const backendNameById = useMemo(
    () => new Map(backends.map((backend) => [backend.id, backend.name])),
    [backends]
  );

  const quickStatusPreviewGroups = useMemo(() => {
    const grouped = new Map<number, QuickStatusItem[]>();
    for (const item of quickStatusItems) {
      const current = grouped.get(item.backend_id) ?? [];
      current.push(item);
      grouped.set(item.backend_id, current);
    }
    const orderedBackendIds = backends
      .map((backend) => backend.id)
      .filter((backendId) => grouped.has(backendId));
    const extraBackendIds = Array.from(grouped.keys())
      .filter((backendId) => !backendNameById.has(backendId))
      .sort((a, b) => a - b);
    return [...orderedBackendIds, ...extraBackendIds]
      .map((backendId) => {
        const backendName = backendNameById.get(backendId) ?? `Backend #${backendId}`;
        const allItems = grouped.get(backendId) ?? [];
        if (allItems.length === 0) {
          return null;
        }
        const tileById = new Map(quickStatusTiles.map((tile) => [tile.id, tile]));
        return {
          backendId,
          backendName,
          allItemsCount: allItems.length,
          filteredItemsCount: allItems.length,
          items: allItems.map((item) => ({
            item,
            tile:
              tileById.get(item.id) ??
              ({
                id: item.id,
                backend_id: item.backend_id,
                backend_display_order: 0,
                backend_name: backendName,
                label: item.label,
                metric_key: item.metric_key,
                value: null,
                display_value: '—',
                status: 'unknown',
                details: null,
              } satisfies QuickStatusTile),
          })),
        };
      })
      .filter((group): group is NonNullable<typeof group> => group !== null);
  }, [backends, backendNameById, quickStatusItems, quickStatusTiles]);

  const quickStatusListGroups = useMemo(() => {
    const search = quickStatusListSearch.trim().toLocaleLowerCase();
    const grouped = new Map<number, QuickStatusItem[]>();
    for (const item of quickStatusItems) {
      const current = grouped.get(item.backend_id) ?? [];
      current.push(item);
      grouped.set(item.backend_id, current);
    }
    const orderedBackendIds = backends
      .map((backend) => backend.id)
      .filter((backendId) => grouped.has(backendId));
    const extraBackendIds = Array.from(grouped.keys())
      .filter((backendId) => !backendNameById.has(backendId))
      .sort((a, b) => a - b);
    return [...orderedBackendIds, ...extraBackendIds]
      .map((backendId) => {
        const backendName = backendNameById.get(backendId) ?? `Backend #${backendId}`;
        const allItems = grouped.get(backendId) ?? [];
        const matchesBackend = search.length > 0 && backendName.toLocaleLowerCase().includes(search);
        const filteredItems =
          search.length === 0 || matchesBackend
            ? allItems
            : allItems.filter((item) => {
                const metricLabel =
                  quickStatusMetricOptions.find((option) => option.key === item.metric_key)?.label.toLocaleLowerCase() ??
                  item.metric_key.toLocaleLowerCase();
                const haystack = [item.label, metricLabel, item.mount_path ?? '', item.ping_endpoint ?? '']
                  .join(' ')
                  .toLocaleLowerCase();
                return haystack.includes(search);
              });
        if (filteredItems.length === 0) {
          return null;
        }
        const totalPages = Math.max(1, Math.ceil(filteredItems.length / QUICK_STATUS_PAGE_SIZE));
        const currentPage = Math.min(quickStatusPageByBackend[backendId] ?? 1, totalPages);
        const start = (currentPage - 1) * QUICK_STATUS_PAGE_SIZE;
        return {
          backendId,
          backendName,
          allItemsCount: allItems.length,
          filteredItemsCount: filteredItems.length,
          currentPage,
          totalPages,
          items: filteredItems.slice(start, start + QUICK_STATUS_PAGE_SIZE),
        };
      })
      .filter((group): group is NonNullable<typeof group> => group !== null);
  }, [backends, backendNameById, quickStatusItems, quickStatusListSearch, quickStatusPageByBackend]);

  const quickStatusMetricMeta = quickStatusMetricOptions.find(
    (option) => option.key === quickStatusForm.metric_key
  );

  const quickStatusThresholdUnit = useMemo(() => {
    if (quickStatusForm.metric_key === 'last_restart') {
      return 'hours';
    }
    if (quickStatusForm.metric_key === 'memory_available_gb' || quickStatusForm.metric_key === 'mount_available_gb') {
      return 'GiB';
    }
    if (quickStatusForm.metric_key === 'ping_delay_ms') {
      return 'ms';
    }
    if (quickStatusForm.metric_key === 'cpu_temperature_c') {
      return '°C';
    }
    if (quickStatusForm.metric_key === 'docker_container_count') {
      return 'count';
    }
    if (quickStatusForm.metric_key.endsWith('percent')) {
      return '%';
    }
    return '';
  }, [quickStatusForm.metric_key]);

  const handleTelegramSave = async (event: FormEvent) => {
    event.preventDefault();
    if (!isAdmin || !telegram) return;
    try {
      setTelegramStatus('Saving Telegram settings…');
      const updated = await updateTelegramSettings({
        bot_token: telegram.bot_token,
        default_chat_id: telegram.default_chat_id,
        notification_batch_window_seconds: telegram.notification_batch_window_seconds,
        notification_cooldown_minutes: telegram.notification_cooldown_minutes,
        is_active: telegram.is_active,
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

  const handleAuthSessionSave = async () => {
    if (!isAdmin) return;
    if (authSessionMinutes === '') {
      setAuthSessionStatus('Please enter a session length.');
      return;
    }
    try {
      setAuthSessionStatus('Saving session length…');
      const clamped = Math.min(AUTH_SESSION_MINUTES_MAX, Math.max(AUTH_SESSION_MINUTES_MIN, Number(authSessionMinutes)));
      const updated = await updateAuthSessionSettings({ auth_session_minutes: clamped });
      setAuthSessionMinutes(updated.auth_session_minutes);
      setAuthSessionStatus('Session length saved.');
    } catch (err) {
      setAuthSessionStatus(extractErrorMessage(err, 'Unable to update session length'));
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
        <section className="card bg-dark border border-secondary">
          <div className="card-body d-flex flex-wrap align-items-center gap-2">
            <span className="badge bg-secondary">Frontend v{FRONTEND_VERSION}</span>
            <span className="badge bg-secondary">Backend v{backendVersion}</span>
            {monitorVersionsByBackend.map((item) => (
              <span className="badge bg-secondary" key={item.backendId}>
                {item.backendName}: Monitor v{item.version}
              </span>
            ))}
          </div>
        </section>
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
        {activeSection === 'quick-status' && (
          <section id="quick-status" className="d-flex flex-column gap-3">
            <div className="d-flex justify-content-between align-items-center flex-wrap gap-2">
              <div>
                <h5 className="mb-0">Quick status tiles</h5>
                <p className="text-secondary small mb-0">Add fast-glance tiles to the dashboard overview.</p>
              </div>
              {!isAdmin && <span className="badge bg-warning text-dark">Admin access required to save changes</span>}
            </div>
            <div className="card bg-dark border border-secondary">
              <div className="card-header border-secondary text-uppercase fw-semibold">Overview Preview</div>
              <div className="card-body d-flex flex-column gap-3">
                {isAdmin && quickStatusItems.length > 1 && (
                  <div className="text-secondary small">
                    Use the server-level edit button to reorder tiles. Desktop supports drag and drop; on iPhone or
                    other touch devices, tap one tile and then tap its destination.
                  </div>
                )}
                {quickStatusItems.length === 0 ? (
                  <div className="text-secondary small">No quick status tiles configured yet.</div>
                ) : (
                  quickStatusPreviewGroups.map((group) => (
                    <section className="quick-status-section" key={group.backendId}>
                      <div className="quick-status-section__header">
                        <h3 className="quick-status-section__title">{group.backendName}</h3>
                        <div className="d-flex align-items-center gap-2 flex-wrap justify-content-end">
                          <span className="quick-status-section__count">
                            {group.filteredItemsCount}
                            {group.filteredItemsCount !== group.allItemsCount ? ` / ${group.allItemsCount}` : ''}{' '}
                            {group.filteredItemsCount === 1 ? 'tile' : 'tiles'}
                          </span>
                          {isAdmin && (
                            <button
                              type="button"
                              className={`btn btn-sm ${
                                quickStatusOrderingBackendId === group.backendId ? 'btn-light text-dark' : 'btn-outline-light'
                              }`}
                              onClick={() => {
                                setQuickStatusOrderingBackendId((current) =>
                                  current === group.backendId ? null : group.backendId
                                );
                                setQuickStatusSelectedMoveId(null);
                                setQuickStatusDraggingId(null);
                              }}
                              disabled={quickStatusOrdering && quickStatusOrderingBackendId !== group.backendId}
                            >
                              {quickStatusOrderingBackendId === group.backendId ? 'Done' : 'Edit order'}
                            </button>
                          )}
                        </div>
                      </div>
                      {quickStatusOrderingBackendId === group.backendId && (
                        <div className="text-secondary small">
                          {quickStatusSelectedMoveId === null
                            ? 'Reorder mode: drag a tile or tap one tile and then tap where it should move.'
                            : 'Tile selected. Tap the destination tile to move it there.'}
                        </div>
                      )}
                      <div className="quick-status-grid">
                        {group.items.map(({ item, tile }, index) => (
                          <div className="quick-status-grid__item" key={item.id}>
                            <QuickStatusTileCard
                              tile={tile}
                              dragging={quickStatusDraggingId === item.id || quickStatusSelectedMoveId === item.id}
                              draggable={isAdmin && !quickStatusOrdering && quickStatusOrderingBackendId === group.backendId}
                              onDragStart={(event) => {
                                if (!isAdmin || quickStatusOrdering || quickStatusOrderingBackendId !== group.backendId) return;
                                setQuickStatusDraggingId(item.id);
                                event.dataTransfer.effectAllowed = 'move';
                                event.dataTransfer.setData('text/plain', String(item.id));
                              }}
                              onDragOver={(event) => {
                                if (!isAdmin || quickStatusOrdering || quickStatusOrderingBackendId !== group.backendId) return;
                                event.preventDefault();
                                event.dataTransfer.dropEffect = 'move';
                              }}
                              onDragEnd={() => setQuickStatusDraggingId(null)}
                              onDrop={() => void handleQuickStatusDrop(group.backendId, item.id)}
                              onClick={() => void handleQuickStatusTileTap(group.backendId, item, tile)}
                            />
                            {quickStatusOrderingBackendId === group.backendId && (
                              <div className="quick-status-tile-controls">
                                <button
                                  type="button"
                                  className="btn btn-sm btn-outline-light"
                                  onClick={() =>
                                    void handleQuickStatusShift(group.backendId, group.items, index, 'earlier')
                                  }
                                  disabled={quickStatusOrdering || index === 0}
                                >
                                  ←
                                </button>
                                <button
                                  type="button"
                                  className="btn btn-sm btn-outline-light"
                                  onClick={() =>
                                    void handleQuickStatusShift(group.backendId, group.items, index, 'later')
                                  }
                                  disabled={quickStatusOrdering || index === group.items.length - 1}
                                >
                                  →
                                </button>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                      {(() => {
                        const selectedPreviewTile = group.items.find(({ item }) => item.id === quickStatusDetailId)?.tile;
                        if (!selectedPreviewTile?.details?.length || quickStatusOrderingBackendId === group.backendId) {
                          return null;
                        }
                        return (
                          <div className="quick-status-detail card-panel rounded-4 p-3">
                            <div className="text-uppercase card-panel__heading fw-semibold mb-2">
                              Tile details
                            </div>
                            <ul className="mb-0 small quick-status-detail__list">
                              {selectedPreviewTile.details.map((line) => (
                                <li
                                  key={`${line.severity}:${line.text}`}
                                  className={
                                    line.severity === 'critical'
                                      ? 'text-danger'
                                      : line.severity === 'warn'
                                        ? 'text-warning'
                                        : 'text-success'
                                  }
                                >
                                  <code>{line.text}</code>
                                </li>
                              ))}
                            </ul>
                          </div>
                        );
                      })()}
                    </section>
                  ))
                )}
              </div>
            </div>
            <div className="card bg-dark border border-secondary">
              <div className="card-header border-secondary text-uppercase fw-semibold">Existing Tiles</div>
              <div className="card-body d-flex flex-column gap-3">
                <div className="d-flex flex-column flex-lg-row gap-2 justify-content-between align-items-lg-center">
                  <div className="text-secondary small">
                    Search tiles by server, label, metric, mount path, or ping target.
                  </div>
                  <input
                    className="form-control bg-dark text-light border-secondary"
                    style={{ maxWidth: '360px' }}
                    value={quickStatusListSearch}
                    onChange={(event) => {
                      setQuickStatusListSearch(event.target.value);
                      setQuickStatusPageByBackend({});
                    }}
                    placeholder="Search quick status tiles"
                  />
                </div>
                {quickStatusItems.length === 0 ? (
                  <div className="text-secondary small">No quick status tiles configured yet.</div>
                ) : quickStatusListGroups.length === 0 ? (
                  <div className="text-secondary small">No quick status tiles match the current search.</div>
                ) : (
                  quickStatusListGroups.map((group) => (
                    <div key={`list-${group.backendId}`} className="rounded-3 border border-secondary p-3 d-flex flex-column gap-3">
                      <button
                        type="button"
                        className="btn btn-outline-light d-flex justify-content-between align-items-center gap-2 text-start"
                        onClick={() =>
                          setQuickStatusCollapsedByBackend((prev) => ({
                            ...prev,
                            [group.backendId]: !(prev[group.backendId] ?? true),
                          }))
                        }
                      >
                        <span className="text-uppercase small fw-semibold">{group.backendName}</span>
                        <span className="d-flex align-items-center gap-2">
                          <span className="badge bg-secondary-subtle text-secondary border border-secondary">
                            {group.filteredItemsCount}
                            {group.filteredItemsCount !== group.allItemsCount ? ` / ${group.allItemsCount}` : ''}{' '}
                            {group.filteredItemsCount === 1 ? 'tile' : 'tiles'}
                          </span>
                          <span>
                            {quickStatusListSearch.trim()
                              ? 'Hide'
                              : (quickStatusCollapsedByBackend[group.backendId] ?? true)
                                ? 'Expand'
                                : 'Collapse'}
                          </span>
                        </span>
                      </button>
                      {(quickStatusListSearch.trim() || !(quickStatusCollapsedByBackend[group.backendId] ?? true)) && (
                        <>
                          {group.items.map((item) => {
                            const meta = quickStatusMetricOptions.find((option) => option.key === item.metric_key);
                            const thresholdLabel =
                              meta?.requiresThresholds
                                ? meta.thresholdDirection === 'range'
                                  ? item.warning_threshold === item.critical_threshold
                                    ? `Target containers: ${item.warning_threshold} (critical outside target)`
                                    : `Target containers: ${item.warning_threshold}-${item.critical_threshold} (critical outside range)`
                                  : meta.thresholdDirection === 'lower'
                                    ? `Warn ≤ ${item.warning_threshold} / Critical ≤ ${item.critical_threshold}`
                                    : `Warn ${item.warning_threshold} / Critical ${item.critical_threshold}`
                                : 'Status OK/Failed';
                            return (
                              <div className="d-flex flex-column gap-2" key={item.id}>
                                <div className="card-panel rounded-3 p-3">
                                  <div className="fw-semibold">{item.label}</div>
                                  <div className="text-secondary small">{getQuickStatusMetricLabel(item.metric_key)}</div>
                                  <div className="text-secondary small">
                                    {thresholdLabel}
                                    {(item.metric_key === 'mount_used_percent' || item.metric_key === 'mount_available_gb') &&
                                    item.mount_path
                                      ? ` · ${item.mount_path}`
                                      : ''}
                                    {meta?.requiresPing
                                      ? ` · ${item.ping_endpoint ?? 'target missing'} · ${item.ping_interval_seconds ?? 60}s`
                                      : ''}
                                  </div>
                                </div>
                                <div className="d-flex gap-2">
                                  <button
                                    type="button"
                                    className="btn btn-sm btn-outline-light"
                                    onClick={() => handleQuickStatusEdit(item)}
                                    disabled={!isAdmin}
                                  >
                                    Edit
                                  </button>
                                  <button
                                    type="button"
                                    className="btn btn-sm btn-outline-danger"
                                    onClick={() => void handleQuickStatusDelete(item)}
                                    disabled={!isAdmin}
                                  >
                                    Delete
                                  </button>
                                </div>
                              </div>
                            );
                          })}
                          {group.totalPages > 1 && (
                            <div className="d-flex justify-content-between align-items-center gap-2 flex-wrap">
                              <div className="text-secondary small">
                                Page {group.currentPage} of {group.totalPages}
                              </div>
                              <div className="d-flex gap-2">
                                <button
                                  type="button"
                                  className="btn btn-sm btn-outline-light"
                                  onClick={() =>
                                    setQuickStatusPageByBackend((prev) => ({
                                      ...prev,
                                      [group.backendId]: Math.max(1, group.currentPage - 1),
                                    }))
                                  }
                                  disabled={group.currentPage <= 1}
                                >
                                  Previous
                                </button>
                                <button
                                  type="button"
                                  className="btn btn-sm btn-outline-light"
                                  onClick={() =>
                                    setQuickStatusPageByBackend((prev) => ({
                                      ...prev,
                                      [group.backendId]: Math.min(group.totalPages, group.currentPage + 1),
                                    }))
                                  }
                                  disabled={group.currentPage >= group.totalPages}
                                >
                                  Next
                                </button>
                              </div>
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
            <div className="card bg-dark border border-secondary">
              <div className="card-header border-secondary text-uppercase fw-semibold">
                {quickStatusEditingId ? 'Edit quick status tile' : 'Add quick status tile'}
              </div>
              <div className="card-body">
                <form className="d-flex flex-column gap-3" onSubmit={handleQuickStatusSubmit}>
                  <div className="row g-3">
                    <div className="col-md-6">
                      <label className="form-label">Backend</label>
                      <select
                        className="form-select bg-dark text-light border-secondary"
                        value={quickStatusForm.backend_id}
                        onChange={(event) =>
                          setQuickStatusForm((prev) => ({
                            ...prev,
                            backend_id: event.target.value === '' ? '' : Number(event.target.value),
                          }))
                        }
                        required
                      >
                        <option value="">Select backend...</option>
                        {backends.map((backend) => (
                          <option key={backend.id} value={backend.id}>
                            {backend.name}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="col-md-6">
                      <label className="form-label">Metric</label>
                      <select
                        className="form-select bg-dark text-light border-secondary"
                        value={quickStatusForm.metric_key}
                        onChange={(event) =>
                          handleQuickStatusMetricChange(event.target.value as QuickStatusMetricKey)
                        }
                      >
                        {quickStatusMetricOptions.map((option) => (
                          <option key={option.key} value={option.key}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                      <div className="form-text text-secondary small">
                        {quickStatusMetricOptions.find((option) => option.key === quickStatusForm.metric_key)?.helper}
                      </div>
                    </div>
                    {(quickStatusForm.metric_key === 'mount_used_percent' ||
                      quickStatusForm.metric_key === 'mount_available_gb') && (
                      <div className="col-12">
                        <label className="form-label">Mount path</label>
                        <input
                          className="form-control bg-dark text-light border-secondary"
                          value={quickStatusForm.mount_path}
                          onChange={(event) =>
                            setQuickStatusForm((prev) => ({ ...prev, mount_path: event.target.value }))
                          }
                          placeholder="/mnt/storage"
                          required
                        />
                      </div>
                    )}
                    {quickStatusMetricMeta?.requiresPing && (
                      <>
                        <div className="col-12 col-lg-8">
                          <label className="form-label">Ping target</label>
                          <input
                            className="form-control bg-dark text-light border-secondary"
                            value={quickStatusForm.ping_endpoint}
                            onChange={(event) =>
                              setQuickStatusForm((prev) => ({ ...prev, ping_endpoint: event.target.value }))
                            }
                            placeholder="8.8.8.8 or server.local"
                            required
                          />
                          <div className="form-text text-secondary small">
                            ICMP ping target (host or IP). Requires CAP_NET_RAW or root on the monitor host.
                          </div>
                        </div>
                        <div className="col-12 col-lg-4">
                          <label className="form-label">Ping interval (sec)</label>
                          <input
                            type="number"
                            className="form-control bg-dark text-light border-secondary"
                            value={quickStatusForm.ping_interval_seconds}
                            onChange={(event) =>
                              setQuickStatusForm((prev) => ({
                                ...prev,
                                ping_interval_seconds: event.target.value === '' ? '' : Number(event.target.value),
                              }))
                            }
                            min={5}
                            required
                          />
                          <div className="form-text text-secondary small">
                            Minimum 5 seconds.
                          </div>
                        </div>
                      </>
                    )}
                    <div className="col-md-6">
                      <label className="form-label">Label</label>
                      <input
                        className="form-control bg-dark text-light border-secondary"
                        value={quickStatusForm.label}
                        onChange={(event) =>
                          setQuickStatusForm((prev) => ({ ...prev, label: event.target.value }))
                        }
                        placeholder="HDD"
                        required
                      />
                    </div>
                    {quickStatusMetricMeta?.requiresThresholds && (
                      <>
                        <div className="col-md-3">
                          <label className="form-label">
                            {quickStatusMetricMeta.thresholdDirection === 'range'
                              ? `Minimum containers${quickStatusThresholdUnit ? ` (${quickStatusThresholdUnit})` : ''}`
                              : `Warning threshold${quickStatusThresholdUnit ? ` (${quickStatusThresholdUnit})` : ''}`}
                          </label>
                          <input
                            type="number"
                            className="form-control bg-dark text-light border-secondary"
                            value={quickStatusForm.warning_threshold}
                            onChange={(event) =>
                              setQuickStatusForm((prev) => ({
                                ...prev,
                                warning_threshold: event.target.value === '' ? '' : Number(event.target.value),
                              }))
                            }
                            min={0}
                            step="any"
                            required
                          />
                        </div>
                        <div className="col-md-3">
                          <label className="form-label">
                            {quickStatusMetricMeta.thresholdDirection === 'range'
                              ? `Maximum containers${quickStatusThresholdUnit ? ` (${quickStatusThresholdUnit})` : ''}`
                              : `Critical threshold${quickStatusThresholdUnit ? ` (${quickStatusThresholdUnit})` : ''}`}
                          </label>
                          <input
                            type="number"
                            className="form-control bg-dark text-light border-secondary"
                            value={quickStatusForm.critical_threshold}
                            onChange={(event) =>
                              setQuickStatusForm((prev) => ({
                                ...prev,
                                critical_threshold: event.target.value === '' ? '' : Number(event.target.value),
                              }))
                            }
                            min={0}
                            step="any"
                            required
                          />
                        </div>
                      </>
                    )}
                    <div className="col-md-3">
                      <label className="form-label">Display order</label>
                      <input
                        type="number"
                        className="form-control bg-dark text-light border-secondary"
                        value={quickStatusForm.display_order}
                        onChange={(event) => {
                          const next = event.target.value;
                          const parsed = next === '' ? '' : Number(next);
                          if (parsed === '' || Number.isFinite(parsed)) {
                            setQuickStatusForm((prev) => ({
                              ...prev,
                              display_order: parsed,
                            }));
                          }
                        }}
                        min={0}
                      />
                    </div>
                  </div>
                  <div className="d-flex gap-2 flex-wrap">
                    <button className="btn btn-light text-dark" type="submit" disabled={!isAdmin}>
                      {quickStatusEditingId ? 'Save tile' : 'Add tile'}
                    </button>
                    <button className="btn btn-outline-light" type="button" onClick={resetQuickStatusForm}>
                      Reset
                    </button>
                    {quickStatusStatus && <span className="text-secondary align-self-center">{quickStatusStatus}</span>}
                  </div>
                </form>
              </div>
            </div>
          </section>
        )}
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
                      <label className="form-label">Alert batch window (seconds)</label>
                      <input
                        type="number"
                        min={0}
                        max={3600}
                        className="form-control bg-dark text-light border-secondary"
                        value={telegram?.notification_batch_window_seconds ?? 60}
                        onChange={(event) =>
                          setTelegram((prev) => {
                            const base = ensureTelegramState(prev);
                            return {
                              ...base,
                              notification_batch_window_seconds: Math.max(0, Number(event.target.value || 0)),
                            };
                          })
                        }
                        required
                      />
                      <div className="form-text text-secondary small">
                        When a new warning or error appears, wait this long before sending so related issues can be
                        grouped into one message.
                      </div>
                    </div>
                    <div>
                      <label className="form-label">Notification cooldown (minutes)</label>
                      <input
                        type="number"
                        min={0}
                        max={10080}
                        className="form-control bg-dark text-light border-secondary"
                        value={telegram?.notification_cooldown_minutes ?? 15}
                        onChange={(event) =>
                          setTelegram((prev) => {
                            const base = ensureTelegramState(prev);
                            return {
                              ...base,
                              notification_cooldown_minutes: Math.max(0, Number(event.target.value || 0)),
                            };
                          })
                        }
                        required
                      />
                      <div className="form-text text-secondary small">
                        After a message is sent, automatic warning, error, and recovery messages wait at least this
                        long before the next delivery. Cooldown starts from send time, not from when the issue first
                        appeared.
                      </div>
                    </div>
                    <div className="form-text text-secondary small">
                      Warning and error rules are configured in <strong>Quick status tiles</strong>. Telegram alerts
                      are sent automatically when a tile changes to warning or error, and a follow-up notification is
                      sent when that state clears.
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
                  <div className="row g-3 align-items-end mb-2">
                    <div className="col-sm-6 col-md-4">
                      <label className="form-label">Session length (minutes)</label>
                      <input
                        type="number"
                        min={AUTH_SESSION_MINUTES_MIN}
                        max={AUTH_SESSION_MINUTES_MAX}
                        className="form-control bg-dark text-light border-secondary"
                        value={authSessionMinutes}
                        onChange={(event) =>
                          setAuthSessionMinutes(event.target.value === '' ? '' : Number(event.target.value))
                        }
                      />
                      <div className="form-text text-secondary small">
                        15 minutes to 30 days (1440 = 24 hours).
                      </div>
                    </div>
                    <div className="col-auto d-flex align-items-end">
                      <button
                        type="button"
                        className="btn btn-light text-dark"
                        onClick={() => void handleAuthSessionSave()}
                        disabled={authSessionMinutes === '' || !isAdmin}
                      >
                        Save session
                      </button>
                    </div>
                    {authSessionStatus && <div className="col-12 text-secondary small">{authSessionStatus}</div>}
                  </div>
                  <div className="small text-secondary mb-2 d-flex flex-column">
                    <span>
                      DB size: <span className="text-light fw-semibold">{formatBinaryBytes(dbSizeBytes)}</span>
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
