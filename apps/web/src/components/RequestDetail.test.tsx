import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { RequestDetail } from "./RequestDetail";
import type { ItemDetail } from "../api/client";

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: async () => body,
  } as Response;
}

const correctedDetail: ItemDetail = {
  id: "REQ-1",
  message: "mensaje original",
  source_area: null,
  status: "classified",
  categoria: "bug",
  prioridad: "alta",
  area_sugerida: "backend",
  idioma: "es",
  resumen: "resumen actual",
  requiere_info: false,
  pregunta_seguimiento: null,
  confianza: 0.42,
  version_prompt: "v1",
  provider: "anthropic_api",
  model: "claude-haiku-4-5",
  needs_review: false,
  corrected: true,
  error: null,
  created_at: "2026-09-27T10:00:00Z",
  updated_at: "2026-09-27T11:00:00Z",
  original_classification: {
    categoria: "consulta",
    prioridad: "baja",
    area_sugerida: "frontend",
    idioma: "es",
    resumen: "resumen original",
    requiere_info: false,
    pregunta_seguimiento: null,
  },
  corrections: [
    {
      previous_values: { categoria: "consulta" },
      new_values: { categoria: "bug" },
      author: "ana",
      reason: "wrong category",
      created_at: "2026-09-27T11:00:00Z",
    },
  ],
};

const failedDetail: ItemDetail = {
  id: "REQ-2",
  message: "mensaje que fallo",
  source_area: null,
  status: "failed",
  categoria: null,
  prioridad: null,
  area_sugerida: null,
  idioma: null,
  resumen: null,
  requiere_info: null,
  pregunta_seguimiento: null,
  confianza: null,
  version_prompt: null,
  provider: "anthropic_api",
  model: "claude-haiku-4-5",
  needs_review: false,
  corrected: false,
  error: "classification_failed: llm_rejected",
  created_at: "2026-09-27T10:00:00Z",
  updated_at: "2026-09-27T10:00:05Z",
  original_classification: null,
  corrections: [],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RequestDetail", () => {
  it("shows current vs original classification values and the correction history", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, correctedDetail)));

    render(
      <RequestDetail
        apiKey="key"
        author="ana"
        requestId="REQ-1"
        onBack={vi.fn()}
        onSaved={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    );

    await screen.findByText(/wrong category/);
    const table = screen.getByRole("table");
    expect(within(table).getByText("resumen actual")).toBeInTheDocument();
    expect(within(table).getByText("resumen original")).toBeInTheDocument();
  });

  it("shows a read-only view with the error for a failed request, offering no correction form", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, failedDetail)));

    render(
      <RequestDetail
        apiKey="key"
        author="ana"
        requestId="REQ-2"
        onBack={vi.fn()}
        onSaved={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    );

    await screen.findByText(/classification_failed/);
    expect(screen.queryByText("Confirm")).not.toBeInTheDocument();
    expect(screen.queryByText("Save")).not.toBeInTheDocument();
  });

  it("calls onUnauthorized on a 401 response", async () => {
    const onUnauthorized = vi.fn();
    const body = { error: "unauthorized", detail: "missing or invalid API key" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, body)));

    render(
      <RequestDetail
        apiKey="bad-key"
        author="ana"
        requestId="REQ-1"
        onBack={vi.fn()}
        onSaved={vi.fn()}
        onUnauthorized={onUnauthorized}
      />,
    );

    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
  });
});
