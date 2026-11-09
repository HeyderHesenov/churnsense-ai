/**
 * The only thing this app keeps in the browser: UI preferences. Never a
 * customer, a filter or a figure. Storage may be unavailable (private mode,
 * blocked site data), so every access falls back silently.
 */
const PREFIX = 'churnsense:';

export function readPreference(key: string): string | null {
  try {
    return window.localStorage.getItem(PREFIX + key);
  } catch {
    return null;
  }
}

export function writePreference(key: string, value: string): void {
  try {
    window.localStorage.setItem(PREFIX + key, value);
  } catch {
    // A preference that cannot be saved is simply not remembered.
  }
}
