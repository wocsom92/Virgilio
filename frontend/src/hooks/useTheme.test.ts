import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { THEME_STORAGE_KEY, useTheme } from './useTheme';

function mockMatchMedia(dark: boolean) {
  const listeners = new Set<() => void>();
  const media = {
    matches: dark,
    media: '(prefers-color-scheme: dark)',
    addEventListener: (_: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
  };
  Object.defineProperty(window, 'matchMedia', { value: () => media, configurable: true });
  return {
    flip: (nextDark: boolean) => {
      media.matches = nextDark;
      listeners.forEach((listener) => listener());
    },
  };
}

describe('useTheme', () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.dataset.theme = '';
    document.head.innerHTML = '<meta name="theme-color" content="">';
  });

  it('follows the system preference by default and reacts to changes', () => {
    const media = mockMatchMedia(true);
    const { result } = renderHook(() => useTheme());
    expect(result.current.preference).toBe('system');
    expect(document.documentElement.dataset.theme).toBe('dark');

    act(() => media.flip(false));
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.content).toBe('#ffffff');
  });

  it('persists an explicit choice and toggles between light and dark', () => {
    mockMatchMedia(false);
    const { result } = renderHook(() => useTheme());
    act(() => result.current.toggle());
    expect(result.current.preference).toBe('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(JSON.parse(window.localStorage.getItem(THEME_STORAGE_KEY) ?? '""')).toBe('dark');

    act(() => result.current.toggle());
    expect(result.current.preference).toBe('light');
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});
