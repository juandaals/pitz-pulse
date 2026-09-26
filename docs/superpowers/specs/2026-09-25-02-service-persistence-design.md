# Spec 02 — Service & persistence

- **Covers:** R2.1–R2.5, R1.10 (review queue), R4.1 (idempotency + endpoint tests)
- **Depends on:** Spec 01 (`Classifier`, `schema.py`, `LLMSettings`)
- **Status:** draft (rev 3 — aligned with Spec 01 rev 3)
- **Decisions used:** D5–D9, D11, D12, D17, D21 (see `docs/MASTER.md`)

## 1. Goal

HTTP service to create (idempotently), list, read and correct classified requests, backed by SQLite
with versioned migrations, protected by an API key. Endpoint paths follow the case contract
(`/solicitudes`, D17); request fields and everything else are English (D11).

## 2. Components (`apps/api/src/pitz_pulse/`)

| File | Responsibility |
|---|---|
| `db.py` | `connect(path)` → new connection per call (`isolation_level=None`, WAL, `busy_timeout=5000`, `foreign_keys=ON`); `migrate(conn)` |
| `repository.py` | SQL only: `reserve`, `complete`, `fail`, `get`, `list`, `apply_correction`, `list_corrections`. Every write inside explicit `BEGIN IMMEDIATE … COMMIT` |
| `service.py` | `TriageService`: idempotency, review flag, correction rules. No HTTP, no SQL |
| `api.py` | `create_app(settings, adapter=None)` factory (served with `uvicorn --factory`; no module-level app); builds the classifier with `build_classifier(settings, adapter)` (Spec 01); routers; `require_api_key` dependency on the `/solicitudes` router only; exception handlers; `X-Pitz-Provider: mock` response header in mock mode (D25) |
| `api_models.py` | HTTP request/response models (enums typed so OpenAPI carries their values) |
| `settings_api.py` | `ApiSettings(LLMSettings)`: `API_KEY` (required, non-empty), `DB_PATH`, `PENDING_STALE_SECONDS` |
| `migrations/001_init.sql` | Initial schema |

A FastAPI dependency yields `db.connect(settings.DB_PATH)` per request and closes it. Routes are sync
`def` (FastAPI thread pool, D5). Run with a single uvicorn worker (documented); correctness does
not depend on it (reservation is DB-level).

Dependencies introduced: `fastapi`, `uvicorn` (runtime); `httpx` (dev, `TestClient`).

## 3. Data model (`migrations/001_init.sql`)

Contract fields keep their contract names; everything else is English.

```
┌──────────────────────────────────────────┐        ┌─────────────────────────────────┐
│ requests                                 │        │ corrections                     │
├──────────────────────────────────────────┤        ├─────────────────────────────────┤
│ id TEXT PK                               │◄──────┐│ id INTEGER PK AUTOINCREMENT     │
│ message TEXT NOT NULL                    │       └┤ request_id TEXT FK NOT NULL     │
│ source_area TEXT                         │        │ previous_values TEXT (JSON)     │
│ message_hash TEXT NOT NULL (sha256)      │        │ new_values TEXT (JSON;          │
│ status TEXT CHECK pending|classified|    │        │   {} = confirmed as correct)    │
│        failed                            │        │ author TEXT NOT NULL            │
│ claim_token TEXT                         │        │ reason TEXT                     │
│ categoria, prioridad, area_sugerida,     │        │ created_at TEXT NOT NULL        │
│ idioma  TEXT  CHECK (enum values)        │        └─────────────────────────────────┘
│ resumen TEXT, requiere_info INTEGER,     │
│ pregunta_seguimiento TEXT,               │   current (possibly corrected) values
│ confianza REAL, version_prompt TEXT      │
│ needs_review INTEGER NOT NULL DEFAULT 0  │
│ corrected INTEGER NOT NULL DEFAULT 0     │
│ original_classification TEXT (JSON)      │   immutable once written
│ error TEXT                               │   last failure kind
│ created_at TEXT, updated_at TEXT         │   UTC ISO-8601 ms + Z, written by Python only
└──────────────────────────────────────────┘
indexes: (categoria), (prioridad), (area_sugerida), (needs_review), (status), (created_at)
schema_migrations(version TEXT PK, applied_at TEXT)
```

The stored `message` is the original text (D9); only the LLM egress is masked; logs never contain it.

Migration runner: lists `migrations/*.sql` sorted; for each pending file on an autocommit
connection: `BEGIN IMMEDIATE` → re-check `schema_migrations` → execute statements → insert version
→ `COMMIT` (`ROLLBACK` on error, then raise). Running twice is a no-op; two processes starting at
once cannot apply a file twice.

## 4. API

`/health`, `/docs`, `/openapi.json` are open (no data, schema only). Everything under `/solicitudes`
requires `X-API-Key` (compared with `secrets.compare_digest`).

