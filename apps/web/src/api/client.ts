/**
 * `fetch` wrapper for the Pitz Pulse API (Spec 05 §3). Base `/api` (nginx/dev-proxy strip the
 * prefix, D22); every call sends `X-API-Key`; any non-2xx response or network failure becomes an
 * `ApiError` carrying the API's `{error, detail, fields?, kind?}` error body.
 */
import type { components, operations } from "./schema";

export type Item = components["schemas"]["Item"];
export type ItemDetail = components["schemas"]["ItemDetail"];
export type Page = components["schemas"]["Page"];
export type PatchBody = components["schemas"]["PatchBody"];
export type ErrorBody = components["schemas"]["ErrorBody"];
export type FieldError = components["schemas"]["FieldError"];
export type CorrectionOut = components["schemas"]["CorrectionOut"];
export type Categoria = components["schemas"]["Categoria"];
export type Prioridad = components["schemas"]["Prioridad"];
export type Area = components["schemas"]["Area"];
export type Idioma = components["schemas"]["Idioma"];
export type ListFilters = NonNullable<
  operations["list_requests_solicitudes_get"]["parameters"]["query"]
>;

const BASE_URL = "/api";
const NETWORK_ERROR: ErrorBody = {
  error: "network_error",
  detail: "the request could not reach the server",
};

/** Thrown for any failed API call; `status` is 0 for a network failure (no response). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: FieldError[] | null;
  readonly kind: string | null;

  constructor(status: number, body: ErrorBody) {
    super(body.detail);
    this.name = "ApiError";
    this.status = status;
    this.code = body.error;
    this.fields = body.fields ?? null;
    this.kind = body.kind ?? null;
  }
}

function buildQuery(filters?: ListFilters): string {
  if (!filters) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null) {
      params.set(key, String(value));
    }
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

async function request<T>(apiKey: string, path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: {
        "X-API-Key": apiKey,
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
  } catch {
    throw new ApiError(0, NETWORK_ERROR);
  }

  if (!response.ok) {
    let body: ErrorBody;
    try {
      body = (await response.json()) as ErrorBody;
    } catch {
      body = { error: "http_error", detail: response.statusText || "request failed" };
    }
    throw new ApiError(response.status, body);
  }

  return (await response.json()) as T;
}

export function listRequests(apiKey: string, filters?: ListFilters): Promise<Page> {
  return request<Page>(apiKey, `/solicitudes${buildQuery(filters)}`);
}

export function getRequest(apiKey: string, id: string): Promise<ItemDetail> {
  return request<ItemDetail>(apiKey, `/solicitudes/${encodeURIComponent(id)}`);
}

export function patchRequest(apiKey: string, id: string, body: PatchBody): Promise<Item> {
  return request<Item>(apiKey, `/solicitudes/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
