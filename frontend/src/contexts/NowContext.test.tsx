import { act, render, renderHook, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { NowProvider, useNow } from './NowContext';

function Clock() {
  const now = useNow();
  return <span data-testid="now">{now}</span>;
}

describe('NowContext', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('ticks all consumers from one shared timer', () => {
    const setInterval = vi.spyOn(window, 'setInterval');
    render(
      <NowProvider tickMs={1000}>
        <Clock />
        <Clock />
      </NowProvider>
    );
    const start = Date.parse('2026-01-01T00:00:00Z');
    expect(screen.getAllByTestId('now').map((node) => Number(node.textContent))).toEqual([start, start]);
    expect(setInterval).toHaveBeenCalledTimes(1);

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getAllByTestId('now').map((node) => Number(node.textContent))).toEqual([start + 1000, start + 1000]);
  });

  it('falls back to Date.now() without a provider', () => {
    const { result } = renderHook(() => useNow());
    expect(result.current).toBe(Date.parse('2026-01-01T00:00:00Z'));
  });
});
