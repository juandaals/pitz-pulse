/**
 * Asks for the API key and reviewer name once (Spec 05 §3); both are kept by `App` in
 * `sessionStorage` after submission. The API key travels in the browser only for this local
 * demo (Spec 05 §7) — production would use SSO and derive `author` from the session.
 */
import { useState } from "react";
import type { FormEvent } from "react";

export interface KeyPromptProps {
  onSubmit: (apiKey: string, author: string) => void;
}

export function KeyPrompt({ onSubmit }: KeyPromptProps) {
  const [apiKey, setApiKey] = useState("");
  const [author, setAuthor] = useState("");
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmedKey = apiKey.trim();
    const trimmedAuthor = author.trim();
    if (!trimmedKey) {
      setError("API key is required");
      return;
    }
    if (trimmedAuthor.length < 1 || trimmedAuthor.length > 100) {
      setError("Reviewer name must have 1-100 characters");
      return;
    }
    setError(null);
    onSubmit(trimmedKey, trimmedAuthor);
  }

  return (
    <form className="key-prompt" onSubmit={handleSubmit}>
      <h1>Pitz Pulse — Review</h1>
      <p>Enter the API key and your name to start reviewing the queue.</p>
      <label>
        API key
        <input
          type="password"
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
          autoComplete="off"
        />
      </label>
      <label>
        Reviewer name
        <input
          type="text"
          value={author}
          onChange={(event) => setAuthor(event.target.value)}
          autoComplete="off"
        />
      </label>
      {error && <p className="form-error">{error}</p>}
      <button type="submit">Continue</button>
    </form>
  );
}
