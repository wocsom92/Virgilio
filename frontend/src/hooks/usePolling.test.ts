import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', () => ({
  isCancelledError: (error: unknown) => error instanceof Error && error.name === 'CanceledError',
}));

import { usePolling } from './usePolling';

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { value: hidden, configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
}

describe('usePolling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setHidden(false);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('runs immediately and then on the interval', async () => {
    const fetcher = vi.fn().mockResolvedValue('ok');
    const { result } = renderHook(() => usePolling(fetcher, { intervalMs: 1000 }));

    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.data).toBe('ok');
    expect(result.current.loading).toBe(false);

    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('backs off exponentially after failures and resets after success', async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new Error('boom'))
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValue('fine');
    const { result } = renderHook(() => usePolling(fetcher, { intervalMs: 1000 }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.error).toBeInstanceOf(Error);

    // First retry after 2 s (1000 * 2^1).
    await act(async () => {
      vi.advanceTimersByTime(1999);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(1);
      await Promise.resolve();
    });
    expect(fetcher).toHaveBeenCalledTimes(2);

    // Second retry after 4 s.
    await act(async () => {
      vi.advanceTimersByTime(4000);
      await Promise.resolve();
    });
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(result.current.data).toBe('fine');
    expect(result.current.error).toBeNull();

    // Back to the normal cadence.
    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it('pauses while the document is hidden and resumes when visible', async () => {
    const fetcher = vi.fn().mockResolvedValue(1);
    renderHook(() => usePolling(fetcher, { intervalMs: 1000 }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetcher).toHaveBeenCalledTimes(1);

    act(() => setHidden(true));
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => {
      setHidden(false);
      await Promise.resolve();
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('aborts the in-flight request on unmount and ignores cancellations', async () => {
    let seenSignal: AbortSignal | null = null;
    const fetcher = vi.fn((signal: AbortSignal) => {
      seenSignal = signal;
      return new Promise<string>((_, reject) => {
        signal.addEventListener('abort', () => {
          const error = new Error('canceled');
          error.name = 'CanceledError';
          reject(error);
        });
      });
    });
    const { result, unmount } = renderHook(() => usePolling(fetcher, { intervalMs: 1000 }));
    expect(seenSignal).not.toBeNull();
    unmount();
    expect((seenSignal as unknown as AbortSignal).aborted).toBe(true);
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.error).toBeNull();
  });

  it('does nothing while disabled and fetches once with a null interval', async () => {
    const fetcher = vi.fn().mockResolvedValue('once');
    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) => usePolling(fetcher, { intervalMs: null, enabled }),
      { initialProps: { enabled: false } }
    );
    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);

    rerender({ enabled: true });
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