| Method | Path | Success | Errors |
|---|---|---|---|
| GET | `/health` | 200 `{status, provider, model, prompt_version}` — the adapter's effective values | — |
| POST | `/solicitudes` | 201 this call classified (new id or retried failed/stale) · 200 already classified | 401 · 409 `id_conflict` · 409 `in_progress` · 422 · 500 `internal_error` · 502 `classification_failed` · 503 `db_busy` |
| GET | `/solicitudes` | 200 `{items, total, limit, offset}` | 401 · 422 |
| GET | `/solicitudes/{id}` | 200 `ItemDetail` | 401 · 404 |
| PATCH | `/solicitudes/{id}` | 200 `Item` | 401 · 404 · 409 `not_classified` · 422 · 503 |

**POST body:** `{id, message, source_area?}` (limits in Spec 01 §2). Unknown fields → 422.

**Item** (list entries, POST and PATCH responses):

| Field | Notes |
|---|---|
| `id`, `message`, `source_area` | input |
| `status` | `pending \| classified \| failed` |
| contract fields `categoria … version_prompt` | current values; `null` unless `status = classified` |
| `needs_review`, `corrected` | bool |
| `error` | failure kind or `null` |
| `created_at`, `updated_at` | UTC ISO-8601 |

**ItemDetail** = Item + `original_classification` (object or `null`) + `corrections[]`
(`{previous_values, new_values, author, reason, created_at}`, oldest first).

**GET filters:** `categoria`, `prioridad`, `area_sugerida`, `needs_review`, `status` (no default:
all statuses); `limit` 1–100 (default 20), `offset` ≥ 0; order `created_at DESC, id ASC`; filters
apply to current values (G12). Invalid enum → 422. Offset past the end → empty `items`, real `total`.

**PATCH body:** any subset of correctable fields (G11: `categoria, prioridad, area_sugerida, idioma,
resumen, requiere_info, pregunta_seguimiento`) + `author` (1–100 chars after strip, required) +
`reason?` (≤ 500). Only fields present in the body are merged (`exclude_unset`). Explicit `null`
allowed only for `pregunta_seguimiento`. Unknown or non-correctable fields (`id`, `confianza`,
`version_prompt`, …) → 422.

**Error body (all errors):** `{error: <code>, detail: <text>, fields?: [{loc, msg}]}` — `fields`
present on 422 (body validation and contract-rule violations alike). Implemented with handlers for
`RequestValidationError`, `HTTPException` and domain errors.

## 5. Class diagram

```
┌──────────────────────────┐ Depends ┌──────────────────────────────┐
│ api.py                   │────────►│ TriageService                │
│ create_app(settings,     │         │ - repo_factory(conn)         │
│            adapter=None) │         │ - classifier: Classifier     │
│ require_api_key()        │         │ - threshold: float           │
│ get_conn() (per request) │         │ - pending_stale_s: int       │
└──────────────────────────┘         │ + create(req) -> (Item, created: bool)
                                     │ + get(id) -> ItemDetail      │
                                     │ + list(filters, page) -> Page│
                                     │ + correct(id, patch) -> Item │
                                     └──────┬───────────────┬───────┘
                                            ▼               ▼
                                  ┌──────────────────┐  ┌──────────────────┐
                                  │ Repository(conn) │  │ Classifier (01)  │
                                  └──────────────────┘  └──────────────────┘

Domain errors → HTTP:
 IdConflict 409 · InProgress 409 · NotFound 404 · NotClassified 409 · ContractViolation 422
 ClassificationError 502 · DbBusy (sqlite "database is locked") 503 · other Exception 500
```

## 6. Flow — idempotent POST

```
POST {id, message, source_area}
  │ validate body (422)
  ▼
hash = sha256(original message, unstripped);  token = uuid4()
BEGIN IMMEDIATE
  row = get(id)
  ├─ none ─────────────────────────► INSERT status=pending, claim_token=token ─┐
  ├─ hash differs ─────────────────► ROLLBACK → 409 id_conflict                │
  ├─ classified ───────────────────► ROLLBACK → 200 stored item (no model call)│
  ├─ pending & fresh ──────────────► ROLLBACK → 409 in_progress                │
  ├─ pending & stale (G16) ────────► UPDATE claim_token=token, updated_at ─────┤
  └─ failed (G15) ─────────────────► UPDATE status=pending, claim_token=token ─┤
COMMIT                                                                         │
  ▼ ◄──────────────────────────────────────────────────────────────────────────┘
classifier.classify(req) → ClassifyOutcome   (outside any transaction: no DB lock during the LLM call;
                                   the service stores outcome.classification)
  ├─ ok ─────────────────► complete(id, token, …): UPDATE … WHERE id=? AND status='pending'
  │                          AND claim_token=?  → status=classified, fields,
  │                          original_classification, needs_review = confianza < threshold,
  │                          claim_token=NULL
  │                          0 rows? (lost claim) → log warning, return current row
  │                        → 201 (created=True)
  ├─ ClassificationError ► fail(id, token, kind) (same guard) → 502
  └─ any other exception ► best-effort fail(id, token, "internal_error") → re-raise → 500
                           (response detail is a constant; logs carry the class name only)
```

