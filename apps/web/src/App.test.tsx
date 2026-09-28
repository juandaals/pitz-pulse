import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { API_KEY_STORAGE, App, AUTHOR_STORAGE } from "./App";

interface MockQueueListProps {
  apiKey: string;
  onSelect: (id: string) => void;
  onUnauthorized: () => void;
}

interface MockRequestDetailProps {
  onConflict: (message: string) => void;
}

const queueListRenders: MockQueueListProps[] = [];

vi.mock("./components/KeyPrompt", () => ({
  KeyPrompt: ({ onSubmit }: { onSubmit: (apiKey: string, author: string) => void }) => (
    <button onClick={() => onSubmit("key-1", "ana")}>submit-key</button>
  ),
}));

vi.mock("./components/QueueList", () => ({
  EMPTY_FILTERS: { categoria: "", prioridad: "", area_sugerida: "" },
  QueueList: (props: MockQueueListProps) => {
    queueListRenders.push(props);
    return (
      <div data-testid="queue-list">
        <button onClick={props.onUnauthorized}>trigger-401</button>
        <button onClick={() => props.onSelect("REQ-1")}>select-item</button>
      </div>
    );
  },
}));

vi.mock("./components/RequestDetail", () => ({
  RequestDetail: (props: MockRequestDetailProps) => (
    <div data-testid="request-detail">
      <button onClick={() => props.onConflict("only classified requests can be corrected")}>
        trigger-409
      </button>
    </div>
  ),
}));

beforeEach(() => {
  sessionStorage.clear();
  queueListRenders.length = 0;
});

describe("App", () => {
  it("shows KeyPrompt when there is no stored key", () => {
    render(<App />);
    expect(screen.getByText("submit-key")).toBeInTheDocument();
    expect(screen.queryByTestId("queue-list")).not.toBeInTheDocument();
  });

  it("uses the key already stored in sessionStorage on the next call, skipping KeyPrompt", () => {
    sessionStorage.setItem(API_KEY_STORAGE, "stored-key");
    sessionStorage.setItem(AUTHOR_STORAGE, "ana");

    render(<App />);

    expect(screen.getByTestId("queue-list")).toBeInTheDocument();
    expect(queueListRenders).toHaveLength(1);
    expect(queueListRenders[0]?.apiKey).toBe("stored-key");
  });

  it("submitting the key prompt stores it and switches to the queue list", () => {
    render(<App />);

    fireEvent.click(screen.getByText("submit-key"));

    expect(screen.getByTestId("queue-list")).toBeInTheDocument();
    expect(sessionStorage.getItem(API_KEY_STORAGE)).toBe("key-1");
    expect(sessionStorage.getItem(AUTHOR_STORAGE)).toBe("ana");
  });

  it("a 401 from a child clears the stored key and falls back to KeyPrompt", () => {
    sessionStorage.setItem(API_KEY_STORAGE, "stored-key");
    sessionStorage.setItem(AUTHOR_STORAGE, "ana");
    render(<App />);

    fireEvent.click(screen.getByText("trigger-401"));

    expect(screen.getByText("submit-key")).toBeInTheDocument();
    expect(sessionStorage.getItem(API_KEY_STORAGE)).toBeNull();
    expect(sessionStorage.getItem(AUTHOR_STORAGE)).toBeNull();
  });

  it("a 409 from RequestDetail shows the conflict message and returns to the list", () => {
    sessionStorage.setItem(API_KEY_STORAGE, "stored-key");
    sessionStorage.setItem(AUTHOR_STORAGE, "ana");
    render(<App />);

    fireEvent.click(screen.getByText("select-item"));
    expect(screen.getByTestId("request-detail")).toBeInTheDocument();

    fireEvent.click(screen.getByText("trigger-409"));

    expect(screen.getByTestId("queue-list")).toBeInTheDocument();
    expect(screen.getByText("only classified requests can be corrected")).toBeInTheDocument();
  });
});
