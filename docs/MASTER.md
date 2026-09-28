# Pitz Pulse — Master Document

Single entry point for the project: scope, architecture, spec index, requirement traceability,
decision log, and the register of gaps / edge cases / contradictions.

- **Source of truth:** the case PDF (confidential, not in this repo). Requirements below are
  paraphrased and tagged `R<part>.<n>` / `X<n>` so every spec, test and doc can point at them.
- **Priority rule:** Parts 1–4 solid and verified before any extra (`X*`).
- **Status legend:** `draft` → `approved` → `planned` → `in progress` → `done (verified)`.
- **Reviews:** `docs/superpowers/reviews/2026-09-25-spec-review.md` (all specs: 100 raw → 76
  confirmed, applied in rev 2) · `docs/superpowers/reviews/2026-09-25-01-spec-review.md` (Spec 01
  spec gate: 5 dr-strange slices + verifier → 5 High confirmed, applied in Spec 01 rev 3) ·
  Spec 02: `docs/superpowers/reviews/2026-09-27-02-spec-review.md` (spec gate, applied in rev 5) ·
  `docs/superpowers/reviews/2026-09-27-02-plan-review.md` (plan gate, rev 5.1) ·
  `docs/superpowers/reviews/2026-09-27-02-implementation-review.md` (implementation gate; rulings
  applied in the final fix wave).

---

## 1. What we are building

An internal service that receives free-text requests (Spanish / Portuguese) written by Pitz areas,
classifies them with an LLM into a fixed JSON contract, stores them, lets people query and correct
them, and measures classifier quality against human-labeled golden sets.

Success = `docker compose up` runs the full stack; the 12 case messages are classified and promoted
to `resultados.json`; `make eval` reports per-field accuracy against `etiquetas_esperadas.json` and
the edge golden set; every production concern in the case (masking, idempotency, retries,
observability, cost) is implemented, tested without real providers, and documented.

## 2. Engineering principles (binding for every spec)

1. KISS. Explicit code over abstractions. Scalability comes from clear boundaries, not layers.
2. Boundaries: **domain** (contract, masking) · **providers** (Strategy/Adapter) · **graph**
   (LangGraph harness) · **service** · **persistence** · **HTTP API** · **web** (extra).
   Dependencies point inward only.
3. Abstractions only where multiple implementations exist: `ProviderAdapter` (anthropic_api,
   claude_agent_sdk, mock). Construction via a factory + registry, no Builder (D18).
4. **LangGraph is always the harness** (flow, state, retries, logging). No provider SDK runs its
   own agent loop (D1).
5. Source files < 300 lines (JSON, lockfiles, Markdown excluded). Split by responsibility first.
6. Minimal dependencies, each justified in the spec that introduces it.
7. Never swallow errors: fail explicitly, log with context, never log message text or credentials.
8. Every spec ships unit tests in the same phase. Tests never call a real provider.
9. Code, identifiers, comments, docs, commits, request fields: English. Case contract exceptions:
   output JSON field names and enum values, the `/solicitudes` path, `resumen` in Spanish, the 12
   test messages in their original language, the required file names.

## 3. Monorepo layout

```
pitz-pulse/
├── CLAUDE.md                     # working rules for AI assistants
├── README.md  DECISIONES.md  AI_LOG.md (owner: candidate)
├── .env.example  .gitignore  Makefile  docker-compose.yml
├── mensajes.json                 # the 12 case messages (copy explicitly allowed by the case)
├── etiquetas_esperadas.json      # case golden labels (AI-drafted, candidate-approved)
├── resultados.json               # promoted run (only `make promote` writes it)
├── resultados.meta.json          # provider/model/temperature/tokens/cost of that run
├── scripts/smoke.sh
├── docs/
│   ├── MASTER.md
│   └── superpowers/{specs,plans,reviews}/
├── apps/
│   ├── api/                      # Python 3.12 · FastAPI · LangGraph · SQLite
│   │   ├── pyproject.toml  uv.lock  Dockerfile
│   │   ├── prompts/              # v1.md, v2.md, CHANGELOG.md
│   │   ├── eval/golden/          # edge_cases.messages.json, edge_cases.labels.json
│   │   ├── eval/runs/            # <set>__<prompt>__<provider>__<model>[__<suffix>].json + .meta.json (committed)
│   │   ├── src/pitz_pulse/       # providers/ subpackage for adapters
│   │   │   └── migrations/       # 001_init.sql, 002_… (extras); packaged with the code
│   │   └── tests/
│   └── web/                      # extra (Spec 05): Vite · React · TS, nginx proxy /api
└── .github/workflows/            # extra (Spec 06a): CI
```

