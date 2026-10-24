/** Helpers for the admin "Add server" flow: token generation, URL cleanup and the agent install command. */

export const DEFAULT_MONITOR_PORT = 29000;
export const MIN_TOKEN_LENGTH = 10;
export const MIN_POLL_INTERVAL_SECONDS = 30;
export const DEFAULT_POLL_INTERVAL_SECONDS = 60;

export function generateMonitorToken(bytes = 24): string {
  const alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  const values = new Uint8Array(bytes);
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(values);
  } else {
    for (let i = 0; i < values.length; i += 1) {
      values[i] = Math.floor(Math.random() * 256);
    }
  }
  let out = '';
  for (const value of values) {
    out += alphabet[value % alphabet.length];
  }
  return out;
}

/** Adds `http://` when the scheme is missing and strips a trailing slash or `/metrics`. */
export function normalizeMonitorUrl(raw: string): string {
  let value = raw.trim();
  if (!value) {
    return '';
  }
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(value)) {
    value = `http://${value}`;
  }
  value = value.replace(/\/+$/, '');
  value = value.replace(/\/metrics(\/latest)?$/i, '');
  return value;
}

export function validateMonitorUrl(raw: string): string | null {
  const value = raw.trim();
  if (!value) {
    return 'Enter the agent address.';
  }
  let parsed: URL;
  try {
    parsed = new URL(normalizeMonitorUrl(value));
  } catch {
    return 'That does not look like a URL. Use the form http://host:29000.';
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return 'The address must start with http:// or https://.';
  }
  if (!parsed.hostname) {
    return 'The address needs a hostname or IP, e.g. http://192.168.1.20:29000.';
  }
  if (parsed.search || parsed.hash) {
    return 'Leave out query strings; use just the host and port.';
  }
  return null;
}

export function validateServerName(raw: string, existingNames: readonly string[] = []): string | null {
  const value = raw.trim();
  if (!value) {
    return 'Give the server a name.';
  }
  if (value.length > 100) {
    return 'Keep the name under 100 characters.';
  }
  const lower = value.toLocaleLowerCase();
  if (existingNames.some((name) => name.trim().toLocaleLowerCase() === lower)) {
    return 'A server with this name already exists. Pick a different name.';
  }
  return null;
}

export function validateToken(raw: string, { required = true }: { required?: boolean } = {}): string | null {
  const value = raw.trim();
  if (!value) {
    return required ? 'Enter the agent token.' : null;
  }
  if (value.length < MIN_TOKEN_LENGTH) {
    return `The token must be at least ${MIN_TOKEN_LENGTH} characters long.`;
  }
  if (/\s/.test(value)) {
    return 'The token must not contain spaces.';
  }
  return null;
}

export function portFromUrl(raw: string): number {
  try {
    const parsed = new URL(normalizeMonitorUrl(raw));
    if (parsed.port) {
      return Number(parsed.port);
    }
    return parsed.protocol === 'https:' ? 443 : 80;
  } catch {
    return DEFAULT_MONITOR_PORT;
  }
}

/** The one-liner that starts the agent on a server from a checkout of this repository. */
export function buildAgentInstallCommand(token: string, port: number = DEFAULT_MONITOR_PORT): string {
  const safeToken = token.trim() || '<your-token>';
  return [
    `MONITOR_API_TOKEN=${safeToken} MONITOR_PORT=${port} MONITOR_HOST_ROOT_SOURCE=/ \\`,
    '  docker compose -f docker-compose.monitor.yml up -d --build monitor',
  ].join('\n');
}

export function formatUptime(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return null;
  }
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) {
    return `${days}d ${hours}h`;
  }
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  return `${minutes}m`;
}

export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the legacy path
  }
  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', 'true');
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}
