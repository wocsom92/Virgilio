import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

const NowContext = createContext<number | null>(null);

const DEFAULT_TICK_MS = 60_000;

/** One shared clock for "updated N minutes ago" labels instead of a timer per tile. */
export function NowProvider({ children, tickMs = DEFAULT_TICK_MS }: { children: ReactNode; tickMs?: number }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let timer: number | null = null;
    const start = () => {
      if (timer !== null) {
        return;
      }
      setNow(Date.now());
      timer = window.setInterval(() => setNow(Date.now()), tickMs);
    };
    const stop = () => {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    };
    const handleVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        start();
      }
    };
    if (!document.hidden) {
      start();
    }
    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [tickMs]);

  return <NowContext.Provider value={now}>{children}</NowContext.Provider>;
}

/** Current time in ms, refreshed by the nearest NowProvider (falls back to Date.now()). */
export function useNow(): number {
  const value = useContext(NowContext);
  return value ?? Date.now();
}
