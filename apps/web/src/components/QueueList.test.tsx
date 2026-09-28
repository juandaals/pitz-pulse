import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { EMPTY_FILTERS, QueueList } from "./QueueList";
import type { QueueListProps } from "./QueueList";
import type { Item, Page } from "../api/client";

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: async () => body,
  } as Response;
}

function makeItem(id: string): Item {
  return {
    id,
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
  };
}

function renderQueueList(overrides: Partial<QueueListProps> = {}) {
  const props: QueueListProps = {
    apiKey: "secret-key",
    filters: EMPTY_FILTERS,
    onFiltersChange: vi.fn(),
    offset: 0,
    onOffsetChange: vi.fn(),
    refreshToken: 0,
    onSelect: vi.fn(),
    onUnauthorized: vi.fn(),
    ...overrides,
  };
  return { ...render(<QueueList {...props} />), props };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("QueueList", () => {
  it("requests the default filters (status=classified, needs_review=true) with no extra filters", async () => {
    const page: Page = { items: [], total: 0, limit: 20, offset: 0 };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, page));
    vi.stubGlobal("fetch", fetchMock);

    renderQueueList();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe("/api/solicitudes?status=classified&needs_review=true&limit=20&offset=0");
  });

  it("requests with the filters passed in by its parent, since it is controlled", async () => {
    const page: Page = { items: [], total: 0, limit: 20, offset: 0 };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, page));
    vi.stubGlobal("fetch", fetchMock);

    renderQueueList({ filters: { categoria: "bug", prioridad: "", area_sugerida: "" } });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toContain("categoria=bug");
  });

  it("shows an explicit empty state when the queue has no items", async () => {
    const page: Page = { items: [], total: 0, limit: 20, offset: 0 };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, page)));

    renderQueueList();

    await screen.findByText("No items in the review queue.");
  });

  it("clamps the offset when the queue shrinks below the current page, then refetches", async () => {
    const onOffsetChange = vi.fn();
    const shrunkPage: Page = { items: [], total: 15, limit: 20, offset: 20 };
    const clampedPage: Page = { items: [], total: 15, limit: 20, offset: 0 };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, shrunkPage))
      .mockResolvedValueOnce(jsonResponse(200, clampedPage));
    vi.stubGlobal("fetch", fetchMock);

    const { rerender, props } = renderQueueList({ offset: 20, onOffsetChange });

    await waitFor(() => expect(onOffsetChange).toHaveBeenCalledWith(0));

    // Stand in for `App` applying the clamp and bumping refreshToken after a save.
    rerender(<QueueList {...props} offset={0} refreshToken={1} />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [secondUrl] = fetchMock.mock.calls[1] as [string];
    expect(secondUrl).toContain("offset=0");
  });

  it("does not flash the empty state while a clamp refetch is pending", async () => {
    const onOffsetChange = vi.fn();
    const shrunkPage: Page = { items: [], total: 15, limit: 20, offset: 20 };
    const clampedPage: Page = { items: [makeItem("REQ-1")], total: 15, limit: 20, offset: 0 };
    let resolveSecond: (value: Response) => void = () => {};
    const secondResponse = new Promise<Response>((resolve) => {
      resolveSecond = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, shrunkPage))
      .mockImplementationOnce(() => secondResponse);
    vi.stubGlobal("fetch", fetchMock);

    const { rerender, props } = renderQueueList({ offset: 20, onOffsetChange });

    await waitFor(() => expect(onOffsetChange).toHaveBeenCalledWith(0));
    expect(screen.queryByText("No items in the review queue.")).not.toBeInTheDocument();

    rerender(<QueueList {...props} offset={0} />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    // The second (correct) request is still pending; the shrunk-page result must never render.
    expect(screen.queryByText("No items in the review queue.")).not.toBeInTheDocument();

    resolveSecond(jsonResponse(200, clampedPage));
    await screen.findByText("REQ-1");
  });

  it("ignores a stale response from an earlier request that resolves after a newer one", async () => {
    const staleItem = makeItem("STALE-1");
    const freshItem = makeItem("FRESH-1");
    let resolveFirst: (value: Response) => void = () => {};
    const firstResponse = new Promise<Response>((resolve) => {
      resolveFirst = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => firstResponse)
      .mockResolvedValueOnce(
        jsonResponse(200, { items: [freshItem], total: 1, limit: 20, offset: 0 } satisfies Page),
      );
    vi.stubGlobal("fetch", fetchMock);

    const { rerender, props } = renderQueueList();

    // A second load starts (e.g. `App` bumping refreshToken after a save) before the first resolves.
    rerender(<QueueList {...props} refreshToken={1} />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    resolveFirst(
      jsonResponse(200, { items: [staleItem], total: 1, limit: 20, offset: 0 } satisfies Page),
    );

    await screen.findByText("FRESH-1");
    expect(screen.queryByText("STALE-1")).not.toBeInTheDocument();
  });

  it("calls onUnauthorized and stops on a 401 response", async () => {
    const onUnauthorized = vi.fn();
    const body = { error: "unauthorized", detail: "missing or invalid API key" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, body)));

    renderQueueList({ apiKey: "bad-key", onUnauthorized });

    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
  });

  it("calls onFiltersChange and resets the offset when a filter select changes", async () => {
    const page: Page = { items: [], total: 0, limit: 20, offset: 0 };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, page)));
    const onFiltersChange = vi.fn();
    const onOffsetChange = vi.fn();

    renderQueueList({ offset: 20, onFiltersChange, onOffsetChange });

    fireEvent.change(screen.getByLabelText("Categoria"), { target: { value: "bug" } });

    expect(onFiltersChange).toHaveBeenCalledWith({ ...EMPTY_FILTERS, categoria: "bug" });
    expect(onOffsetChange).toHaveBeenCalledWith(0);
  });
});
