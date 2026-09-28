/**
 * Holds the API key + reviewer name (sessionStorage), the queue's filters and pagination offset,
 * and switches between the queue list and a selected request's detail (Spec 05 §3-§5). `filters`
 * and `offset` live here (not in `QueueList`, which unmounts on every round trip through
 * `RequestDetail`) so they survive that round trip. A 401 from any child clears the key and falls
 * back to `KeyPrompt`; a 409 from a correction (the row changed between fetch and submit, Spec 05
 * §5/§6) shows its message here and returns to the list.
 */
import { useState } from "react";
import { EMPTY_FILTERS, QueueList } from "./components/QueueList";
import type { Filters } from "./components/QueueList";
import { KeyPrompt } from "./components/KeyPrompt";
import { RequestDetail } from "./components/RequestDetail";

export const API_KEY_STORAGE = "pitz-pulse-review:api-key";
export const AUTHOR_STORAGE = "pitz-pulse-review:author";

type View = { name: "list" } | { name: "detail"; id: string };

export function App() {
  const [apiKey, setApiKey] = useState<string | null>(() =>
    sessionStorage.getItem(API_KEY_STORAGE),
  );
  const [author, setAuthor] = useState<string | null>(() => sessionStorage.getItem(AUTHOR_STORAGE));
  const [view, setView] = useState<View>({ name: "list" });
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [offset, setOffset] = useState(0);
  const [refreshToken, setRefreshToken] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);

  function handleKeySubmit(key: string, name: string) {
    sessionStorage.setItem(API_KEY_STORAGE, key);
    sessionStorage.setItem(AUTHOR_STORAGE, name);
    setApiKey(key);
    setAuthor(name);
  }

  function handleUnauthorized() {
    sessionStorage.removeItem(API_KEY_STORAGE);
    sessionStorage.removeItem(AUTHOR_STORAGE);
    setApiKey(null);
    setAuthor(null);
  }

  function handleSelect(id: string) {
    setNotice(null);
    setView({ name: "detail", id });
  }

  function handleSaved() {
    setRefreshToken((token) => token + 1);
    setView({ name: "list" });
  }

  function handleConflict(message: string) {
    setNotice(message);
    setRefreshToken((token) => token + 1);
    setView({ name: "list" });
  }

  if (!apiKey || !author) {
    return <KeyPrompt onSubmit={handleKeySubmit} />;
  }

  if (view.name === "detail") {
    return (
      <RequestDetail
        apiKey={apiKey}
        author={author}
        requestId={view.id}
        onBack={() => setView({ name: "list" })}
        onSaved={handleSaved}
        onConflict={handleConflict}
        onUnauthorized={handleUnauthorized}
      />
    );
  }

  return (
    <>
      {notice && (
        <div className="notice">
          <p>{notice}</p>
          <button type="button" onClick={() => setNotice(null)}>
            Dismiss
          </button>
        </div>
      )}
      <QueueList
        apiKey={apiKey}
        filters={filters}
        onFiltersChange={setFilters}
        offset={offset}
        onOffsetChange={setOffset}
        refreshToken={refreshToken}
        onSelect={handleSelect}
        onUnauthorized={handleUnauthorized}
      />
    </>
  );
}
