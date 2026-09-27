import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueueList } from "./QueueList";
import type { Page } from "../api/client";

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

describe("QueueList", () => {
  it("requests the default filters (status=classified, needs_review=true) with no extra filters", async () => {
    const page: Page = { items: [], total: 0, limit: 20, offset: 0 };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, page));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <QueueList
        apiKey="secret-key"
        offset={0}
        onOffsetChange={vi.fn()}
        refreshToken={0}
        onSelect={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe("/api/solicitudes?status=classified&needs_review=true&limit=20&offset=0");
  });

  it("shows an explicit empty state when the queue has no items", async () => {
    const page: Page = { items: [], total: 0, limit: 20, offset: 0 };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, page)));

    render(
      <QueueList
        apiKey="secret-key"
        offset={0}
        onOffsetChange={vi.fn()}
        refreshToken={0}
        onSelect={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    );

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

    const { rerender } = render(
      <QueueList
        apiKey="secret-key"
        offset={20}
        onOffsetChange={onOffsetChange}
        refreshToken={0}
        onSelect={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    );

    await waitFor(() => expect(onOffsetChange).toHaveBeenCalledWith(0));

    // Stand in for `App` applying the clamp and bumping refreshToken after a save.
    rerender(
      <QueueList
        apiKey="secret-key"
        offset={0}
        onOffsetChange={onOffsetChange}
        refreshToken={1}
        onSelect={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [secondUrl] = fetchMock.mock.calls[1] as [string];
    expect(secondUrl).toContain("offset=0");
  });

  it("calls onUnauthorized and stops on a 401 response", async () => {
    const onUnauthorized = vi.fn();
    const body = { error: "unauthorized", detail: "missing or invalid API key" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, body)));

    render(
      <QueueList
        apiKey="bad-key"
        offset={0}
        onOffsetChange={vi.fn()}
        refreshToken={0}
        onSelect={vi.fn()}
        onUnauthorized={onUnauthorized}
      />,
    );

    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
  });
});
