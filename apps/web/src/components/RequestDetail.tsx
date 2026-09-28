/**
 * One request: message, current vs original classification, confianza and correction history
 * (Spec 05 §3, §5, §6). Only a `classified` row (as of this fetch) offers `CorrectionForm`;
 * `pending`/`failed` rows are read-only and show their `error`. A 409 from the PATCH can still
 * happen here even so: the row can leave `classified` between this fetch and the submit (e.g. a
 * retried POST reclassifying it concurrently, Spec 05 §7) — `CorrectionForm` reports that via
 * `onConflict` instead of showing it inline, and the caller returns to the list (Spec 05 §5/§6).
 */
import { useCallback, useEffect, useState } from "react";
import { ApiError, getRequest } from "../api/client";
import type { CorrectionOut, ItemDetail } from "../api/client";
import { CorrectionForm } from "./CorrectionForm";

export interface RequestDetailProps {
  apiKey: string;
  author: string;
  requestId: string;
  onBack: () => void;
  onSaved: () => void;
  onConflict: (message: string) => void;
  onUnauthorized: () => void;
}

const CONTRACT_FIELDS = [
  "categoria",
  "prioridad",
  "area_sugerida",
  "idioma",
  "resumen",
  "requiere_info",
  "pregunta_seguimiento",
] as const;

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return String(value);
}

/** "confirmed" for an empty diff; otherwise the changed fields as `field: previous → new`. */
function correctionSummary(correction: CorrectionOut): string {
  const fields = Object.keys(correction.new_values);
  if (fields.length === 0) {
    return "confirmed";
  }
  const changes = fields
    .map(
      (field) =>
        `${field}: ${displayValue(correction.previous_values[field])} → ${displayValue(correction.new_values[field])}`,
    )
    .join(", ");
  return correction.reason ? `${changes} (${correction.reason})` : changes;
}

export function RequestDetail({
  apiKey,
  author,
  requestId,
  onBack,
  onSaved,
  onConflict,
  onUnauthorized,
}: RequestDetailProps) {
  const [detail, setDetail] = useState<ItemDetail | null>(null);
  const [status, setStatus] = useState<"loading" | "loaded" | "error">("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setStatus("loading");
    try {
      const result = await getRequest(apiKey, requestId);
      setDetail(result);
      setStatus("loaded");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        onUnauthorized();
        return;
      }
      setStatus("error");
      setErrorMessage(error instanceof Error ? error.message : "unexpected error");
    }
  }, [apiKey, requestId, onUnauthorized]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="request-detail">
      <button type="button" onClick={onBack}>
        Back to list
      </button>

      {status === "loading" && <p>Loading...</p>}
      {status === "error" && (
        <div className="error-box">
          <p>Could not load the request: {errorMessage}</p>
          <button type="button" onClick={load}>
            Retry
          </button>
        </div>
      )}

      {status === "loaded" && detail && (
        <>
          <h1>
            {detail.id}
            {detail.provider === "mock" && <span className="badge-mock">mock</span>}
          </h1>
          <p className="message">{detail.message}</p>
          <p>status: {detail.status}</p>

          {detail.status !== "classified" ? (
            detail.error && <p className="form-error">error: {detail.error}</p>
          ) : (
            <>
              <p>confianza: {displayValue(detail.confianza)}</p>

              <table className="comparison">
                <thead>
                  <tr>
                    <th>field</th>
                    <th>current</th>
                    <th>original</th>
                  </tr>
                </thead>
                <tbody>
                  {CONTRACT_FIELDS.map((field) => (
                    <tr key={field}>
                      <td>{field}</td>
                      <td>{displayValue(detail[field])}</td>
                      <td>
                        {displayValue(
                          detail.original_classification
                            ? detail.original_classification[field]
                            : detail[field],
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <h2>Correction history</h2>
              {detail.corrections.length === 0 ? (
                <p>No corrections yet.</p>
              ) : (
                <ul className="corrections">
                  {detail.corrections.map((correction, index) => (
                    <li key={index}>
                      {correction.created_at} — {correction.author}: {correctionSummary(correction)}
                    </li>
                  ))}
                </ul>
              )}

              <CorrectionForm
                apiKey={apiKey}
                requestId={requestId}
                author={author}
                initial={{
                  categoria: detail.categoria!,
                  prioridad: detail.prioridad!,
                  area_sugerida: detail.area_sugerida!,
                  idioma: detail.idioma!,
                  resumen: detail.resumen ?? "",
                  requiere_info: detail.requiere_info ?? false,
                  pregunta_seguimiento: detail.pregunta_seguimiento,
                }}
                onSuccess={onSaved}
                onConflict={onConflict}
                onUnauthorized={onUnauthorized}
              />
            </>
          )}
        </>
      )}
    </div>
  );
}
