import '@testing-library/jest-dom/vitest';

// Node 22+ exposes an experimental `localStorage` global that is `undefined`
// unless `--localstorage-file` is passed. Vitest's jsdom environment skips
// globals that already exist, so provide an in-memory Storage for tests.
class MemoryStorage implements Storage {
  private store = new Map<string, string>();

  get length(): number {
    return this.store.size;
  }

  clear(): void {
    this.store.clear();
  }

  getItem(key: string): string | null {
    return this.store.has(key) ? (this.store.get(key) as string) : null;
  }

  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null;
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }

  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
}

for (const name of ['localStorage', 'sessionStorage'] as const) {
  if (typeof window !== 'undefined' && !window[name]) {
    Object.defineProperty(window, name, { value: new MemoryStorage(), configurable: true });
  }
  if (!(globalThis as Record<string, unknown>)[name]) {
    Object.defineProperty(globalThis, name, { value: window[name], configurable: true });
  }
}
