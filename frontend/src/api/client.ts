/**
 * The only way the app talks to the server: same origin, JSON, typed by the
 * schema generated from the API's own OpenAPI document.
 *
 * Errors carry a status and the API's stable ``error`` code. Their text is
 * never shown to a person: a page shows its own message and a retry. The one
 * exception is a 422's ``problems`` list, which describes the caller's own
 * input (a CSV they uploaded) and is rendered as plain text.
 */
import type { components } from './schema';

export type Schemas = components['schemas'];

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly problems: readonly string[] = [],
    readonly retryAfter: number | null = null,
    /** How many problems the refusal listed beyond those kept. */
    readonly problemsOmitted = 0,
  ) {
    super(`API error ${status} (${code})`);
    this.name = 'ApiError';
  }

  /** Worth retrying: the server or the network failed, not the request. */
  get transient(): boolean {
    return this.status === 0 || this.status >= 500;
  }
}

type Query = Record<string, string | number | boolean | readonly string[] | null | undefined>;

function url(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) value.forEach((v: string) => params.append(key, v));
    else params.set(key, String(value));
  }
  const text = params.toString();
  return `/api${path}${text ? `?${text}` : ''}`;
}

/** The most problems a refusal keeps; a page says how many more there were. */
const MAX_PROBLEMS = 50;

/** The body of a refusal, if it has the documented shape. */
async function refusal(response: Response): Promise<ApiError> {
  let code = 'http_error';
  let listed: string[] = [];
  try {
    const body: unknown = await response.json();
    if (body && typeof body === 'object' && 'error' in body && typeof body.error === 'string') {
      code = body.error;
      if (response.status === 422 && 'problems' in body && Array.isArray(body.problems)) {
        listed = body.problems.filter((p): p is string => typeof p === 'string');
      }
    }
  } catch {
    // Not the documented shape: the status alone tells the page what to do.
  }
  const retry = Number(response.headers.get('retry-after'));
  const problems = listed.slice(0, MAX_PROBLEMS);
  return new ApiError(
    response.status,
    code,
    problems,
    Number.isFinite(retry) && retry > 0 ? retry : null,
    listed.length - problems.length,
  );
}

interface RequestOptions {
  method?: 'GET' | 'POST';
  query?: Query;
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal | undefined;
}

async function send(path: string, options: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  let body: BodyInit | undefined;
  if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.json);
  } else if (options.form) {
    body = options.form;
  }
  let response: Response;
  try {
    response = await fetch(url(path, options.query), {
      method: options.method ?? 'GET',
      headers,
      body: body ?? null,
      signal: options.signal ?? null,
      credentials: 'same-origin',
      cache: 'no-store',
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, 'network_error');
  }
  if (!response.ok) throw await refusal(response);
  return response;
}

/** A JSON response from the API. */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(path, options);
  return (await response.json()) as T;
}

/** The address of a CSV export, for a plain same-origin download link. */
export function exportUrl(query: Query): string {
  return url('/customers/export', query);
}
