/**
 * Integration test for the bug fixed in this pass: `filters` used to live inside `QueueList`,
 * which unmounts on every trip to `RequestDetail`, so a chosen filter was silently dropped the
 * moment a reviewer opened and saved an item. Nothing here is mocked except `fetch`, so the whole
 * list -> detail -> save -> list round trip runs for real.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { API_KEY_STORAGE, App, AUTHOR_STORAGE } from "./App";
import type { Item, ItemDetail, Page } from "./api/client";

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: async () => body,
  } as Response;
}

const queueItem: Item = {
  id: "REQ-1",
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
  confianza: 0.4,
  version_prompt: "v1",
  provider: "anthropic_api",
  model: "claude-haiku-4-5",
  needs_review: true,
  corrected: false,
  error: null,
  created_at: "now",
  updated_at: "now",
  possible_duplicate_of: null,
};

const detail: ItemDetail = {
  ...queueItem,
  original_classification: null,
  corrections: [],
};

function page(): Page {
  return { items: [queueItem], total: 1, limit: 20, offset: 0 };
}

beforeEach(() => {
  sessionStorage.setItem(API_KEY_STORAGE, "key-1");
  sessionStorage.setItem(AUTHOR_STORAGE, "ana");
});

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("App — filters survive the detail round trip", () => {
  it("filters_survive_detail_round_trip", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (method === "PATCH") {
        return jsonResponse(200, queueItem);
      }
      if (url.startsWith("/api/solicitudes/")) {
        return jsonResponse(200, detail);
      }
      return jsonResponse(200, page());
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    // Initial queue load, default filters.
    await screen.findByText("REQ-1");

    // Reviewer narrows the queue by categoria.
    fireEvent.change(screen.getByLabelText("Categoria"), { target: { value: "bug" } });
    await waitFor(() => {
      const lastUrl = fetchMock.mock.calls.at(-1)?.[0] as string;
      expect(lastUrl).toContain("categoria=bug");
    });

    // Open the item and confirm it (empty diff = confirmation, Spec 05 §5).
    fireEvent.click(screen.getByText("REQ-1"));
    await screen.findByText("Confirm");
    fireEvent.click(screen.getByText("Confirm"));

    // Back on the list ("Review queue" only renders there, unlike the detail view's own "REQ-1"
    // heading): the refetch must still carry the categoria filter chosen earlier.
    await screen.findByText("Review queue");
    await screen.findByText("REQ-1");
    const lastUrl = fetchMock.mock.calls.at(-1)?.[0] as string;
    expect(lastUrl).toContain("categoria=bug");
    expect(screen.getByLabelText("Categoria")).toHaveValue("bug");
  });
});