`PENDING_STALE_SECONDS` default 420; at startup it must be ≥
`(1 + INVALID_OUTPUT_RETRIES) × deadline_s + 60`, where `deadline_s` is Spec 01's hard per-invoke
deadline (`LLM_TIMEOUT_SECONDS × (1 + LLM_MAX_RETRIES) + 30`; 150 s with defaults → minimum 360 s),
else startup error. Because every adapter enforces that deadline, a live worker can never be
re-claimed; the claim-token guard additionally makes any late worker a no-op.

## 7. Flow — PATCH correction

```
PATCH {fields…, author, reason?}
  ▼ validate body shape (422)
BEGIN IMMEDIATE
  row = get(id) ── none ──► ROLLBACK 404 ;  status ≠ classified ──► ROLLBACK 409 not_classified
  merged = current ⊕ present fields ──► Classification.model_validate(merged) ── fail ──► ROLLBACK 422
  diff = fields whose value changed
  INSERT corrections(previous_values=current[diff], new_values=merged[diff], author, reason)
  UPDATE requests SET <diff>, corrected = corrected OR (diff non-empty), needs_review = 0, updated_at
COMMIT ──► 200
```
Empty diff = human confirmed the model output (`new_values = {}`); `corrected` stays as it was.

## 8. State diagram — request lifecycle

```
                        POST (new id)
                             │
                             ▼
                      ┌─────────────┐ classify ok ┌───────────────────┐
         ┌───────────►│   pending   │────────────►│ classified         │
         │            │ claim_token │             │ needs_review=conf<t│
         │            └──┬───────┬──┘             └──┬──────────┬──────┘
         │  classify err │       │ stale:            │ PATCH     │ PATCH (diff)
         │  or internal  │       │ next POST         │ (no diff) │
         │               ▼       │ re-claims         │           ▼
         │        ┌──────────┐   └──► pending        │    classified,
         └────────│  failed  │        (new token)    │    corrected=1
   POST again     └──────────┘                       ▼    needs_review=0
   (same id+text)                          needs_review=0  (more PATCHes → more rows)
```

## 9. Gaps, edge cases, contradictions (this spec)

- **G5** same id, different text → 409 (D8). Whitespace-only difference counts as different (no
  silent normalization of user text); documented.
- **G6** concurrent same-id POSTs → `BEGIN IMMEDIATE` reservation; exactly one model call.
- **G11 / G12** correctable fields; filters on current values.
- **G15** failed → 502, re-POST retries; failing again stays failed (502).
- **G16 / G24** stale pending re-claim with claim token; late completion is a no-op.
- **G17** input limits.
- Same id + same text + different `source_area` → treated as the same request (case defines
  idempotency by id + message).
- `API_KEY` unset/empty → startup error. `database is locked` beyond busy timeout → 503 `db_busy`.
- Concurrent PATCHes → serialized; the second's `previous_values` equal the first's `new_values`.

## 10. Tests

| File | Cases |
|---|---|
| `test_db.py` | migrate empty DB; second run no-op; failing migration leaves no partial table or version row; two connections serialize on `BEGIN IMMEDIATE`; CHECK enum lists equal the `schema.py` StrEnums (parity) |
| `test_repository.py` | reserve/complete/fail; claim-token guard (late complete after re-claim → no-op); filters combined incl. `status`; pagination incl. offset past end; ordering tie-break |
| `test_idempotency.py` | same id+text → 1 classifier call, 201 then 200; different text → 409; pending fresh → 409; stale pending re-claimed; failed → retried (201); failed twice → 502 and still failed; FakeAdapter raising `RuntimeError` → row failed `internal_error`, 500, re-POST retries; **two threads same id → exactly 1 classifier call**; `needs_review` recomputed when a failed row is later classified |
| `test_corrections.py` | original immutable after 2 PATCHes; history order; empty diff = confirmation clears `needs_review`, keeps `corrected`; omitted vs explicit null; contract rule violation → 422 with `fields`; PATCH on pending/failed → 409; non-correctable or unknown field → 422; blank author → 422; concurrent PATCH previous_values |
| `test_api.py` | 401 missing/wrong key; mock mode → `X-Pitz-Provider: mock` header; `/health` reports effective provider/model; `/health`, `/docs`, `/openapi.json` open; POST 201/200/409/422/502/503; GET list filters incl. `needs_review`, `status`; invalid enum filter 422; limit 0/101 → 422; GET 404; PATCH 404/422/200; every error body has `error` and `detail`; Item and ItemDetail keys; OpenAPI schema contains enum values |
| `test_review_flag.py` | confianza below / equal / above threshold |
| `test_settings_api.py` | `API_KEY` required; `PENDING_STALE_SECONDS` below the derived minimum → error |

All tests use `create_app(settings, adapter=FakeAdapter(...))` and a temp SQLite file.

## 11. Acceptance

`make test` green; manual run in mock mode: POST twice → one `llm_call` log line; PATCH then GET
shows original + correction.
