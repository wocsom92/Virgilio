import { useCallback, useEffect, useMemo } from 'react';

import { useLocalStorage } from './useLocalStorage';

export type ThemePreference = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'virgilio-theme';
const THEME_COLORS: Record<ResolvedTheme, string> = { light: '#ffffff', dark: '#111a2b' };

function systemTheme(): ResolvedTheme {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return 'light';
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function applyTheme(theme: ResolvedTheme) {
  document.documentElement.dataset.theme = theme;
  document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]').forEach((meta) => {
    meta.content = THEME_COLORS[theme];
  });
}

/** Theme preference persisted per browser; `system` follows prefers-color-scheme live. */
export function useTheme() {
  const [preference, setPreference] = useLocalStorage<ThemePreference>(THEME_STORAGE_KEY, 'system');
  const resolved: ResolvedTheme = preference === 'system' ? systemTheme() : preference;

  useEffect(() => {
    applyTheme(preference === 'system' ? systemTheme() : preference);
    if (preference !== 'system' || typeof window.matchMedia !== 'function') {
      return;
    }
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const handleChange = () => applyTheme(media.matches ? 'dark' : 'light');
    media.addEventListener('change', handleChange);
    return () => media.removeEventListener('change', handleChange);
  }, [preference]);

  const toggle = useCallback(() => {
    setPreference((current) => {
      const currentResolved = current === 'system' ? systemTheme() : current;
      return currentResolved === 'dark' ? 'light' : 'dark';
    });
  }, [setPreference]);

  return useMemo(
    () => ({ preference, resolved, setPreference, toggle }),
    [preference, resolved, setPreference, toggle]
  );
}
