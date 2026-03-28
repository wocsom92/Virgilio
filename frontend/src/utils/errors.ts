import axios from 'axios';

function normalizeWhitespace(value: string): string {
  return value.replace(/\s+/g, ' ').trim();
}

function ensureSentence(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return '';
  }
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}

function extractValidationMessage(value: unknown): string | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (typeof record.msg === 'string' && record.msg.trim()) {
    return normalizeWhitespace(record.msg);
  }
  return null;
}

function extractErrorText(value: unknown): string | null {
  if (typeof value === 'string' && value.trim()) {
    return normalizeWhitespace(value);
  }
  if (Array.isArray(value)) {
    const parts = value
      .map((item) => extractValidationMessage(item) ?? extractErrorText(item))
      .filter((item): item is string => Boolean(item));
    return parts.length > 0 ? parts.join('; ') : null;
  }
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return (
      extractValidationMessage(record) ??
      extractErrorText(record.detail) ??
      extractErrorText(record.error) ??
      extractErrorText(record.message)
    );
  }
  return null;
}

function looksTechnical(value: string): boolean {
  return (
    value.includes('\n') ||
    /https?:\/\//i.test(value) ||
    /[{[\]}]/.test(value) ||
    /(axios|traceback|exception|errno|econn|stack|connecterror|readtimeout)/i.test(value)
  );
}

function normalizeKnownErrorText(rawMessage: string, statusCode?: number): string | null {
  const message = normalizeWhitespace(rawMessage);
  const lower = message.toLowerCase();

  if (statusCode === 401 && lower !== 'invalid username or password') {
    return 'Your session expired. Sign in again.';
  }
  if (statusCode === 403 || lower === 'admin privileges required') {
    return 'This action requires an admin account.';
  }
  if (lower === 'invalid username or password') {
    return 'Incorrect username or password.';
  }
  if (lower === 'could not validate credentials') {
    return 'Your session expired. Sign in again.';
  }
  if (lower === 'admin already configured') {
    return 'An admin account already exists. Sign in instead.';
  }
  if (lower === 'username is required') {
    return 'Enter a username before saving.';
  }
  if (lower === 'username already exists') {
    return 'That username is already in use.';
  }
  if (lower === 'cannot delete your own account') {
    return 'You cannot delete the account you are currently using.';
  }
  if (lower === 'at least one admin account is required') {
    return 'Create another admin account before removing this one.';
  }
  if (lower === 'backend not found' || lower === 'backend unavailable') {
    return 'That backend no longer exists. Reload the page and try again.';
  }
  if (lower === 'quick status item not found') {
    return 'That quick tile no longer exists. Reload the page and try again.';
  }
  if (lower === 'site monitor not found') {
    return 'That site monitor no longer exists. Reload the page and try again.';
  }
  if (lower === 'a site monitor with this name already exists') {
    return 'A site monitor with this name already exists.';
  }
  if (lower === 'telegram integration disabled') {
    return 'Telegram notifications are disabled. Enable Telegram first.';
  }
  if (lower === 'telegram settings incomplete') {
    return 'Telegram settings are incomplete. Add the bot token and default chat first.';
  }
  if (lower === 'no chat configured') {
    return 'No Telegram chat is configured yet. Add a default chat first.';
  }
  if (lower.startsWith('telegram api error')) {
    return 'Telegram rejected the request. Check the bot token, chat ID, and bot permissions.';
  }
  if (lower.startsWith('could not reach monitor')) {
    return 'The monitor agent did not respond. Check the backend address, API token, and monitor availability.';
  }
  if (lower.startsWith('monitor reboot failed 404')) {
    return 'The monitor agent does not expose a reboot endpoint.';
  }
  if (lower.startsWith('monitor payload missing')) {
    return 'The monitor agent responded without the expected metrics payload.';
  }
  if (lower.startsWith('unable to fetch mount points:')) {
    return 'Mount points could not be loaded from the monitor agent. Check the monitor connection and try again.';
  }
  if (lower === 'missing bearer token') {
    return 'The monitor request is missing its API token.';
  }
  if (lower === 'invalid token') {
    return 'The monitor API token was rejected. Update the token and try again.';
  }
  if (lower === 'no snapshots found') {
    return 'No metric samples are available yet for this time range.';
  }
  if (lower === 'invalid range. use hourly, daily, or weekly.') {
    return 'The selected chart range is invalid.';
  }
  if (lower === 'host reboot is disabled by configuration') {
    return 'Host reboot is disabled in the server configuration.';
  }
  if (lower.startsWith('reboot command failed')) {
    return 'The reboot command failed on the host. Check the configured reboot command and container permissions.';
  }
  if (!looksTechnical(message) && statusCode !== undefined && statusCode < 500) {
    return ensureSentence(message);
  }
  return null;
}

function fallbackMessage(fallback: string): string {
  const sentence = ensureSentence(fallback);
  return sentence || 'Request failed. Try again.';
}

export function getUserFacingErrorMessage(error: unknown, fallback: string): string {
  const axiosError = axios.isAxiosError(error) ? error : null;
  const statusCode = axiosError?.response?.status;

  if (axiosError?.code === 'ECONNABORTED') {
    return 'The request timed out. Check the connection and try again.';
  }
  if (axiosError && !axiosError.response) {
    return 'Cannot reach the API. Check whether Virgilio is online and try again.';
  }

  const rawMessage =
    extractErrorText(axiosError?.response?.data) ??
    extractErrorText(error) ??
    (error instanceof Error && error.message.trim() ? normalizeWhitespace(error.message) : null);

  const normalized = rawMessage ? normalizeKnownErrorText(rawMessage, statusCode) : null;
  if (normalized) {
    return normalized;
  }
  if (statusCode === 401) {
    return 'Your session expired. Sign in again.';
  }
  if (statusCode === 403) {
    return 'This action requires an admin account.';
  }
  return fallbackMessage(fallback);
}

export function formatNotificationDeliveryError(errorMessage?: string | null): string | null {
  if (!errorMessage || !errorMessage.trim()) {
    return null;
  }
  return (
    normalizeKnownErrorText(errorMessage) ??
    (!looksTechnical(errorMessage) ? ensureSentence(errorMessage) : null) ??
    'Telegram delivery failed. Check the Telegram settings and network access.'
  );
}