## 4. Global architecture

```
  curl / web UI (X5, via nginx /api proxy)          Slack Events (X1, signed)
        │ X-API-Key                                        │ HMAC signature
        ▼                                                  ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ api.py  routes · auth on /solicitudes · error handlers · /health      │
 └──────────────────────────────────┬───────────────────────────────────┘
                                    ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ service.py  TriageService (idempotency + claim token, corrections)    │
 └───────┬──────────────────────────────────────────────┬───────────────┘
         ▼                                              ▼
 ┌─────────────────┐                ┌──────────────────────────────────────┐
 │ repository.py   │                │ classifier.py → graph.py (LangGraph)  │
 │ SQLite, per-req │                │ mask → call_llm → validate → retry    │
 │ connection      │                └──────────────────┬───────────────────┘
 └─────────────────┘                                   ▼
                                    «Strategy» ProviderAdapter (factory + registry)
                                     ├─ AnthropicApiAdapter  ─► ChatAnthropic ─► Messages API (API key)
                                     ├─ ClaudeAgentSdkAdapter ─► Agent SDK as transport only (OAuth token)
                                     └─ MockAdapter           (offline)

 batch.py ─► classifier ─► eval/runs/*.json + meta      evaluate.py ◄─ runs + golden sets
 promote.py ─► resultados.json + resultados.meta.json   (refuses mock unless --allow-mock, D31)
```

## 5. Spec index and build order

