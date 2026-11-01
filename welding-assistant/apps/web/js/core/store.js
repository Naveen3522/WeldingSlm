// Browser storage that never throws (it can be blocked or full). Only per-browser settings live here; History is kept on the server.

export function load(key, fallback = null) {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value;
  } catch (error) {
    return fallback;
  }
}

export function save(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (error) {
    // Ignored: the setting just will not be remembered.
  }
}
