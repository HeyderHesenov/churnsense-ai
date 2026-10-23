/**
 * The API, as component tests see it: MSW answers each request in-process.
 * Every test starts from the default handlers (the shell's own two requests);
 * a test adds what its page reads with ``server.use(answer(...))``, and any
 * request nobody answers fails the test (see ``setup.ts``).
 *
 * ``answer`` is typed by the API's own schema: a body that does not match
 * what the path returns, or a path the API does not have, does not compile.
 */
import type { JsonBodyType } from 'msw';
import { http, HttpResponse } from 'msw/http';
import { setupServer } from 'msw/node';
import type { Schemas } from '../api/client';
import type { paths } from '../api/schema';
import { health, modelInfo } from './fixtures';

type Method = 'get' | 'post';
type ApiPath = keyof paths;

/** What ``method path`` returns on success, according to the schema. */
type Success<P extends ApiPath, M extends Method> = paths[P][M] extends {
  responses: { 200: { content: { 'application/json': infer Body } } };
}
  ? Body
  : never;

/** "/api/customers/{customer_id}" in MSW's syntax, "/api/customers/:customer_id". */
function route(path: ApiPath): string {
  return path.replace(/\{(\w+)\}/g, ':$1');
}

/** ``method path`` answered with ``body``. */
export function answer<P extends ApiPath, M extends Method>(
  method: M,
  path: P,
  body: Success<P, M>,
) {
  return http[method](route(path), () => HttpResponse.json(body as JsonBodyType));
}

/** ``method path`` refused with ``status``, in the API's error shape. */
export function refuse(method: Method, path: ApiPath, status: number, error: string) {
  const body: Schemas['ErrorResponse'] = { error, detail: 'Refused by a test.', problems: [] };
  return http[method](route(path), () => HttpResponse.json(body, { status }));
}

export const server = setupServer(
  answer('get', '/api/health', health()),
  answer('get', '/api/model-info', modelInfo()),
);

/** The URLs of the API requests made from now on, in order. */
export function recordRequests(): URL[] {
  const seen: URL[] = [];
  server.events.on('request:start', ({ request }) => {
    seen.push(new URL(request.url));
  });
  return seen;
}