| # | Spec | Covers | File | Plan | Status |
|---|------|--------|------|------|--------|
| 01 | Classification core | Part 1 | `specs/2026-09-25-01-classification-core-design.md` | pending | implemented and gate-reviewed on `feat/spec-01-classification-core`; real smoke deferred to the Pitz key (D29) |
| 02 | Service & persistence | Part 2 | `specs/2026-09-25-02-service-persistence-design.md` | `plans/2026-09-27-02-service-persistence-plan.md` (v2, plan gate applied) | implemented and gate-reviewed (PR #2) |
| 03 | Evaluation, golden sets, iteration | Part 3 | `specs/2026-09-25-03-evaluation-design.md` | `plans/2026-09-27-03-evaluation-plan.md` (v2) | implemented on feat/spec-03-evaluation (rev 5); edge labels approved 2026-09-27 |
| 04 | 04a delivery · 04b docs | Part 2 (compose) + Part 4 | `specs/2026-09-25-04-delivery-docs-design.md` | `plans/2026-09-27-04-delivery-docs-plan.md` (fast mode) | implemented on feat/spec-04-delivery-docs; final review applied |
| 05 | Review web UI (extra) | X5 | `specs/2026-09-25-05-review-web-ui-design.md` | pending | draft rev 2 |
| 06 | Extras: CI, compare, duplicates, Slack | X1–X4 | `specs/2026-09-25-06-extras-design.md` | pending | draft rev 2 |

```
01 core ──► [etiquetas_esperadas.json approved ✔ · edge labels approved · ANTHROPIC_API_KEY in .env]
   │
   ▼
02 service ──► 04a delivery (Docker, compose, Makefile, smoke)
                    │
                    ▼
               03 evaluation + iteration + promote ──► 04b docs (README, DECISIONES)
                    │
      Parts 1–4 verified
                    ▼
      06a CI ──► 05 web UI ──► 06b compare ──► 06c duplicates ──► 06d Slack
```

Per spec: approve spec → write plan → approve plan → implement with tests → run verification →
show real output → propose commit(s) → candidate approves.

## 6. Requirement traceability

| ID | Requirement (paraphrased) | Spec | Verified by |
|----|---------------------------|------|-------------|
| R1.1 | Output object with the 10 contract fields and exact enum values | 01 | `test_schema.py` |
| R1.2 | Priority follows the case rubric | 01, 03 | prompt v1 + eval `prioridad` accuracy |
| R1.3 | Real LLM integration + documented mock mode; temperature = 0 | 01, 03, 04 | `test_anthropic_adapter.py`, `test_agent_sdk_adapter.py`, `test_mock_adapter.py`, `test_models_catalog.py`, `test_config.py`, `test_promote.py` (temperature 0 enforced), README |
| R1.4 | Structured output via tool use, validated against a schema | 01 | `test_tool_schema.py`, `test_graph.py`, real smoke call |
| R1.5 | Timeouts, invalid responses, rate limits: retries with backoff | 01 | `test_anthropic_adapter.py` (SDK config + mapping), `test_graph.py` (feedback retry) |
| R1.6 | Whole batch with bounded concurrency | 01 | `test_batch.py`, `test_runs.py` |
| R1.7 | Mask CNPJ/RFC, emails, phones before the LLM; document coverage | 01, 04b | `test_masking.py`, DECISIONES |
| R1.8 | Prompt versioned and outside business logic | 01 | `prompts/`, `test_prompts.py` |
| R1.9 | `resultados.json` with the 12 messages committed | 03 | `make promote`, `test_promote.py` |
| R1.10 | `confianza` has a real use | 01, 02, 03 | `needs_review` + queue filter, threshold sweep in eval |
| R2.1 | POST, GET list (filters + pagination), GET by id | 02 | `test_api.py` |
| R2.2 | POST idempotent: no duplicate, no second model call | 02 | `test_idempotency.py` |
| R2.3 | PATCH keeps original + correction | 02 | `test_corrections.py` |
| R2.4 | API key auth in a header | 02 | `test_api.py` |
| R2.5 | SQLite with schema/migrations in repo | 02 | `migrations/`, `test_db.py` |
| R2.6 | Structured log per model call: latency, tokens, estimated cost | 01 | `test_graph.py` (log per attempt), sample in README |
| R2.7 | Full stack with `docker compose up` | 04a | verification §7 with output in phase review |
| R3.1 | `etiquetas_esperadas.json` with justifications for doubtful cases | 03 (candidate) | `test_evaluate_cli.py`, `test_golden_files.py` |
| R3.2 | `make eval`: per-field accuracy + failing messages | 03 | `test_scoring.py`, real runs |
| R3.3 | README: last run + ≥ 1 prompt iteration and its effect | 03, 04b | README + `prompts/CHANGELOG.md` |
| R4.1 | Tests: schema, masking, idempotency, main endpoints; no real API | 01, 02 | `make test` (+ CI 06a) |
| R4.2 | `DECISIONES.md` ≤ 2 pages answering the 6 topics | 04b | page / word check |
| R4.3 | `AI_LOG.md` with 3–5 concrete examples | candidate | — |
| R4.4 | README: run, test, eval, stack and why, assumptions, pending | 04b | review |
| R4.5 | `.env.example` without real values | 04a | review |
| R4.6 | Real commit history | all | `git log` |
| R4.7 | (desirable) link to AI conversations in README | candidate | — |
| X1 | Slack Events endpoint, signature verification, reply in thread | 06d | signed-fixture tests |
| X2 | CI running tests + linter | 06a | green run |
| X3 | Duplicate / very similar request detection | 06c | `test_duplicates.py` |
| X4 | Caching or cheaper model, cost vs quality | 06b | comparison table from real runs |
| X5 | Minimal web view to review and correct | 05 | vitest + manual run |

## 7. Decision log

| ID | Decision | Why | Status |
|----|----------|-----|--------|
| D1 | **LangGraph is the harness**: graph `mask → call_llm → validate → retry`; provider SDKs are transports only | Candidate decision: provider portability and a graph that can grow (e.g. multi-turn clarification). The feedback-retry loop is a real cycle. Agent SDK as harness rejected: its loop/tools/`.claude` loading add non-deterministic, filesystem-capable behavior | approved |
| D2 | Default model `claude-haiku-4-5`, `LLM_TEMPERATURE` via env (default 0, empty = omit); compare against `claude-sonnet-4-6` and `claude-sonnet-5` | Case requires temperature 0; Sonnet 5 / Opus 5+ reject temperature (400) → startup error if configured, never silently dropped | approved |
| D3 | Forced tool call + `strict` where supported; tool schema stripped of keywords strict rejects; Pydantic re-validates | Schema-valid arguments; constraints JSON Schema/strict can't express stay in Pydantic (G23) | approved |
| D4 | Transport retries inside the SDK (via adapter); graph retries only invalid output, with errors fed back (amended by D4a) | At temperature 0 an identical retry repeats the same invalid output | approved |
| D5 | Sync code: FastAPI `def` routes, per-request SQLite connection, `ThreadPoolExecutor` batch. Exception: Slack route is `async` (raw body) | One code path, bounded concurrency, no event-loop blocking | approved |
| D6 | stdlib `sqlite3` + plain SQL migrations with an atomic runner | No ORM/Alembic for 2 tables | approved |
| D7 | Idempotency: `BEGIN IMMEDIATE` reservation row + message hash + claim token | Race-safe, records failures, late workers become no-ops (G24) | approved |
| D8 | Same id with different text → 409 | Returning another message's classification is a data bug | approved |
| D9 | DB stores original text; only the LLM egress is masked; logs never contain text | Reviewers need the full text; DB is internal; residual risk documented | approved |
| D10 | Masking: email, CNPJ (numeric + 2026 alphanumeric), CPF, CURP, RFC, phones BR/MX | Case minimum + personal IDs of both countries (LGPD / LFPDPPP) | approved |
| D11 | Request fields in English: `id`, `message`, `source_area` | Candidate rule | approved |
| D12 | `confianza` < `CONFIDENCE_THRESHOLD` → `needs_review`; queue filter; threshold chosen from the eval sweep (placeholder 0.7 until a real run, G35) | Measurable product use of confidence | approved |
| D13 | Monorepo `apps/api` + `apps/web`; web after Parts 1–4 | Candidate requirement + case priority | approved |
| D14 | No golden message is ever used as a prompt example (tested) | Leaking eval data inflates accuracy | approved |
| D15 | `version_prompt` set by code from the prompt file | Model could hallucinate it | approved |
| D16 | `pregunta_seguimiento` in the message language; `resumen` always Spanish | Candidate decision; case mandates Spanish only for `resumen` | approved |
| D17 | Endpoint path stays `/solicitudes` | Case contract; evaluators test that path | approved |
| D18 | Providers as Strategy/Adapter (`anthropic_api` API key · `claude_agent_sdk` OAuth token · `mock`), factory + registry, model catalog with capabilities and prices | Swap providers by config; capabilities differ per model. Builder rejected (one-step construction). Candidate asserts the OAuth path works; validated at the first real run | approved |
| D19 | Batch writes only `eval/runs/`; `make promote` is the only writer of `resultados.json` (+ meta), refuses mock unless `--allow-mock` (D31) | Prevents mock/compare runs from overwriting the deliverable | approved |
| D20 | Two model-agnostic golden sets: case (assistant-drafted, candidate-approved labels) + edge (assistant-drafted messages, candidate-approved labels) | Regression guard against overfitting 12 messages | approved |
| D21 | Config split: `LLMSettings` (batch/eval/API) and `ApiSettings` (API only: `API_KEY`, `DB_PATH`, `PENDING_STALE_SECONDS`); `ApiSettings` **composes** `LLMSettings` (`settings.llm`), never inherits or serializes it | CLIs must not require the HTTP API key; inheritance + `asdict` would copy secrets and re-run catalog checks | approved |
| D22 | Web served by nginx proxying `/api` → api (no CORS); compose profile `web` | One origin; web failure never blocks the core stack | approved |
| D23 | Official `resultados.json` only from `anthropic_api` at temperature 0 (mock only with `--allow-mock`, D31); promote also enforces set = case, the 12 ids, no failures, hashes and full contract | Case requires temperature 0; the Agent SDK cannot set it. Agent SDK = development provider | approved |
| D24 | Agent SDK adapter uses `claude-agent-sdk` directly (no LangChain wrapper), plain-JSON reply validated by the graph, full isolation options, explicit subprocess env, deadline budget (checked between attempts; see D28), concurrency semaphore | The SDK has no forced tool choice and `output_format` hides a retry loop (conflicts with D4); wrappers lack isolation options | approved |
| D25 | Provider unset + exactly one credential → that provider (logged); mock responses carry `X-Pitz-Provider: mock` | Evaluators who only set their key must not silently get mock output | approved |
| D26 | Run stems include the golden set; no overwrite without `FORCE`; meta carries prompt/input/results hashes; `eval/runs/` committed after every real run | Evidence for the README iteration story cannot be silently destroyed or mismatched | approved |
| D4a | Amendment to D4: the Anthropic adapter owns all of its transport retries itself (`ChatAnthropic(max_retries=0)`), capping every `retry-after` wait at 30 s; the per-invoke deadline budget (checked between attempts, not a wall-clock kill — D28) is `LLM_TIMEOUT_SECONDS × (1 + LLM_MAX_RETRIES) + LLM_MAX_RETRIES × 30 + 10` (220 s with defaults) | The underlying SDK would otherwise honor an unbounded server `retry-after`, which could blow any deadline; only an adapter-owned retry loop can cap it (G33) | approved |
| D27 | Execution rulings confirmed while implementing Spec 01 (this phase): tracing is disabled with the four LangSmith/LangChain env vars plus `langsmith.utils.get_env_var.cache_clear()` and `tracing_context(enabled=False)` around every graph run — `run_trees.configure(enabled=False)` is deliberately not used because it leaks process-global state; masking guards are bounded to plausible amounts/dates and the separated-RFC and bare-phone rules are keyword/case gated as documented in Spec 01 §8.6; `ClaudeAgentSdkAdapter` rejects at construction any config whose deadline cannot fit one attempt plus SDK cleanup; batch uses a sliding concurrency window that stops submitting after the first `llm_rejected` and records never-submitted items as `cancelled` (stop rule narrowed by D28) | Verified while implementing and testing Spec 01; specs and this document are updated to match rather than left describing intent that diverged from the shipped behavior | approved |
| D28 | Spec 01 implementation-gate rulings: (a) the per-invoke deadline is a best-effort budget checked between attempts; an in-flight Anthropic attempt is bounded only by the httpx per-phase timeouts and Spec 02's claim token is the safety net; (b) the batch stops only on credential rejections (401/403, `authentication_failed`, `billing_error`, CLI not found) — other `llm_rejected` items are recorded and the batch continues; (c) run meta records `tool_schema_sha256` (Spec 03 promote check deferred to Spec 03); (d) masking strips Unicode `Cf` before NFKC and keeps prompt-only over-masking (`número`, keyword + year list, separated alphanumeric codes as `[CNPJ]`, area-coded `NNNN-NNNN` without a keyword); the exact CPF shape wins over the IPv4 guard; (e) a bare `Exception` from the Agent SDK query maps to `unavailable` / `SDKControlError`; (f) `tool_schema_sha256` is null when the provider does not send the tool | (a) a wall-clock kill of an in-flight httpx call needs threads the plan removed on purpose — cost: one attempt can overrun by up to one per-phase timeout; (b) one oversized edge message must not kill a 12+N run — cost: a systemic 400 runs the whole set (4xx are not billed) and the all-rejected summary names the error types; (c) tool descriptions carry rubric text outside `prompt_sha256`; (d) D14 | approved |
| D29 | The assistant makes no real model call and uses no personal key. `.env` defaults to `LLM_PROVIDER=anthropic_api` with an empty `ANTHROPIC_API_KEY` (fails fast with `ConfigError`, never silent mock); `LLM_PROVIDER=mock` is the development/test provider (same graph, contract validation and error types; no external calls). README explains how to activate the real integration with Pitz's key; live acceptance of the strict tool schema (G23, Spec 01 §12) is verified by whoever runs it with that key | Candidate decision 2026-09-27: no spend on personal credentials | approved |
| D30 | Spec 02 spec-gate rulings: zero-arg `create_app()` factory (uvicorn `--factory`); one app-scoped `TriageService` owning the model-slot semaphore (≤ 2× waiters, 30 s, then 503 `busy`), per-call connections with `check_same_thread=False`; `repository.transaction()` always rolls back; migrations split statements (no `executescript`); rows store `provider`/`model` and the mock header also marks stored mock rows (no auto re-classification); lost claim answers with the current row state, never 201; `complete` retried on lock; `needs_review` computed on read from a `reviewed` flag and a shared `review.needs_review`; `PENDING_STALE_SECONDS` defaults to the derived minimum; catch-all middleware logs class names only and never re-raises; unknown query params → 422; exact error-code table; `/health` async (exception to D5); `.env.example` sets `LLM_PROVIDER=anthropic_api` with an empty key while no `.env` still auto-selects mock (D29 scope) | Each closes a verified defect or contradiction from `docs/superpowers/reviews/2026-09-27-02-spec-review.md` | approved |
| D31 | `resultados.json` is produced from a **mock** case run via `promote --allow-mock` (meta `mock: true`); `claude_agent_sdk` stays non-promotable; the README gives the exact commands to regenerate it and to run the real v1 → v2 iteration with Pitz's key; no v2 prompt is written from mock results. Edge-set labels are drafted by the assistant and approved by the candidate at the end of Spec 03 | D29 forbids real calls; the deliverable must exist and be honest about its provenance | approved |

## 8. Gaps, edge cases and contradictions register

| ID | Finding | Type | Resolution | Spec |
|----|---------|------|------------|------|
| G1 | Annex B shows `requiere_info: true` for an actionable bug | ambiguity (Annex B is orientative, a format reference — not ground truth) | Our criterion "team cannot start without asking" is defined in the prompt and documented; candidate's labels decide | 01, 03 |
| G2 | `confianza` is self-reported, often poorly calibrated | gap | Per-message table + threshold sweep | 03 |
| G3 | Input "area that writes" not in output contract | gap | Optional `source_area`, context for the model | 01, 02 |
| G4 | Temperature rejected by Sonnet 5 / Opus 5+ and not exposed by the Agent SDK | contradiction | D2 catalog + startup error; meta records effective temperature | 01 |
| G5 | Same id, different text undefined | gap | 409 | 02 |
| G6 | Concurrent same-id POSTs | edge case | Reservation; one model call; threaded test | 02 |
| G7 | 20-word limit not expressible in JSON Schema | gap | Pydantic + feedback retry | 01 |
| G8 | Language of `pregunta_seguimiento` unspecified | gap | D16 | 01 |
| G9 | Masking false positives (dates, amounts, `error 500`) | edge case | Tightened phone rule + negative fixtures; documented over-masking | 01 |
| G10 | No real PII in the 12 messages | gap | Synthetic fixtures + edge golden set | 01, 03 |
| G11 | Correctable fields unspecified | gap | Listed in 02 §4 | 02 |
| G12 | Filters on original or corrected values | gap | Current values | 02 |
| G13 | Caching minimum prefix (Haiku 4.5: 4096 tokens) | constraint | Measured prefix in 06b; no padding | 06 |
| G14 | Monthly cost needs real token counts | gap | From run metas incl. retries; labeled estimate otherwise | 04b |
| G15 | Classification fails after retries | edge case | Row failed, 502, re-POST retries | 02 |
| G16 | Process dies while pending | edge case | Stale re-claim after derived minimum | 02 |
| G17 | Empty / huge messages, malformed ids | edge case | 422 limits | 01, 02 |
| G18 | Prompt injection / delimiter break-out | risk | Delimiters + escaping + data instruction | 01 |
| G19 | Temperature 0 not fully deterministic | risk | Noise floor from repeated run | 03 |
| G20 | 12 labels → overfitting | risk | Edge golden set; future set from corrections | 03 |
| G21 | Mock output mistaken for model output | risk | Meta provider; mock header; promote refuses mock unless `--allow-mock` (D31) | 03 |
| G22 | Compose must run without `.env` | edge case | `${VAR:-default}` → mock + dev key | 04a |
| G23 | Strict tool schema rejects min/max/length keywords (every call would 400) | feasibility | `build_tool_schema` strips them; test + real smoke call | 01 |
| G24 | Stale re-claim lets a late worker overwrite a newer result | edge case | Claim token guard on complete/fail | 02 |
| G25 | Every classify overwrote `resultados.json` (incl. mock/compare runs) | cross-spec | D19 run files + promote | 01, 03, 06 |
| G26 | Slack cannot send `X-API-Key` | cross-spec | Auth scoped to `/solicitudes`; Slack uses signature | 02, 06 |
| G27 | Docs needed eval numbers but were scheduled before eval | cross-spec | Spec 04 split 04a/04b | 04 |
| G28 | Rule checks unreachable if results are contract-validated first | contradiction | Structural validation in eval | 03 |
| G29 | Agent SDK used as harness would load tools/`.claude` config | risk | Transport-only adapter options, tested | 01 |
| G30 | "Empty = omit" temperature contradicted "empty = unset" and compose `:-` | contradiction | Three-state `LLM_TEMPERATURE` (unset → 0, `none` → omit); compose `${LLM_TEMPERATURE-0}` | 01, 04a |
| G31 | LangSmith tracing (transitive dependency) could upload raw state | risk | Mask before the graph; tracing forced off | 01 |
| G32 | Agent SDK CLI inherits env and persists transcripts | risk | Explicit env, persistence off, isolation options tested | 01 |
| G33 | No hard per-invoke deadline (uncapped retry-after, CLI defaults) | edge case | Deadline budget in every adapter, checked between attempts (in-flight attempt bounded by per-phase timeouts, D28); Spec 02 stale window + claim token cover overruns | 01, 02 |
| G34 | Masking missed common real formats and over-masked dates/IPs/amounts | edge case | Widened patterns + guards + fixtures (Spec 01 §8.6) | 01 |
| G35 | No real run under D29: no measured prompt iteration (R3.3), no sweep-derived threshold (D12), no calibration evidence (R1.10) | gap | Tooling + README commands for Pitz's key; 0.7 disclosed as placeholder; mock sweep never shown as evidence | 03, 04b |

## 9. Open items (blocking)

1. ~~`etiquetas_esperadas.json`~~ approved 2026-09-25 (AI-drafted, candidate-approved; disclosed in README/AI_LOG).
2. ~~Edge-set labels~~ approved 2026-09-27 (assistant-drafted, candidate-approved; EDGE-15/16 changed by the candidate).
3. No real key in this repo's development (D29). `resultados.json` comes from a marked mock run (D31); real runs need Pitz's `ANTHROPIC_API_KEY`.
4. ~~Mock `resultados.json` disclosure~~ done: README opens with it (Spec 04b). Merge PRs #1–#4 in order.

## 10. Delivery checklist (maps to the case deliverables)

- [x] Real commit history, conventional messages
- [x] `README.md`: compose, tests, eval, stack + why, assumptions, last eval run (mock, G35), prompt iteration protocol, pending items
- [x] `resultados.json` + meta (promoted from a **mock** run, `mock: true`, D31; real regeneration documented)
- [x] `etiquetas_esperadas.json` (approved) — mock eval result in README; real one pending (G35)
- [x] `DECISIONES.md` ≤ 2 pages, six topics
- [ ] `AI_LOG.md` (candidate)
- [x] `.env.example` with no real values
- [x] `docker compose up` verified from a clean clone (smoke OK)
- [x] `make test` and `make eval` verified (672 passed; mock eval in README and review reports)
