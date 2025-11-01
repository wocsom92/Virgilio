import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

let accessToken: string | null = null;

const api = axios.create({
  baseURL: API_BASE_URL,
});

api.interceptors.request.use((config) => {
  if (accessToken) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export type AuthRole = 'admin' | 'viewer';

export interface TokenResponse {
  access_token: string;
  token_type: string;
  username: string;
  role: AuthRole;
  user_id: number;
}

export interface AuthStatus {
  needs_bootstrap: boolean;
}

export interface AuthUser {
  id: number;
  username: string;
  role: AuthRole;
}

export interface UserCreatePayload {
  username: string;
  password: string;
  role: AuthRole;
}

export async function fetchAuthStatus() {
  const { data } = await api.get<AuthStatus>('/auth/status');
  return data;
}

export async function bootstrapAdmin(username: string, password: string) {
  const { data } = await api.post<TokenResponse>('/auth/bootstrap', { username, password });
  return data;
}

export async function login(username: string, password: string) {
  const { data } = await api.post<TokenResponse>('/auth/login', { username, password });
  return data;
}

export async function fetchCurrentUser() {
  const { data } = await api.get<AuthUser>('/auth/me');
  return data;
}

export async function listUsers() {
  const { data } = await api.get<AuthUser[]>('/auth/users');
  return data;
}

export async function createUser(payload: UserCreatePayload) {
  const { data } = await api.post<AuthUser>('/auth/users', payload);
  return data;
}

export async function deleteUser(userId: number) {
  await api.delete(`/auth/users/${userId}`);
}

export interface MountedVolume {
  mount_point: string;
  total_gb?: number | null;
  used_percent?: number | null;
}

export interface MountDisplayEntry {
  path: string;
  label: string;
}

export interface MountMetricSelection {
  enabled: boolean;
  mounts: MountDisplayEntry[];
}

export type SelectedMetricValue = boolean | MountMetricSelection | string | string[] | null | undefined;

export type SelectedMetrics = Record<string, SelectedMetricValue>;

export interface CpuLoad {
  one?: number | null;
  five?: number | null;
  fifteen?: number | null;
}

export type MetricRange = 'hourly' | 'daily' | 'weekly';

export interface NetworkThroughput {
  interface: string;
  tx_bps: number | null;
  rx_bps: number | null;
}

export interface DiskTemperature {
  device: string;
  temperature_c: number | null;
}

export interface MetricSeriesPoint {
  reported_at: string;
  cpu_temperature_c?: number | null;
  ram_used_percent?: number | null;
  disk_usage_percent?: number | null;
  cpu_load?: CpuLoad | null;
  mounted_usage?: MountedVolume[] | null;
  disk_temperatures?: DiskTemperature[] | null;
  network_bps?: NetworkThroughput[] | null;
}

export interface MetricSeriesResponse {
  backend_id: number;
  range: MetricRange;
  window_offset: number;
  window_start: string;
  window_end: string;
  previous_offset_with_data?: number | null;
  next_offset_with_data?: number | null;
  points: MetricSeriesPoint[];
  reboot_markers?: string[] | null;
}

export interface MetricSnapshot {
  id?: number;
  backend_id?: number;
  reported_at: string;
  cpu_temperature_c?: number | null;
  ram_used_percent?: number | null;
  total_ram_gb?: number | null;
  disk_usage_percent?: number | null;
  mounted_usage?: MountedVolume[] | null;
  cpu_load?: CpuLoad | null;
  network_counters?: Array<{ interface: string; bytes_sent?: number | null; bytes_recv?: number | null }> | null;
  disk_temperatures?: DiskTemperature[] | null;
  backend_version?: string | null;
  os_version?: string | null;
  uptime_seconds?: number | null;
  warnings?: string[] | null;
}

export interface MonitoredBackend {
  id: number;
  name: string;
  base_url: string;
  api_token: string;
  is_active: boolean;
  display_order: number;
  poll_interval_seconds: number;
  notes?: string | null;
  selected_metrics?: SelectedMetrics | null;
  last_seen_at?: string | null;
  last_warning?: string | null;
  latest_snapshot?: MetricSnapshot | null;
}

export interface TelegramSettings {
  id: number;
  bot_token?: string | null;
  default_chat_id?: string | null;
  warn_thresholds?: WarningThresholds | null;
  is_active: boolean;
}

export interface SystemSettings {
  retention_days: number;
}

export interface WarningThresholds {
  cpu_temperature_c?: number | null;
  ram_used_percent?: number | null;
  disk_usage_percent?: number | null;
  mounted_usage_percent?: number | null;
}

export interface RebootResponse {
  status: string;
  requested_at: string;
}

export async function fetchDashboard(): Promise<MonitoredBackend[]> {
  const { data } = await api.get<MonitoredBackend[]>('/dashboard/');
  return data;
}

export async function fetchBackendVersion(): Promise<string> {
  const { data } = await api.get<{ version: string }>('/version');
  return data.version;
}

export async function listBackends(): Promise<MonitoredBackend[]> {
  const { data } = await api.get<MonitoredBackend[]>('/backends/');
  return data;
}

export interface BackendCreatePayload {
  name: string;
  base_url: string;
  api_token: string;
  is_active?: boolean;
  display_order?: number;
  poll_interval_seconds?: number;
  notes?: string;
  selected_metrics?: SelectedMetrics;
}

export async function createBackend(payload: BackendCreatePayload) {
  const { data } = await api.post<MonitoredBackend>('/backends/', payload);
  return data;
}

export async function updateBackend(id: number, payload: Partial<BackendCreatePayload>) {
  const { data } = await api.put<MonitoredBackend>(`/backends/${id}`, payload);
  return data;
}

export async function deleteBackend(id: number) {
  await api.delete(`/backends/${id}`);
}

export async function rebootBackendHost(id: number) {
  const { data } = await api.post<{ status: string }>(`/backends/${id}/reboot`);
  return data;
}

export async function refreshBackend(id: number) {
  const { data } = await api.post<{ snapshot: MetricSnapshot }>(`/backends/${id}/refresh`);
  return data.snapshot;
}

export async function fetchBackendMounts(id: number) {
  const { data } = await api.get<string[]>(`/backends/${id}/mounts`);
  return data;
}

export async function fetchDatabaseSize() {
  const { data } = await api.get<{ size_bytes: number }>('/system/db-size');
  return data.size_bytes;
}

export async function fetchMetricSeries(id: number, range: MetricRange, offset = 0) {
  const { data } = await api.get<MetricSeriesResponse>(`/dashboard/${id}/series`, {
    params: { range_name: range, offset },
  });
  return data;
}

export async function getTelegramSettings() {
  const { data } = await api.get<TelegramSettings>('/telegram/settings');
  return data;
}

export async function updateTelegramSettings(payload: Partial<TelegramSettings>) {
  const { data } = await api.put<TelegramSettings>('/telegram/settings', payload);
  return data;
}

export async function sendTelegramStats() {
  const { data } = await api.post<{ status: string; message: string }>('/telegram/send/stats');
  return data;
}

export async function sendTelegramWarnings() {
  const { data } = await api.post<{ status: string; message: string }>('/telegram/send/warn');
  return data;
}

export async function fetchSystemSettings() {
  const { data } = await api.get<SystemSettings>('/system/retention');
  return data;
}

export async function updateSystemSettings(payload: SystemSettings) {
  const { data } = await api.put<SystemSettings>('/system/retention', payload);
  return data;
}

export async function requestReboot(reason?: string) {
  const { data } = await api.post<RebootResponse>('/system/reboot', { reason });
  return data;
}

export function backendApiBaseUrl() {
  return API_BASE_URL;
}
