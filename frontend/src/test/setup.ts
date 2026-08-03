import '@testing-library/jest-dom/vitest';

// Node >= 22 ships an experimental global localStorage/sessionStorage whose
// methods are not usable without --localstorage-file, and it shadows jsdom's
// implementation under vitest. Replace with an in-memory Storage when broken.
function createMemoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    removeItem: (key: string) => {
      store.delete(key);
    },
    setItem: (key: string, value: string) => {
      store.set(String(key), String(value));
    },
  } as Storage;
}

for (const name of ['localStorage', 'sessionStorage'] as const) {
  const existing = (globalThis as Record<string, unknown>)[name] as Storage | undefined;
  const broken = !existing || typeof existing.setItem !== 'function' || typeof existing.clear !== 'function';
  if (broken) {
    const storage = createMemoryStorage();
    Object.defineProperty(globalThis, name, { value: storage, writable: true, configurable: true });
    if (typeof window !== 'undefined' && window !== (globalThis as unknown)) {
      Object.defineProperty(window, name, { value: storage, writable: true, configurable: true });
    }
  }
}
