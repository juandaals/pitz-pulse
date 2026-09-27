/**
 * The low-confidence review queue (Spec 05 §3, §5). Default filters `status=classified` +
 * `needs_review=true`; the reviewer can additionally narrow by categoria, prioridad and
 * area_sugerida. Pagination offset is owned by `App` so it survives a round trip through
 * `RequestDetail`; after a save the page is refetched at the same offset, clamped to the last
 * page if the reviewed item left the queue (Spec 05 §7).
 */
import { useCallback, useEffect, useState } from "react";
import { areaValues, categoriaValues, prioridadValues } from "../api/schema";
import { ApiError, listRequests } from "../api/client";
import type { Area, Categoria, Page, Prioridad } from "../api/client";

const LIMIT = 20;

export interface QueueListProps {
  apiKey: string;
  offset: number;
  onOffsetChange: (offset: number) => void;
  refreshToken: number;
  onSelect: (id: string) => void;
  onUnauthorized: () => void;
}

interface Filters {
  categoria: Categoria | "";
  prioridad: Prioridad | "";
  area_sugerida: Area | "";
}

const EMPTY_FILTERS: Filters = { categoria: "", prioridad: "", area_sugerida: "" };

/** Same page the reviewed item would now fall on, so a shrunk queue never shows an empty gap. */
function clampOffset(offset: number, total: number, limit: number): number {
  if (total === 0) return 0;
  const lastPageOffset = Math.floor((total - 1) / limit) * limit;
  return Math.min(offset, lastPageOffset);
}

export function QueueList({
  apiKey,
  offset,
  onOffsetChange,
  refreshToken,
  onSelect,
  onUnauthorized,
}: QueueListProps) {
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [page, setPage] = useState<Page | null>(null);
  const [status, setStatus] = useState<"loading" | "loaded" | "error">("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setStatus("loading");
    try {
      const result = await listRequests(apiKey, {
        status: "classified",
        needs_review: true,
        categoria: filters.categoria || undefined,
        prioridad: filters.prioridad || undefined,
        area_sugerida: filters.area_sugerida || undefined,
        limit: LIMIT,
        offset,
      });
      setPage(result);
      setStatus("loaded");
      const clamped = clampOffset(offset, result.total, result.limit);
      if (clamped !== offset) {
        onOffsetChange(clamped);
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        onUnauthorized();
        return;
      }
      setStatus("error");
      setErrorMessage(error instanceof Error ? error.message : "unexpected error");
    }
    // `refreshToken` has no effect on the request itself; it only forces this callback's identity
    // to change so the effect below refetches after a save (Spec 05 §7).
  }, [apiKey, filters, offset, onOffsetChange, onUnauthorized, refreshToken]);

  useEffect(() => {
    load();
  }, [load]);

  function updateFilter(name: keyof Filters, value: string) {
    setFilters((prev) => ({ ...prev, [name]: value }));
    onOffsetChange(0);
  }

  return (
    <div className="queue-list">
      <h1>Review queue</h1>
      <div className="filters">
        <label>
          Categoria
          <select value={filters.categoria} onChange={(e) => updateFilter("categoria", e.target.value)}>
            <option value="">All</option>
            {categoriaValues.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          Prioridad
          <select value={filters.prioridad} onChange={(e) => updateFilter("prioridad", e.target.value)}>
            <option value="">All</option>
            {prioridadValues.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          Area sugerida
          <select
            value={filters.area_sugerida}
            onChange={(e) => updateFilter("area_sugerida", e.target.value)}
          >
            <option value="">All</option>
            {areaValues.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
      </div>

      {status === "loading" && <p>Loading...</p>}
      {status === "error" && (
        <div className="error-box">
          <p>Could not load the queue: {errorMessage}</p>
          <button type="button" onClick={load}>
            Retry
          </button>
        </div>
      )}
      {status === "loaded" && page && page.items.length === 0 && (
        <p>No items in the review queue.</p>
      )}
      {status === "loaded" && page && page.items.length > 0 && (
        <>
          <table>
            <thead>
              <tr>
                <th>id</th>
                <th>categoria</th>
                <th>prioridad</th>
                <th>area_sugerida</th>
                <th>idioma</th>
                <th>confianza</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((item) => (
                <tr key={item.id} onClick={() => onSelect(item.id)} className="queue-row">
                  <td>
                    {item.id}
                    {item.provider === "mock" && <span className="badge-mock">mock</span>}
                  </td>
                  <td>{item.categoria}</td>
                  <td>{item.prioridad}</td>
                  <td>{item.area_sugerida}</td>
                  <td>{item.idioma}</td>
                  <td>{item.confianza}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="pagination">
            <button type="button" disabled={offset === 0} onClick={() => onOffsetChange(Math.max(0, offset - LIMIT))}>
              Previous
            </button>
            <span>
              {offset + 1}-{Math.min(offset + LIMIT, page.total)} of {page.total}
            </span>
            <button
              type="button"
              disabled={offset + LIMIT >= page.total}
              onClick={() => onOffsetChange(offset + LIMIT)}
            >
              Next
            </button>
          </div>
        </>
      )}
    </div>
  );
}
