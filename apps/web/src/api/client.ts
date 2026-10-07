/** Thin fetch wrapper: base URL from VITE_API_URL, bearer token, readable errors, and
 * detection of a sleeping free-tier backend (slow first response or network failure). */

export const API_URL = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');
const SLOW_RESPONSE_MS = 5000;

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// --- "server is waking up" signal (tiny external store for useSyncExternalStore) ----------
let waking = false;
const listeners = new Set<() => void>();

export const wakeStore = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  get: () => waking,
  set(value: boolean) {
    if (waking !== value) {
      waking = value;
      listeners.forEach((listener) => listener());
    }
  },
};

// --- auth token ---------------------------------------------------------------------------
let token: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setAuth(newToken: string | null, unauthorized?: () => void) {
  token = newToken;
  if (unauthorized) onUnauthorized = unauthorized;
}

export function authHeaders(): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail)) return 'Проверьте введённые данные';
  } catch {
    // not JSON
  }
  return `Ошибка сервера (${response.status})`;
}

export async function api<T>(
  path: string,
  options: { method?: string; json?: unknown; form?: FormData } = {},
): Promise<T> {
  const headers: Record<string, string> = { ...authHeaders() };
  let body: BodyInit | undefined;
  if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.json);
  } else if (options.form) {
    body = options.form;
  }
  const slowTimer = setTimeout(() => wakeStore.set(true), SLOW_RESPONSE_MS);
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { method: options.method ?? 'GET', headers, body });
  } catch {
    wakeStore.set(true); // network error: backend asleep or unreachable
    throw new ApiError(0, 'Сервер недоступен. Возможно, он просыпается — подождите немного.');
  } finally {
    clearTimeout(slowTimer);
  }
  wakeStore.set(false);
  if (response.status === 401 && token) onUnauthorized?.();
  if (!response.ok) throw new ApiError(response.status, await errorMessage(response));
  return (response.status === 204 ? undefined : await response.json()) as T;
}
