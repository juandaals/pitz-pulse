import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getRequest, listRequests, patchRequest } from "./client";
import type { ErrorBody, Item, ItemDetail, Page, PatchBody } from "./client";

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: async () => body,
  } as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("client — requests", () => {
  it("listRequests sends X-API-Key and calls the /api base with query filters", async () => {
    const page: Page = { items: [], total: 0, limit: 20, offset: 0 };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, page));
    vi.stubGlobal("fetch", fetchMock);

    const result = await listRequests("secret-key", {
      status: "classified",
      needs_review: true,
      limit: 20,
      offset: 0,
    });

    expect(result).toEqual(page);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/solicitudes?status=classified&needs_review=true&limit=20&offset=0");
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("secret-key");
  });

  it("listRequests with no filters calls the bare /api/solicitudes path", async () => {
    const page: Page = { items: [], total: 0, limit: 20, offset: 0 };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, page));
    vi.stubGlobal("fetch", fetchMock);

    await listRequests("secret-key");

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe("/api/solicitudes");
  });

  it("getRequest calls /api/solicitudes/{id} with the API key", async () => {
    const item: ItemDetail = {
      id: "abc",
      message: "hola",
      source_area: null,
      status: "classified",
      categoria: "bug",
      prioridad: "alta",
      area_sugerida: "backend",
      idioma: "es",
      resumen: "resumen",
      requiere_info: false,
      pregunta_seguimiento: null,
      confianza: 0.9,
      version_prompt: "v1",
      provider: "mock",
      model: "mock",
      needs_review: false,
      corrected: false,
      error: null,
      created_at: "now",
      updated_at: "now",
      original_classification: null,
      corrections: [],
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, item));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getRequest("secret-key", "abc");

    expect(result).toEqual(item);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/solicitudes/abc");
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("secret-key");
  });

  it("patchRequest sends a PATCH with a JSON body and Content-Type", async () => {
    const item: Item = {
      id: "abc",
      message: "hola",
      source_area: null,
      status: "classified",
      categoria: "bug",
      prioridad: "alta",
      area_sugerida: "backend",
      idioma: "es",
      resumen: "resumen",
      requiere_info: false,
      pregunta_seguimiento: null,
      confianza: 0.9,
      version_prompt: "v1",
      provider: "mock",
      model: "mock",
      needs_review: false,
      corrected: true,
      error: null,
      created_at: "now",
      updated_at: "now",
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, item));
    vi.stubGlobal("fetch", fetchMock);
    const body: PatchBody = { author: "ana" };

    const result = await patchRequest("secret-key", "abc", body);

    expect(result).toEqual(item);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/solicitudes/abc");
    expect(init.method).toBe("PATCH");
    expect(init.body).toBe(JSON.stringify(body));
    const headers = init.headers as Record<string, string>;
    expect(headers["X-API-Key"]).toBe("secret-key");
    expect(headers["Content-Type"]).toBe("application/json");
  });
});

describe("client — error mapping", () => {
  it.each([401, 404, 409])("maps a %i response to an ApiError", async (status) => {
    const body: ErrorBody = { error: `code_${status}`, detail: `detail ${status}` };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(status, body)));

    await expect(getRequest("secret-key", "abc")).rejects.toMatchObject({
      status,
      code: `code_${status}`,
      message: `detail ${status}`,
      fields: null,
    });
  });

  it("maps a 422 response with fields to an ApiError carrying those fields", async () => {
    const body: ErrorBody = {
      error: "validation_error",
      detail: "request validation failed",
      fields: [{ loc: ["body", "resumen"], msg: "resumen must have 1-20 words, has 21" }],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(422, body)));

    let error: unknown;
    try {
      await patchRequest("secret-key", "abc", { author: "ana" });
    } catch (caught) {
      error = caught;
    }

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(422);
    expect((error as ApiError).fields).toEqual(body.fields);
  });

  it("maps a fetch rejection (network failure) to an ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );

    let error: unknown;
    try {
      await listRequests("secret-key");
    } catch (caught) {
      error = caught;
    }

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
    expect((error as ApiError).code).toBe("network_error");
  });
});
