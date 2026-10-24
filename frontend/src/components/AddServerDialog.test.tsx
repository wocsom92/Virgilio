import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { MonitoredBackend, SelectedMetrics } from '../api/client';
import { createBackend, testMonitorConnection } from '../api/client';
import { AddServerDialog } from './AddServerDialog';

vi.mock('../api/client', () => ({
  createBackend: vi.fn(),
  testMonitorConnection: vi.fn(),
}));

const metricOptions = [
  { key: 'cpu_temperature_c', label: 'CPU temperature' },
  { key: 'ram_used_percent', label: 'RAM usage' },
  { key: 'mounted_usage', label: 'Mounted volumes' },
] as const;

const createDefaultSelectedMetrics = (): SelectedMetrics => ({
  cpu_temperature_c: true,
  ram_used_percent: true,
  mounted_usage: { enabled: true, mounts: [{ path: '/', label: '/' }] },
});

const savedBackend: MonitoredBackend = {
  id: 7,
  name: 'Living room Pi',
  base_url: 'http://192.168.1.20:29000',
  is_active: true,
  display_order: 2,
  poll_interval_seconds: 60,
};

function renderDialog(overrides: Partial<Parameters<typeof AddServerDialog>[0]> = {}) {
  const props = {
    open: true,
    onClose: vi.fn(),
    onSaved: vi.fn(),
    onOpenDashboard: vi.fn(),
    existingNames: ['Office NAS'],
    nextDisplayOrder: 2,
    metricOptions,
    createDefaultSelectedMetrics,
    mountedUsageKey: 'mounted_usage',
    ...overrides,
  };
  render(<AddServerDialog {...props} />);
  return props;
}

