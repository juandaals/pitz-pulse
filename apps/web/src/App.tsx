/**
 * Holds the API key + reviewer name (sessionStorage) and switches between the queue list and a
 * selected request's detail (Spec 05 §3-§5). A 401 from any child clears the key and falls back
 * to `KeyPrompt`.
 */
import { useState } from "react";
import { KeyPrompt } from "./components/KeyPrompt";
import { QueueList } from "./components/QueueList";
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
  const [offset, setOffset] = useState(0);
  const [refreshToken, setRefreshToken] = useState(0);

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
        onSaved={() => {
          setRefreshToken((token) => token + 1);
          setView({ name: "list" });
        }}
        onUnauthorized={handleUnauthorized}
      />
    );
  }

  return (
    <QueueList
      apiKey={apiKey}
      offset={offset}
      onOffsetChange={setOffset}
      refreshToken={refreshToken}
      onSelect={(id) => setView({ name: "detail", id })}
      onUnauthorized={handleUnauthorized}
    />
  );
}
