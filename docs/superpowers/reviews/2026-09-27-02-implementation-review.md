# Spec 02 implementation-gate review — 2026-09-27

Target: branch `feat/spec-02-service-persistence`, `f58b98a..4734544` (11 commits, 9 plan tasks,
one task fix round). Workers: 3 dr-strange (POST/idempotency/concurrency · HTTP contract, PATCH,
errors · cross-cutting and merge readiness). All three were cut by a usage limit once and resumed
with their context. Medium findings were re-executed by the orchestrator before acceptance.

## Tool suite (HEAD 4734544)

```
uv run pytest            544 passed
ruff format --check      66 files already formatted
ruff check               All checks passed!
files >= 300 lines       none (largest: config.py 220, repository.py 212, intake.py 197)
```
Every intermediate commit passes its own tests and lint (checked in scratch worktrees:
387 → 414 → 434 → 455 → 486 → 488 → 500 → 514 → 544). The wheel contains
`pitz_pulse/migrations/001_init.sql`.

## Review summary

No High findings. The idempotency core holds: `BEGIN IMMEDIATE` reservation, the
`status='pending' AND claim_token=?` guard, lost claims that never answer 201, atomic migrations,
a real held lock turning into `DbBusy`. The contract is intact (10 fields, enum values,
`/solicitudes`, non-correctable `id`/`confianza`/`version_prompt`), and a real `uvicorn --factory`
run with marker strings in message, source_area, reason, bad JSON and unknown fields leaked none
of them to stdout or stderr. There is no interface drift against Spec 01 as built.

## Findings (deduped, severity-sorted)

| # | Sev | Workers | Finding | Resolution | Verified |
|---|---|---|---|---|---|
| I1 | Med | all 3 | `LLM_CONCURRENCY` ≥ 14 → slots + waiters exceed anyio's 40 threads; sync routes (401 included) hang instead of 503 `busy` | Raise the thread limiter at startup; async auth dependency | live uvicorn repro; limiter measured 40 |
| I2 | Med | contract | `pregunta_seguimiento` length checked after strip → 50 005-char value stored | Raw-length rule | re-executed |
| I3 | Med | contract | `PENDING_STALE_SECONDS` unbounded → `OverflowError` 500 | Max 7 days | re-executed |
| I4 | Med | flow, cross | `pitz_pulse.service` not pinned; failure events vanish at `LOG_LEVEL=WARNING` | Pin at INFO | re-executed |
| I5 | Med | flow, cross | `request_outcome` loses kind/status on errors; `attempts=0` after a paid call | Carry kind, status, attempts | probe |
| I6 | Med | flow | `db_busy` after reservation says `Retry-After: 1`, then 409 for ~530 s | Retry-After = seconds until stale | probe |
| I7 | Med | contract | OpenAPI shows non-nullable PATCH fields as nullable (wrong web types) | Drop the null branch | openapi dump |
| I8 | Med | contract | Request bodies read with no size limit, before auth | 413 above 64 KB | inferred |
| I9 | Med | flow | Late success discarded when the re-claimer already failed | Kept (G24 guard unchanged); documented | test asserts it |
| L | Low | all | Duplicate correctable-field lists; direct `starlette` import; `db.connect` outside the `DbBusy` translation; non-snapshot read pairs; docs drift (MASTER migrations path, reviews list, spec status pointers) | Fixed | code citation |
| L | Low | all | Repeated query params last-wins; uvicorn plain-text logs; stored `model` is the configured alias; NFC/NFD counted as different text; mock header on error answers about stored mock rows; `API_KEY` min length; `3f109b2` has no body | Documented / deferred (04a, README) | — |

## Coverage ledger

| Dimension | flow | contract | cross |
|---|---|---|---|
| contract fidelity | ✔ | ✔ | ✔ |
| idempotency | ✔ | ✔ | ✔ |
| concurrency | ✔ | ✔ | ✔ |
| failure / retries | ✔ | ✔ | ✔ |
| provider boundary | ✔ | – | ✔ |
| privacy & logging | ✔ | ✔ | ✔ |
| auth | – | ✔ | ✔ |
| data fidelity | ✔ | ✔ | ✔ |
| migrations | ✔ | – | ✔ |
| delivery | – | ✔ | ✔ |
| cost | ✔ | – | ✔ |
| engineering rules / history | ✔ | ✔ | ✔ |

Not reviewed: eval integrity (Spec 03). Real-provider behavior is out of scope (D29).

## Outcome

One fix wave (items A–D of the fix brief) and a scoped re-review; results appended below.

### Fix wave and re-review

Fix wave `4734544..489b291` (4 commits). Orchestrator re-ran the verified findings at `489b291`:
padded question rejected; `PENDING_STALE_SECONDS=10**14` → `ConfigError`; `pitz_pulse.service`
stays at INFO under `LOG_LEVEL=WARNING`; thread limiter 64 at `LLM_CONCURRENCY=16`; 70 000-byte
body → 413. Suite: 568 passed, ruff clean, no file ≥ 300 lines.

Scoped re-review (1 dr-strange): 12/14 items ADDRESSED, 2 PARTIAL, no regression.
- Item 9 PARTIAL — crash paths log `request_outcome` with `status: null`. Accepted, documented
  in Spec 02 §9 (row state via GET, attempts via `llm_call`).
- Items 13/14 PARTIAL — this report was untracked; committed with this wave.
- Chunked bodies without `Content-Length` are not bounded in the app → Spec 02 §9; nginx in
  Spec 04. Lifespan must stay on (thread limiter) → noted in Spec 04 §1.

Final state: implementation gate closed for Spec 02; no open High or Medium findings.
