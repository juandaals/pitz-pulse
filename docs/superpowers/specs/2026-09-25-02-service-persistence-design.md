# Spec 02 — Service & persistence

- **Covers:** R2.1–R2.5, R1.10 (review queue), R4.1 (idempotency + endpoint tests)
- **Depends on:** Spec 01 as built (branch `feat/spec-01-classification-core`, PR #1):
  `schema.RequestInput`, `schema.Classification`, `config.parse_llm_settings(env)` → `LLMSettings`,
  `classifier.build_classifier(settings, adapter=None)` → `Classifier.classify(req)` →
  `ClassifyOutcome(classification, attempts)`, raising `ClassificationError(kind, attempts)` or
  `ClassificationCrash(error_type, attempts)`; `logs.configure_logging` / `log_event`
- **Status:** implemented on feat/spec-02-service-persistence (rev 5.1; implementation-gate
  rulings applied). Reviews: `docs/superpowers/reviews/2026-09-27-02-spec-review.md`,
  `docs/superpowers/reviews/2026-09-27-02-plan-review.md` and
  `docs/superpowers/reviews/2026-09-27-02-implementation-review.md`
- **Decisions used:** D5–D9, D11, D12, D17, D21, D25, D28, D29, D30 (see `docs/MASTER.md`)

## 1. Goal

HTTP service to create (idempotently), list, read and correct classified requests, backed by SQLite
with versioned migrations, protected by an API key. Endpoint paths follow the case contract
(`/solicitudes`, D17); request fields and everything else are English (D11).

## 2. Components (`apps/api/src/pitz_pulse/`)

| File | Responsibility |
|---|---|
| `db.py` | `connect(path)` → new connection (`isolation_level=None`, `check_same_thread=False`; PRAGMAs in order `busy_timeout=5000`, `journal_mode=WAL`, `foreign_keys=ON`; creates the parent dir); `migrate(conn)` |
| `repository.py` | `Repository(conn)`: SQL plus row ↔ typed mapping (`INTEGER` → `bool`, enum strings → StrEnums, JSON columns → dicts). `transaction()` context manager: `BEGIN IMMEDIATE`, `COMMIT`, `ROLLBACK` on any exception (then re-raise). `read_transaction()`: deferred `BEGIN` … `COMMIT` (rollback on error; reuses an open transaction) so read pairs see one snapshot — `list` (count + rows) and `service.get` (row + corrections). Methods: `get`, `insert_pending`, `reclaim`, `complete`, `fail`, `list`, `insert_correction`, `update_fields`, `list_corrections` |
| `service.py` | `TriageService` (one per app): idempotency, lost-claim mapping, review flag, correction rules, model-slot semaphore. Opens and closes its own connection per call (`db.connect(db_path)`, inside the lock translation: a lock while connecting is `DbBusy` too); drives `repo.transaction()`. No HTTP |
| `errors.py` | Domain errors, all subclasses of `DomainError`: `IdConflict`, `InProgress(retry_after_s)`, `NotFound`, `NotClassified`, `ContractViolation(fields)`, `ClassificationFailed(kind)`, `Busy`, `DbBusy` |
| `api.py` | `create_app(settings: ApiSettings \| None = None, adapter=None)` factory, served with `uvicorn --factory pitz_pulse.api:create_app` (uvicorn calls it with **no arguments**); routes; `require_api_key` on the `/solicitudes` router only |
| `http_errors.py` | Exception handlers (domain errors, `RequestValidationError`, **Starlette's `HTTPException`**, imported as `fastapi.exceptions.StarletteHTTPException`) and a catch-all HTTP middleware: any other exception → 500 `internal_error`, logs `unhandled_error` with the class name only, **never re-raised** (otherwise Starlette re-raises and uvicorn prints a traceback with chained exception text, D9) |
| `api_models.py` | HTTP models: `CreateBody`, `PatchBody`, `ListQuery` (all `extra="forbid"`, strict types), `Item`, `ItemDetail`, `Page`, `ErrorBody` (enums typed so OpenAPI carries their values) |
| `settings_api.py` | `ApiSettings` (frozen dataclass, **composition**): `llm: LLMSettings`, `api_key` (`API_KEY`, required, printable ASCII, `repr=False`), `db_path` (`DB_PATH`), `pending_stale_s` (`PENDING_STALE_SECONDS`, derived floor ≤ value ≤ 604800 = 7 days); `parse_api_settings(env)`. Composition, not inheritance: nothing may `asdict`/`replace` settings (secrets, `__post_init__` checks) |
| `review.py` | `needs_review(confianza, threshold) -> bool` (`confianza < threshold`, D12); imported by Spec 03 scoring so both use one comparator |
| `migrations/001_init.sql` | Initial schema (packaged next to the code: `Path(__file__).parent / "migrations"`) |

**Factory.** `create_app()` with `settings=None`: `configure_logging(os.environ LOG_LEVEL or
"INFO")` → `parse_api_settings(os.environ)` → `configure_logging(settings.llm.log_level)` →
`migrate` → `build_classifier(settings.llm, adapter)` → one `TriageService`. Any `ConfigError`
aborts startup (non-zero exit, message names the variable, never its value): missing `API_KEY`, or
`LLM_PROVIDER=anthropic_api` with an empty `ANTHROPIC_API_KEY` (D29). Tests pass `settings` and a
`FakeAdapter` explicitly.

**Concurrency bound.** `TriageService` owns one `threading.BoundedSemaphore(llm.concurrency)`
(`LLM_CONCURRENCY`) shared by every request of the app (and by Spec 06d, which reuses the app's
service). A request that needs a model call waits at most `QUEUE_WAIT_S = 30` for a slot
(constructor argument, so tests inject a small value). At most `2 × LLM_CONCURRENCY` requests may
wait; beyond that, or on timeout → `Busy`. Slot handling: `acquired = sem.acquire(timeout=…)`;
release in `finally` only if acquired, and before `complete()`. Already-classified and conflicting
requests never take a slot. `/health` is `async def` with no DB access, so a saturated thread pool
cannot fail the compose healthcheck (a recorded exception to D5). Every waiter holds a worker
thread, so the app's lifespan raises anyio's default thread limiter to
`max(current, 3 × LLM_CONCURRENCY + 16)` (64 at the maximum concurrency 16; the default 40 is
never lowered): the `busy` bound, not thread starvation, limits waiting requests.
`require_api_key` is `async def` (a 401 needs no thread). Multiple uvicorn workers would
multiply the bound: idempotency does not depend on a single worker, the cost bound does; compose
runs one worker.

Dependencies introduced: `fastapi`, `uvicorn` (runtime, declared directly even though `uvicorn`
is already transitive). `TestClient` uses `httpx2`, already in the dev group.

## 3. Data model (`migrations/001_init.sql`)

Contract fields keep their contract names; everything else is English.

```
┌───────────────────────────────────────────────┐   ┌──────────────────────────────────────┐
│ requests                                      │   │ corrections                          │
├───────────────────────────────────────────────┤   ├──────────────────────────────────────┤
│ id TEXT PK                                    │◄─┐│ id INTEGER PK AUTOINCREMENT          │
│ message TEXT NOT NULL                         │  └┤ request_id TEXT NOT NULL FK          │
│ source_area TEXT                              │   │ previous_values TEXT NOT NULL        │
│ message_hash TEXT NOT NULL (sha256)           │   │   CHECK json_valid                   │
│ status TEXT NOT NULL CHECK pending|classified │   │ new_values TEXT NOT NULL CHECK       │
│        |failed                                │   │   json_valid ({} = confirmed)        │
│ claim_token TEXT                              │   │ author TEXT NOT NULL                 │
│ categoria, prioridad, area_sugerida, idioma   │   │ reason TEXT                          │
│   TEXT CHECK (enum values)                    │   │ created_at TEXT NOT NULL             │
│ resumen TEXT, pregunta_seguimiento TEXT       │   └──────────────────────────────────────┘
│ requiere_info INTEGER CHECK IN (0,1)          │   index corrections(request_id, id)
│ confianza REAL CHECK BETWEEN 0 AND 1          │
│ version_prompt TEXT                           │
│ provider TEXT, model TEXT                     │   who produced the classification (G21)
│ reviewed INTEGER NOT NULL DEFAULT 0 CHECK 0|1 │   set by any PATCH
│ corrected INTEGER NOT NULL DEFAULT 0 CHECK 0|1│   "ever corrected"
│ original_classification TEXT CHECK json_valid │   10 contract fields; immutable once written
│ error TEXT                                    │   last failure kind; NULL unless failed
│ created_at TEXT NOT NULL, updated_at NOT NULL │   UTC `YYYY-MM-DDTHH:MM:SS.mmmZ`, Python only
│ CHECK status <> 'classified' OR (all contract │
│   fields, provider, model, original NOT NULL) │
└───────────────────────────────────────────────┘
indexes: (categoria), (prioridad), (area_sugerida), (status), (created_at, id)
schema_migrations(version TEXT PK, applied_at TEXT NOT NULL)
```

The stored `message` is the original text (D9); only the LLM egress is masked; logs never contain it.

Migration runner: lists `migrations/*.sql` sorted; for each pending file: `BEGIN IMMEDIATE` →
re-check `schema_migrations` → split the file into statements with `sqlite3.complete_statement`
and `execute` each one → insert version → `COMMIT`; `ROLLBACK` on error, then raise.
**`executescript` is forbidden**: it commits the open transaction first, so a failing file would
leave partial tables (verified). Running twice is a no-op; two processes starting at once cannot
apply a file twice.

## 4. API

`/health`, `/docs`, `/openapi.json` are open (no data, schema only). Everything under `/solicitudes`
requires `X-API-Key`: read with `APIKeyHeader(auto_error=False)`; missing or wrong → 401;
compared as bytes with `hmac.compare_digest` (a non-ASCII header must be a 401, not a 500). On
FastAPI 0.141 authentication runs before body validation: without a valid key, a JSON body that
fails validation gets 401; only a body that is not valid JSON gets 422 (it fails while the body is
read, before dependencies) — documented, no data is exposed. An HTTP middleware rejects a request
whose `Content-Length` exceeds 65536 bytes with 413 `payload_too_large` before auth and body
parsing (bounded memory).

| Method | Path | Success | Errors |
|---|---|---|---|
| GET | `/health` | 200 `{status, provider, model, prompt_version}` — the adapter's effective values | — |
| POST | `/solicitudes` | 201 this call classified (new id or retried failed/stale) · 200 already classified | 401 · 409 `id_conflict` · 409 `in_progress` · 422 · 500 · 502 · 503 `busy` · 503 `db_busy` |
| GET | `/solicitudes` | 200 `{items, total, limit, offset}` | 401 · 422 · 503 `db_busy` |
| GET | `/solicitudes/{id}` | 200 `ItemDetail` | 401 · 404 · 422 · 503 `db_busy` |
| PATCH | `/solicitudes/{id}` | 200 `Item` | 401 · 404 · 409 `not_classified` · 422 · 503 `db_busy` |

**Error codes** (body `{error, detail, fields?, kind?}`):

| HTTP | `error` | Notes |
|---|---|---|
| 401 | `unauthorized` | |
| 404 | `not_found` | unknown id, or unknown route (Starlette `HTTPException`) |
| 405 | `method_not_allowed` | |
| 413 | `payload_too_large` | `Content-Length` > 65536, checked before auth |
| 409 | `id_conflict` · `in_progress` · `not_classified` | `in_progress` carries `Retry-After` = seconds until the row becomes stale |
| 422 | `validation_error` | `fields: [{loc, msg}]`; cross-field contract rules use `loc: ["body"]` |
| 500 | `internal_error` | constant `detail` |
| 502 | `classification_failed` | `kind`: the row's failure kind — `llm_unavailable \| llm_rejected \| invalid_output`, or `internal_error \| busy` when a lost claim reports another request's failure |
| 503 | `busy` · `db_busy` | `Retry-After`: 30 (`busy`); `db_busy`: 1 before any reservation, else seconds until the reserved row becomes stale (`pending_stale_s` − age, ≥ 1; the row stays pending) |

**POST body:** `{id, message, source_area?}` (limits in Spec 01 §2). Unknown fields → 422. Text
that cannot be encoded as UTF-8 (lone surrogates) → 422 (validator added to `RequestInput`).

**Path `{id}`:** same constraints as the body `id`; violations → 422.

**Item** (list entries, POST and PATCH responses):

| Field | Notes |
|---|---|
| `id`, `message`, `source_area` | input |
| `status` | `pending \| classified \| failed` |
| contract fields `categoria … version_prompt` | current values; `null` unless `status = classified` |
| `provider`, `model` | who produced the classification; `null` unless classified |
| `needs_review` | computed on read: classified, not `reviewed`, and `needs_review(confianza, CONFIDENCE_THRESHOLD)` — so a threshold chosen later by Spec 03 applies to existing rows |
| `corrected` | bool, "ever corrected" (not reset if a later PATCH restores the original values) |
| `error` | failure kind or `null`: `llm_unavailable \| llm_rejected \| invalid_output \| internal_error \| busy` |
| `created_at`, `updated_at` | UTC ISO-8601 |

After a PATCH the current `version_prompt`, `provider` and `model` still name what produced the
original; `corrected = true` means the current values are (partly) human.

**ItemDetail** = Item + `original_classification` (the 10 contract fields, or `null`) +
`corrections[]` (`{previous_values, new_values, author, reason, created_at}`, oldest first).

**Mock marking (D25, G21):** `X-Pitz-Provider: mock` is set when the running provider is mock
**or** any row in the response has `provider = mock` (e.g. rows kept in the named volume after a
real key was added). Mock rows are not re-classified automatically (that would break "no second
model call"); the README says to `make down -v` to start clean.

**GET filters** (`ListQuery`, `extra="forbid"`: unknown params such as `?area=` → 422 with
`loc: ["query", <name>]`): `categoria`, `prioridad`, `area_sugerida`, `needs_review`, `status`
(no default: all statuses); `limit` 1–100 (default 20), `offset` 0–2³¹−1; order
`created_at DESC, id ASC`; filters apply to current values (G12). Invalid enum → 422. Offset past the end → empty `items`, real
`total`.

**PATCH body** (`PatchBody`, strict types — `"yes"` is not a bool): any subset of correctable
fields (G11: `categoria, prioridad, area_sugerida, idioma, resumen, requiere_info,
pregunta_seguimiento`) + `author` (1–100 chars after strip, stored stripped, required) + `reason?`
(≤ 500, stored stripped; empty → `null`). Only fields present in the body are merged
(`exclude_unset`). Explicit `null` allowed only for `pregunta_seguimiento`; `null` on any other
field → 422 with `loc` on that field (those six fields are declared as their plain type with a
`None` default, so OpenAPI shows no `null` branch for them). The correctable-field list lives once,
in `corrections.CORRECTABLE_FIELDS`. No implicit fixes: changing `requiere_info` must come with a consistent
`pregunta_seguimiento` (explicit `null` when turning it off), else the contract rule fails → 422.
Unknown or non-correctable fields (`id`, `confianza`, `version_prompt`, …) → 422.

## 5. Class diagram

```
┌──────────────────────────────┐ Depends ┌───────────────────────────────────────┐
│ api.py                       │────────►│ TriageService (one per app)           │
│ create_app(settings=None,    │         │ - db_path, classifier, threshold      │
│            adapter=None)     │         │ - pending_stale_s, queue_wait_s, clock│
│ require_api_key()            │         │ - slots: BoundedSemaphore             │
│ http_errors: handlers +      │         │ + create(req) -> (Item, created: bool)│
│   catch-all middleware       │         │ + get(id) -> ItemDetail               │
└──────────────────────────────┘         │ + list(query) -> Page                 │
                                         │ + correct(id, patch) -> Item          │
                                         └──────┬────────────────────┬───────────┘
                                                ▼ per call            ▼
                                  ┌──────────────────────────┐  ┌──────────────────┐
                                  │ Repository(db.connect()) │  │ Classifier (01)  │
                                  │ + transaction()          │  └──────────────────┘
                                  └──────────────────────────┘

Domain errors → HTTP (errors.DomainError subclasses):
 IdConflict 409 · InProgress 409 · NotFound 404 · NotClassified 409 · ContractViolation 422
 ClassificationFailed 502 · Busy 503 · DbBusy (sqlite "database is locked") 503
 ClassificationCrash / any other exception → 500 via the catch-all middleware
```

`clock` is an injectable `() -> datetime` (UTC) so tests make rows stale without waiting.

## 6. Flow — idempotent POST

```
POST {id, message, source_area}
  │ validate body (422)
  ▼
hash = sha256(original message, unstripped);  token = uuid4();  now = clock()
with repo.transaction():                         (BEGIN IMMEDIATE … COMMIT / ROLLBACK)
  row = get(id)
  ├─ none ─────────────────► INSERT status=pending, claim_token=token, created/updated=now ─┐
  ├─ hash differs ─────────► IdConflict → 409 id_conflict                                  │
  ├─ classified ───────────► return row → 200 (no model call)                              │
  ├─ pending, fresh ───────► InProgress(retry_after) → 409 in_progress                     │
  ├─ pending, stale (G16) ─► UPDATE claim_token=token, updated_at=now ─────────────────────┤
  └─ failed (G15) ─────────► UPDATE status=pending, claim_token=token, error=NULL,         │
                             updated_at=now ───────────────────────────────────────────────┤
  ▼ ◄──────────────────────────────────────────────────────────────────────────────────────┘
stale  ⇔  updated_at < now − pending_stale_s   (cutoff computed in Python, same string format)
retries classify the stored row (same id + message; stored source_area)

take model slot (≤ queue_wait_s, ≤ 2×concurrency waiters) ── no slot ──► fail(busy) → 503 busy
  ▼
classifier.classify(req)          (outside any transaction; slot released right after)
  ├─ ok ─────────────────► complete(id, token, classification, provider, model):
  │                          UPDATE … WHERE id=? AND status='pending' AND claim_token=?
  │                          → status=classified, fields, original_classification, provider,
  │                          model, error=NULL, claim_token=NULL, updated_at
  │                          "database is locked" → retry up to 3× (0.2 s, 0.4 s, 0.8 s; safe:
  │                          token-guarded) → still locked: log request_complete_failed → 503
  │                          db_busy, Retry-After = seconds until stale (row stays pending;
  │                          re-claimed after the stale window; same for a locked fail write)
  ├─ ClassificationError ► fail(id, token, kind) (same guard, updated_at) → 502 + kind
  ├─ ClassificationCrash ► fail(id, token, "internal_error") → 500
  └─ other exception ────► best-effort fail(id, token, "internal_error"); if that write fails,
                           log request_fail_write_failed (class name) → 500

complete/fail matched 0 rows (lost claim, G24): log request_claim_lost; answer from the current
row: classified → 200 · pending → 409 in_progress · failed → 502 with its kind. Never 201.
```

Every POST logs exactly one `request_outcome` event (`id`, `http_status`, `status`, `error`,
`kind`, `attempts`, `latency_ms`) — including 200 replays, 409s and `busy`; never message text. On
error paths `kind` is the `ClassificationFailed` kind (else `null`) and `status` is the row's
status when known (carried on the domain error, no extra read); `attempts` counts the billed
model calls even when `complete` raises unexpectedly. `pitz_pulse.service` is pinned at INFO like
`pitz_pulse.llm` (DEBUG when the root is DEBUG), so outcome and failure events survive
`LOG_LEVEL=WARNING` (R2.6). Per-attempt cost stays in Spec 01's `llm_call` lines. Every write sets
`updated_at`.

`PENDING_STALE_SECONDS`: when unset it defaults to the derived minimum
`(1 + INVALID_OUTPUT_RETRIES) × deadline_s + QUEUE_WAIT_S + 60` (530 s with defaults), so raising
`LLM_TIMEOUT_SECONDS` never blocks startup; an explicit value below that minimum is a startup
error naming both variables and the computed floor; a value above 604800 (7 days) is a startup
error naming the variable and the maximum. `deadline_s` is Spec 01's per-invoke budget
(`LLM_TIMEOUT_SECONDS × (1 + LLM_MAX_RETRIES) + LLM_MAX_RETRIES × 30 + 10`), checked between
attempts, not a wall-clock kill (D28): an attempt already in flight can overrun it (the httpx
read timeout resets on every received chunk). The stale window therefore does not guarantee that
a worker is dead when its row is re-claimed. **The claim token is the guarantee against
overwrite**; the residual cost — re-claiming a live worker can cause a duplicate paid call — is
accepted and listed in the README pending items. This phase also corrects the
`LLMSettings.deadline_s` docstring ("hard bound" → budget, D28).

## 7. Flow — PATCH correction

```
PATCH {fields…, author, reason?}
  ▼ validate body shape (422)
with repo.transaction():
  row = get(id) (typed) ── none ──► NotFound 404 ;  status ≠ classified ──► NotClassified 409
  merged = current contract values ⊕ present fields
  Classification.model_validate(merged) ── fail ──► ContractViolation 422 (rolled back)
  diff = fields whose value changed (typed comparison: bool vs bool)
  insert_correction(previous_values=current[diff], new_values=merged[diff], author, reason)
  update_fields(<diff>, corrected = corrected OR diff non-empty, reviewed = 1, updated_at)
──► 200
```
Empty diff = human confirmed the model output (`new_values = {}`); `corrected` stays as it was.
History JSON stores typed values (`true`, not `1`).

## 8. State diagram — request lifecycle

```
                        POST (new id)
                             │
                             ▼
                      ┌─────────────┐ classify ok ┌────────────────────┐
         ┌───────────►│   pending   │────────────►│ classified         │
         │            │ claim_token │             │ reviewed=0         │
         │            └──┬───────┬──┘             └──┬──────────┬──────┘
         │ classify err, │       │ stale:            │ PATCH     │ PATCH (diff)
         │ internal, busy│       │ next POST         │ (no diff) │
         │               ▼       │ re-claims         │           ▼
         │        ┌──────────┐   └──► pending        │    classified,
         └────────│  failed  │        (new token)    │    corrected=1
   POST again     └──────────┘                       ▼    reviewed=1
   (same id+text)                             reviewed=1  (more PATCHes → more rows)
```

## 9. Gaps, edge cases, contradictions (this spec)

- **G5** same id, different text → 409 (D8). Whitespace-only difference counts as different (no
  silent normalization of user text); documented.
- **G6** concurrent same-id POSTs → reservation; exactly one model call.
- **G11 / G12** correctable fields; filters on current values.
- **G15** failed → 502, re-POST retries; failing again stays failed (502).
- **G16 / G24** stale pending re-claim with claim token; late completion is a no-op and the late
  caller gets the current state, never 201.
- **G17** input limits, path id, offset bound, unencodable text.
- **G21** provenance per row; mock header also for stored mock rows.
- Same id + same text + different `source_area` → the same request (idempotency is id + message).
- `API_KEY` unset/empty/non-ASCII, or explicit real provider without its credential (D29) →
  startup error.
- More than `LLM_CONCURRENCY` classifications at once → waiters (≤ 2× concurrency) wait ≤ 30 s,
  then 503 `busy`; `/health` stays responsive.
- `llm_rejected` (e.g. provider 401 with a bad key) → 502 with `kind`, row keeps it.
- Settings are never serialized (no `asdict`, never in `/health`, keys `repr=False`).
- `database is locked` beyond busy timeout → 503 `db_busy`; after a paid classification,
  `complete` is retried first.
- A statement error inside a transaction always rolls back (no connection left mid-transaction).
- Concurrent PATCHes → serialized; the second's `previous_values` equal the first's `new_values`.
- Container stopped mid-call → row pending until stale; 409 carries `Retry-After`; README runbook.
- `POST /solicitudes/` (trailing slash) → 307 from Starlette; README examples use the exact path.
- `pregunta_seguimiento` length (≤ 300) is checked on the raw value, so stored text meets the
  contract; the non-blank check uses the stripped value. A model question padded past 300 chars
  is invalid output (retried once).
- Repeated query params (`?categoria=bug&categoria=datos`) take the last value.
- The 413 limit reads `Content-Length`; a chunked body without it is not bounded in the app (nginx in Spec 04 sets `client_max_body_size`).
- On crash paths `request_outcome` logs `status: null`; the row state is visible through GET and the attempts through `llm_call`.
- Late success is discarded when a re-claimer already finished (classified or failed): the G24
  guard is kept as is; the cost is one extra paid retry in a rare overload race.
- NFC and NFD forms of the same text count as different text (no normalization, as G5).
- The stored `model` is the configured alias; the actual model id is in the `llm_call` line.
- uvicorn's own access/error log format stays plain text; Spec 04a decides it.
- Bodies over 65536 bytes (`Content-Length`) → 413 before auth.
- `make web-types` (Spec 05) builds the app with `LLM_PROVIDER=mock API_KEY=web-types
  DB_PATH=<temp file>` set by the Makefile target, so it needs no secrets and touches no real DB.

## 10. Tests

| File | Cases |
|---|---|
| `test_db.py` | migrate empty DB; second run no-op; migration `CREATE TABLE ok(x); CREATE TABLE bad(` → no `ok` table, no version row, `in_transaction` False; connection created in one thread works in another; `journal_mode` is `wal`; CHECKs reject a classified row with null fields, `requiere_info=2`, `confianza=7`, invalid JSON; CHECK enum lists equal the `schema.py` StrEnums (parity) |
| `test_repository.py` | insert/reclaim/complete/fail; claim-token guard (late complete after re-claim → 0 rows); a CHECK violation inside `transaction()` leaves `in_transaction` False and a following write succeeds; typed round-trip (`requiere_info` is `bool`); reclaim/complete clear `error`; every write sets `updated_at`; filters combined incl. `status` and `needs_review`; pagination incl. offset past end; ordering tie-break |
| `test_idempotency.py` | same id+text → 1 classifier call, 201 then 200; different text → 409; pending fresh → 409 with `Retry-After`; stale (via injected clock) → re-claimed; failed → retried (201) and `error` cleared; failed retry refreshes `updated_at` so a concurrent POST gets 409; failed twice → 502 and still failed; `ClassificationCrash` → row `internal_error`, 500, re-POST retries; **two threads same id**: the fake blocks on an `Event` until the second POST has received 409, then exactly 1 call; **concurrency 2, six distinct ids against a blocking fake → at most 2 simultaneous invokes**; slot timeout (injected `queue_wait_s`) → 503 `busy`, row failed `busy`, re-POST → 201; already-classified POST never waits for a slot; lost claim (A blocked, clock advanced, B re-claims, A completes) → A gets 409/200/502 per current row, never 201, `request_claim_lost` logged; `complete` locked once → retried and classified; locked always → 503 `db_busy`; each POST logs one `request_outcome` without message text |
| `test_corrections.py` | PATCH only `categoria` on a `requiere_info=true` row → 200, history stores JSON `true`; original immutable after 2 PATCHes; history order; empty diff = confirmation sets `reviewed`, keeps `corrected`; omitted vs explicit null; `requiere_info` true→false without explicit null → 422; `{"categoria": null}` → 422; `"requiere_info": "yes"` → 422; contract rule violation → 422 `validation_error` with `fields`; PATCH on pending/failed → 409; non-correctable or unknown field → 422; blank author → 422; concurrent PATCH previous_values; `"¿Qué?"` + 300 spaces → 422 |
| `test_api.py` | 401 missing/wrong/non-ASCII key; error codes exact for 401/404/405/409/422/500/502/503; unknown route → `not_found` body; unknown query param (`?area=`) → 422; offset 2³¹ → 422; path id > 64 chars → 422; lone surrogate → 422; mock mode → `X-Pitz-Provider: mock`; stored mock row read through an app with a non-mock fake → header present and `provider = mock`; `/health` reports effective provider/model and answers while every slot is taken; `/health`, `/docs`, `/openapi.json` open; GET list filters incl. `needs_review`, `status`; `needs_review` follows a changed threshold without rewriting rows; GET 404; Item and ItemDetail keys; OpenAPI schema contains enum values; `PatchBody` shows a `null` branch only for `pregunta_seguimiento` |
| `test_app_factory.py` | `uvicorn.Config("pitz_pulse.api:create_app", factory=True).load()` with a mock env succeeds; `create_app()` without `API_KEY` → `ConfigError` naming it; `create_app()` with `adapter=None` in mock env uses `MockAdapter`; one POST writes exactly one JSON `llm_call` and one `request_outcome` line to stderr; a fake raising `ValueError("SENTINEL")` → 500 constant body and no captured log output (uvicorn loggers included) contains `SENTINEL` (`TestClient(raise_server_exceptions=False)`) |
| `test_http_limits.py` | thread limiter ≥ 64 at `LLM_CONCURRENCY=16` and ≥ 40 by default (inside `with TestClient`); body > 65536 → 413 without a key; exactly 65536 reaches auth |
| `test_outcome_log.py` | at `WARNING`, a locked `complete` still logs `request_complete_failed` and `request_outcome`; `llm_rejected` outcome carries `kind` and `status="failed"`; unexpected `complete` error logs `attempts == 1`; `DbBusy` after a reservation carries `Retry-After` = seconds until stale (service and HTTP), before any reservation 1; a lock while connecting → `DbBusy` |
| `test_read_snapshots.py` | `get` and `list` read their pairs between one `BEGIN` and `COMMIT`; unknown id rolls back; `list` inside an open transaction reuses it; no transaction left open |
| `test_review_flag.py` | confianza below / equal / above threshold via `review.needs_review` |
| `test_settings_api.py` | `API_KEY` required and ASCII; `PENDING_STALE_SECONDS` unset → derived minimum (530 with defaults; larger when `LLM_TIMEOUT_SECONDS` is raised); explicit value below the minimum → error naming both variables and the floor; above 604800 → error naming the maximum; `repr(settings)` contains no key; `LLM_PROVIDER=anthropic_api` without key → `ConfigError` naming the variable |

API tests use `create_app(settings, adapter=FakeAdapter(...))` and a temp SQLite file;
`test_app_factory.py` covers the zero-argument path.

## 11. Acceptance

`make test` green · `make lint` clean · every source file < 300 lines · manual run in mock mode
(`uvicorn --factory pitz_pulse.api:create_app`): POST twice → one `llm_call` line and two
`request_outcome` lines; PATCH then GET shows original + correction.
