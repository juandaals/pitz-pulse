import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CorrectionForm } from "./CorrectionForm";
import type { CorrectionFormInitial } from "./CorrectionForm";
import type { ErrorBody, Item } from "../api/client";

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: async () => body,
  } as Response;
}

const initial: CorrectionFormInitial = {
  categoria: "bug",
  prioridad: "alta",
  area_sugerida: "backend",
  idioma: "es",
  resumen: "resumen valido de la solicitud",
  requiere_info: false,
  pregunta_seguimiento: null,
};

const savedItem: Item = {
  id: "REQ-1",
  message: "hola",
  source_area: null,
  status: "classified",
  categoria: "bug",
  prioridad: "alta",
  area_sugerida: "backend",
  idioma: "es",
  resumen: "resumen valido de la solicitud",
  requiere_info: false,
  pregunta_seguimiento: null,
  confianza: 0.9,
  version_prompt: "v1",
  provider: "anthropic_api",
  model: "claude-haiku-4-5",
  needs_review: false,
  corrected: true,
  error: null,
  created_at: "now",
  updated_at: "now",
};

function renderForm(overrides: Partial<CorrectionFormInitial> = {}) {
  const onSuccess = vi.fn();
  const onUnauthorized = vi.fn();
  render(
    <CorrectionForm
      apiKey="secret-key"
      requestId="REQ-1"
      author="ana"
      initial={{ ...initial, ...overrides }}
      onSuccess={onSuccess}
      onUnauthorized={onUnauthorized}
    />,
  );
  return { onSuccess, onUnauthorized };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CorrectionForm", () => {
  it("Confirm sends only author, even though the form still holds the current values", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, savedItem));
    vi.stubGlobal("fetch", fetchMock);
    const { onSuccess } = renderForm();

    fireEvent.click(screen.getByText("Confirm"));

    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(savedItem));
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ author: "ana" });
  });

  it("Save sends only the fields that changed", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, savedItem));
    vi.stubGlobal("fetch", fetchMock);
    renderForm();

    fireEvent.change(screen.getByLabelText("Categoria"), { target: { value: "datos" } });
    fireEvent.change(screen.getByLabelText("Resumen"), {
      target: { value: "nuevo resumen corregido" },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      author: "ana",
      categoria: "datos",
      resumen: "nuevo resumen corregido",
    });
  });

  it("turning requiere_info off sends pregunta_seguimiento: null in the diff", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, savedItem));
    vi.stubGlobal("fetch", fetchMock);
    renderForm({ requiere_info: true, pregunta_seguimiento: "cual es tu duda?" });

    fireEvent.click(screen.getByLabelText("Requiere info"));
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      author: "ana",
      requiere_info: false,
      pregunta_seguimiento: null,
    });
  });

  it("renders the 422 field errors from the response", async () => {
    const body: ErrorBody = {
      error: "validation_error",
      detail: "request validation failed",
      fields: [{ loc: ["body"], msg: "resumen must have at most 200 characters" }],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(422, body)));
    renderForm();

    fireEvent.click(screen.getByText("Save"));

    await screen.findByText("resumen must have at most 200 characters");
  });

  it("shows the server's message on a 409 conflict", async () => {
    const body: ErrorBody = {
      error: "not_classified",
      detail: "only classified requests can be corrected",
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(409, body)));
    renderForm();

    fireEvent.click(screen.getByText("Confirm"));

    await screen.findByText("only classified requests can be corrected");
  });

  it("calls onUnauthorized on a 401 response and does not call onSuccess", async () => {
    const body: ErrorBody = { error: "unauthorized", detail: "missing or invalid API key" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, body)));
    const { onSuccess, onUnauthorized } = renderForm();

    fireEvent.click(screen.getByText("Confirm"));

    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
    expect(onSuccess).not.toHaveBeenCalled();
  });
});
