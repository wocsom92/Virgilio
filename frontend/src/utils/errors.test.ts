import { describe, expect, it } from 'vitest';

import { formatNotificationDeliveryError, getUserFacingErrorMessage } from './errors';

describe('getUserFacingErrorMessage', () => {
  it('normalizes invalid login errors', () => {
    expect(
      getUserFacingErrorMessage(
        {
          isAxiosError: true,
          response: { status: 401, data: { detail: 'Invalid username or password' } },
        },
        'Fallback'
      )
    ).toBe('Incorrect username or password.');
  });

  it('normalizes network failures', () => {
    expect(
      getUserFacingErrorMessage(
        {
          isAxiosError: true,
          message: 'Network Error',
        },
        'Fallback'
      )
    ).toBe('Cannot reach the API. Check whether Virgilio is online and try again.');
  });

  it('normalizes monitor connectivity failures', () => {
    expect(
      getUserFacingErrorMessage(
        {
          isAxiosError: true,
          response: { status: 502, data: { detail: 'Could not reach monitor: [Errno 111] Connection refused' } },
        },
        'Fallback'
      )
    ).toBe('The monitor agent did not respond. Check the backend address, API token, and monitor availability.');
  });

  it('falls back to the provided message for unknown server errors', () => {
    expect(
      getUserFacingErrorMessage(
        {
          isAxiosError: true,
          response: { status: 500, data: { detail: 'unexpected low-level failure {trace}' } },
        },
        'Could not load dashboard data. Check the API connection and try again.'
      )
    ).toBe('Could not load dashboard data. Check the API connection and try again.');
  });
});

describe('formatNotificationDeliveryError', () => {
  it('normalizes Telegram API failures', () => {
    expect(
      formatNotificationDeliveryError("Telegram API error: {'ok': false, 'error_code': 400, 'description': 'Bad Request'}")
    ).toBe('Telegram rejected the request. Check the bot token, chat ID, and bot permissions.');
  });

  it('keeps simple user-safe messages', () => {
    expect(formatNotificationDeliveryError('Bot is muted')).toBe('Bot is muted.');
  });
});