describe('AddServerDialog', () => {
  beforeEach(() => {
    vi.mocked(createBackend).mockReset();
    vi.mocked(testMonitorConnection).mockReset();
  });

  it('shows the agent install command with the generated token and carries the token to the connect step', async () => {
    const user = userEvent.setup();
    renderDialog();

    const tokenInput = screen.getByLabelText('Agent token') as HTMLInputElement;
    expect(tokenInput.value.length).toBeGreaterThanOrEqual(10);
    const command = screen.getByTestId('install-command').textContent ?? '';
    expect(command).toContain(`MONITOR_API_TOKEN=${tokenInput.value}`);
    expect(command).toContain('MONITOR_PORT=29000');
    expect(command).toContain('docker compose -f docker-compose.monitor.yml up -d');

    await user.click(screen.getByRole('button', { name: 'Next: connect' }));

    expect(screen.getByLabelText('Agent address')).toBeInTheDocument();
    expect((screen.getByLabelText('Agent token') as HTMLInputElement).value).toBe(tokenInput.value);
  });

  it('walks through connect, test and save, then offers the dashboard', async () => {
    const user = userEvent.setup();
    vi.mocked(testMonitorConnection).mockResolvedValue({
      ok: true,
      base_url: 'http://192.168.1.20:29000',
      latency_ms: 42,
      hostname: 'raspi5',
      monitor_version: '9.1.0',
      cpu_temperature_c: 47.25,
      ram_used_percent: 31,
      uptime_seconds: 90000,
    });
    vi.mocked(createBackend).mockResolvedValue(savedBackend);
    const props = renderDialog();

    await user.click(screen.getByRole('button', { name: 'Agent already running' }));

    await user.type(screen.getByLabelText('Name'), 'Living room Pi');
    const urlInput = screen.getByLabelText('Agent address');
    await user.type(urlInput, '192.168.1.20:29000/');
    await user.tab();
    expect((urlInput as HTMLInputElement).value).toBe('http://192.168.1.20:29000');
    await user.type(screen.getByLabelText('Agent token'), 'super-secret-token');

    await user.click(screen.getByTestId('test-connection'));

    await screen.findByTestId('probe-ok');
    expect(testMonitorConnection).toHaveBeenCalledWith({
      base_url: 'http://192.168.1.20:29000',
      api_token: 'super-secret-token',
      backend_id: undefined,
    });
    const details = screen.getByTestId('probe-details');
    expect(within(details).getByText('raspi5')).toBeInTheDocument();
    expect(within(details).getByText('9.1.0')).toBeInTheDocument();
    expect(within(details).getByText('47.3 °C')).toBeInTheDocument();
    expect(within(details).getByText('1d 1h')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Next: options' }));
    expect(screen.getByText(/Connected · raspi5/)).toBeInTheDocument();
    expect((screen.getByLabelText('Poll every') as HTMLInputElement).value).toBe('60');

    await user.click(screen.getByRole('checkbox', { name: 'RAM usage' }));
    await user.click(screen.getByRole('button', { name: 'Add server' }));

    await screen.findByTestId('add-server-done');
    expect(createBackend).toHaveBeenCalledWith({
      name: 'Living room Pi',
      base_url: 'http://192.168.1.20:29000',
      api_token: 'super-secret-token',
      is_active: true,
      display_order: 2,
      poll_interval_seconds: 60,
      notes: undefined,
      selected_metrics: {
        cpu_temperature_c: true,
        ram_used_percent: false,
        mounted_usage: { enabled: true, mounts: [{ path: '/', label: '/' }] },
      },
    });
    expect(props.onSaved).toHaveBeenCalledWith(savedBackend);
    expect(screen.getByText('Living room Pi is now monitored')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Open dashboard' }));
    expect(props.onOpenDashboard).toHaveBeenCalled();
  });

  it('explains a refused connection and shows the install command', async () => {
    const user = userEvent.setup();
    vi.mocked(testMonitorConnection).mockResolvedValue({
      ok: false,
      base_url: 'http://pi.local:29000',
      error_kind: 'refused',
      error: '[Errno 61] Connection refused',
      hint: 'The host answered but nothing is listening on that port.',
    });
    renderDialog();

    await user.click(screen.getByRole('button', { name: 'Agent already running' }));
    await user.type(screen.getByLabelText('Name'), 'pi');
    await user.type(screen.getByLabelText('Agent address'), 'http://pi.local:29000');
    await user.type(screen.getByLabelText('Agent token'), 'another-secret-token');
    await user.click(screen.getByTestId('test-connection'));

    const error = await screen.findByTestId('probe-error');
    expect(within(error).getByText('Connection refused')).toBeInTheDocument();
    expect(within(error).getByText(/nothing is listening/)).toBeInTheDocument();
    expect(within(error).getByText(/Agent not installed yet/)).toBeInTheDocument();
    expect(within(error).getByText(/MONITOR_API_TOKEN=another-secret-token MONITOR_PORT=29000/)).toBeInTheDocument();

    // Changing the address invalidates the stale result.
    await user.clear(screen.getByLabelText('Agent address'));
    await user.type(screen.getByLabelText('Agent address'), 'http://pi2.local:29000');
    await waitFor(() => expect(screen.queryByTestId('probe-error')).not.toBeInTheDocument());

    // Saving is still possible, but the options step says the connection is not verified.
    await user.click(screen.getByRole('button', { name: 'Next: options' }));
    expect(screen.getByText('Connection not verified')).toBeInTheDocument();
  });

  it('validates the connect step before moving on', async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole('button', { name: 'Agent already running' }));
    await user.click(screen.getByRole('button', { name: 'Next: options' }));

    expect(screen.getByText('Give the server a name.')).toBeInTheDocument();
    expect(screen.getByText('Enter the agent address.')).toBeInTheDocument();
    expect(screen.getByText('Enter the agent token.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Poll every')).not.toBeInTheDocument();

    await user.type(screen.getByLabelText('Name'), 'office nas');
    await user.type(screen.getByLabelText('Agent address'), 'not a url');
    await user.type(screen.getByLabelText('Agent token'), 'short');
    await user.click(screen.getByRole('button', { name: 'Next: options' }));

    expect(screen.getByText('A server with this name already exists. Pick a different name.')).toBeInTheDocument();
    expect(screen.getByText(/does not look like a URL/)).toBeInTheDocument();
    expect(screen.getByText('The token must be at least 10 characters long.')).toBeInTheDocument();
    expect(screen.getByTestId('test-connection')).toBeDisabled();
  });

  it('surfaces a save error without leaving the options step', async () => {
    const user = userEvent.setup();
    vi.mocked(createBackend).mockRejectedValue(new Error('boom'));
    renderDialog();

    await user.click(screen.getByRole('button', { name: 'Agent already running' }));
    await user.type(screen.getByLabelText('Name'), 'pi');
    await user.type(screen.getByLabelText('Agent address'), 'http://pi.local:29000');
    await user.type(screen.getByLabelText('Agent token'), 'another-secret-token');
    await user.click(screen.getByRole('button', { name: 'Next: options' }));
    await user.click(screen.getByRole('button', { name: 'Add server' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be saved|boom/i);
    expect(screen.getByLabelText('Poll every')).toBeInTheDocument();
  });
});
