import { useCallback, useEffect, useRef, useState } from 'react';

import { isCancelledError } from '../api/client';

export interface UsePollingOptions {
  /** Delay between successful runs. `null` fetches once and never re-polls. */
  intervalMs: number | null;
  /** When false the hook is idle (no requests, timers cleared). Defaults to true. */
  enabled?: boolean;
  /** Pause while the tab is hidden and resume when it becomes visible. Defaults to true. */
  pauseWhenHidden?: boolean;
  /** Cap for the exponential error back-off. Defaults to 8× the interval. */
  maxBackoffMs?: number;
  /** Re-run immediately (and reset state) whenever one of these changes. */
  deps?: ReadonlyArray<unknown>;
}

export interface UsePollingResult<T> {
  data: T | null;
  error: unknown;
  /** True until the first result for the current deps has settled. */
  loading: boolean;
  /** Run the fetcher now; resolves when it settles. */
  refresh: () => Promise<void>;
  lastSuccessAt: number | null;
}

const DEFAULT_BACKOFF_FACTOR = 8;

/**
 * One timer per data source: a `setTimeout` chain (runs never overlap), one `AbortController`
 * per run, paused while the document is hidden, exponential back-off after failures.
 */
export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  options: UsePollingOptions
): UsePollingResult<T> {
  const { intervalMs, enabled = true, pauseWhenHidden = true, maxBackoffMs, deps = [] } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [lastSuccessAt, setLastSuccessAt] = useState<number | null>(null);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const runRef = useRef<(() => Promise<void>) | null>(null);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      runRef.current = null;
      return;
    }

    let disposed = false;
    let timer: number | null = null;
    let controller: AbortController | null = null;
    let failures = 0;
    let lastSuccess: number | null = null;
    let inFlight: Promise<void> | null = null;
    const backoffCap = maxBackoffMs ?? (intervalMs ?? 60_000) * DEFAULT_BACKOFF_FACTOR;

    const clearTimer = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };

    const hidden = () => pauseWhenHidden && typeof document !== 'undefined' && document.hidden;

    const schedule = (delayMs: number) => {
      clearTimer();
      if (disposed || intervalMs === null || hidden()) {
        return;
      }
      timer = window.setTimeout(() => {
        timer = null;
        void run();
      }, Math.max(0, delayMs));
    };

    const run = (): Promise<void> => {
      if (disposed) {
        return Promise.resolve();
      }
      if (inFlight) {
        return inFlight;
      }
      controller?.abort();
      const current = new AbortController();
      controller = current;
      inFlight = (async () => {
        try {
          const result = await fetcherRef.current(current.signal);
          if (disposed || current.signal.aborted) {
            return;
          }
          failures = 0;
          lastSuccess = Date.now();
          setData(result);
          setError(null);
          setLastSuccessAt(lastSuccess);
          setLoading(false);
          schedule(intervalMs ?? 0);
        } catch (err) {
          if (disposed || current.signal.aborted || isCancelledError(err)) {
            return;
          }
          failures += 1;
          setError(err);
          setLoading(false);
          const base = intervalMs ?? 60_000;
          schedule(Math.min(base * 2 ** failures, backoffCap));
        } finally {
          inFlight = null;
        }
      })();
      return inFlight;
    };

    const handleVisibility = () => {
      if (hidden()) {
        clearTimer();
        controller?.abort();
        inFlight = null;
        return;
      }
      if (intervalMs === null) {
        return;
      }
      const elapsed = lastSuccess === null ? Number.POSITIVE_INFINITY : Date.now() - lastSuccess;
      if (elapsed >= intervalMs) {
        void run();
      } else {
        schedule(intervalMs - elapsed);
      }
    };

    setLoading(true);
    setError(null);
    runRef.current = run;
    if (pauseWhenHidden && typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', handleVisibility);
      // Some embedded browsers show a page without a visibilitychange event; focus is a cheap
      // second chance to resume (handleVisibility is a no-op when nothing is due).
      window.addEventListener('focus', handleVisibility);
    }
    if (!hidden()) {
      void run();
    }

    return () => {
      disposed = true;
      clearTimer();
      controller?.abort();
      runRef.current = null;
      if (pauseWhenHidden && typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', handleVisibility);
        window.removeEventListener('focus', handleVisibility);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, intervalMs, pauseWhenHidden, maxBackoffMs, ...deps]);

  const refresh = useCallback(async () => {
    if (runRef.current) {
      await runRef.current();
    }
  }, []);

  return { data, error, loading, refresh, lastSuccessAt };
}
