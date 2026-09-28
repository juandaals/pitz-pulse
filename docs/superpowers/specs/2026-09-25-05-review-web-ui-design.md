# Spec 05 — Review web UI (extra X5)

- **Covers:** X5; makes R1.10 (review queue) and R2.3 (corrections) usable by non-developers
- **Depends on:** Specs 01–04 done and verified; `make web-types` (04a); CI freshness check is
  added by 06a, and a local `make web-types` + `git diff --exit-code` covers it until then
- **Status:** implemented on feat/spec-05-web
- **Decisions used:** D12, D13, D22 (see `docs/MASTER.md`)

## 1. Goal

A minimal internal page where a reviewer sees the low-confidence queue, reads a request, and
confirms or corrects its classification — feeding the corrections table.

Out of scope: user accounts, SSO, design system, router library, state-management library.

## 2. Stack, dependencies and serving (`apps/web`)

| Dependency | Why |
|---|---|
| `react`, `react-dom` | UI |
| `vite`, `typescript` | build + types |
| `openapi-typescript` (dev) | generates `src/api/schema.ts` from the API's OpenAPI; run with enum-values output so selects get runtime option lists (Spec 02 types enums in response models) |
| `vitest`, `@testing-library/react`, `jsdom` (dev) | unit tests |

Serving (D22): `apps/web/Dockerfile` (node build stage → `nginx:alpine`) + `apps/web/nginx.conf`
that serves the build and **proxies `/api/` → `http://api:8000/`**. The browser only talks to one
origin, so the API needs no CORS. Compose `web` service lives under profile `web`
(`docker compose --profile web up`), `depends_on: api: condition: service_healthy`; a web build
failure can never block the core `docker compose up`.

`make web-types`: dumps `create_app().openapi()` offline via `uv` (no running server) with
`LLM_PROVIDER=mock API_KEY=web-types DB_PATH=<temp file>` set by the target (no secrets, no real
DB; Spec 02 §9), then runs
`openapi-typescript`. The generated file is committed.

The UI shows a "mock" badge on any item whose `provider` is `mock` (Spec 02 Item, D25), and maps
422 `validation_error` `fields` to form errors (cross-field rules arrive with `loc: ["body"]` and
are shown above the form).

## 3. Components (`apps/web/src/`)

| File | Responsibility |
|---|---|
| `api/schema.ts` | generated types + enum value arrays |
| `api/client.ts` | `fetch` wrapper: base `/api`, `X-API-Key`, error body `{error, detail, fields?}` → `ApiError` |
| `App.tsx` | holds API key + reviewer name (sessionStorage), selected id; list ↔ detail |
| `components/KeyPrompt.tsx` | asks for API key and reviewer name once |
| `components/QueueList.tsx` | filters (default `status=classified&needs_review=true`; categoria, prioridad, area_sugerida) + pagination |
| `components/RequestDetail.tsx` | message, current vs original classification, confianza, corrections history; pending/failed rows read-only with `error` |
| `components/CorrectionForm.tsx` | enum selects, resumen, requiere_info ⇄ pregunta, reason; Confirm / Save |
| `validation.ts` | client mirror of contract rules; word count = `text.trim().split(/\s+/).filter(Boolean).length`; author 1–100 chars — server stays authoritative |

## 4. Component diagram

```
App (apiKey, author, selectedId)
 ├── KeyPrompt                         (no key)
 ├── QueueList ──► client.listRequests(filters, page)
 └── RequestDetail ──► client.getRequest(id)
       └── CorrectionForm ──► validation.validateCorrection(form, author) -> FieldError[]
                         └──► client.patchRequest(id, changedFieldsOnly + author + reason)
client.ts ──types──► schema.ts (generated from API OpenAPI)
```

## 5. Flow — review one item

```
open app ─► no key? ─► KeyPrompt (key + reviewer name) ─► sessionStorage
   ▼
QueueList  GET /api/solicitudes?status=classified&needs_review=true
   ▼ click row
RequestDetail  GET /api/solicitudes/{id}
   ├─ status ≠ classified ─► read-only view (status, error) + back
   ├─ "Confirm" ─► PATCH {author}                       (empty diff = confirmation)
   └─ edit ─► validate ─► PATCH {changed fields only, author, reason}
                           (requiere_info off → pregunta_seguimiento: null)
   ▼
200 ─► back to list, refetch same offset (clamped to last page)
401 ─► clear key ─► KeyPrompt · 422 ─► field errors from `fields` · 409 ─► message + back to list
5xx/network ─► error with retry
```

## 6. State diagram — detail view

```
            load error (→ back to list)
                ▲
┌─────────┐ ok ┌┴────────┐ edit ┌─────────┐ submit ┌────────────┐ 200 ┌───────┐
│ loading │───►│ viewing │─────►│ editing │───────►│ submitting │────►│ saved │──► list (refetch)
└─────────┘    └────┬────┘      └────┬────┘        └──┬───┬──┬──┘     └───────┘
                    │ confirm        │ cancel         │   │  │ 401 ──► KeyPrompt
                    └──► submitting  └──► viewing     │   │ 422 ──► editing (field errors)
                                                      │  409 ──► error ("changed", back to list)
                                                      5xx/net ─► error ──retry──► submitting
```

## 7. Gaps, edge cases, contradictions (this spec)

- **API key in the browser**: acceptable for a local demo only; production = SSO, `author` from the
  session. Documented.
- **Two reviewers on one item**: last write wins, both corrections kept; optimistic locking = future.
- **409 from PATCH**: happens when the item is not `classified` (e.g. opened from a non-default
  filter); detail shows pending/failed read-only, so the form is never offered for them.
- **Enum drift**: prevented by generated types + freshness check.
- **Pagination after review**: the reviewed item leaves the queue → refetch same offset, clamp.
- **Empty queue** → explicit empty state.
- **Word counting**: same rule as the server (shared test vector with double spaces and newline).

## 8. Tests (vitest)

| File | Cases |
|---|---|
| `client.test.ts` | sends `X-API-Key` and `/api` base; maps 401/404/409/422 (with `fields`) and network errors to `ApiError` |
| `validation.test.ts` | word limit incl. shared vector; pregunta rules; author required 1–100 |
| `App.test.tsx` | no key → KeyPrompt; stored key used on next call; 401 clears key |
| `QueueList.test.tsx` | default filters in request; empty state; refetch after save at clamped offset |
| `RequestDetail.test.tsx` | current vs original values; history; failed row read-only with error |
| `CorrectionForm.test.tsx` | Confirm sends only `author`; only changed fields sent; requiere_info off → `pregunta_seguimiento: null`; 422 `fields` rendered; 409 message |

## 9. Acceptance

`npm test` green; `docker compose --profile web up` serves the page; manual run: correct one item,
verify via `GET /solicitudes/{id}` that `corrections[]` has the entry.
