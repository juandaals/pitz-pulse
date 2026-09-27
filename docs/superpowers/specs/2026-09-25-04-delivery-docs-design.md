# Spec 04 — Delivery, ops & docs

Split in two phases because the docs need Spec 03's numbers:
- **04a Delivery** (before Spec 03): Dockerfile, compose, Makefile, env inventory, smoke.
- **04b Docs** (after Spec 03): README, DECISIONES.

- **Covers:** R2.7, R4.2, R4.4, R4.5; supports R1.3 (mock docs), R1.7 (masking docs)
- **Depends on:** 04a → Specs 01–02 · 04b → Spec 03
- **Status:** draft (rev 3 — aligned with Spec 01 rev 3)

---

## 04a — Delivery

### 1. Components

| File | Responsibility |
|---|---|
| `apps/api/Dockerfile` | `python:3.12-slim`; `COPY --from=ghcr.io/astral-sh/uv` ; copy `pyproject.toml uv.lock src/ prompts/ migrations/`; `uv sync --frozen --no-dev`; `useradd -m app` (writable `HOME`, needed by the Agent SDK CLI); `mkdir -p /data && chown app:app /data`; `USER app`; `uvicorn --factory pitz_pulse.api:create_app --workers 1` (lifespan must stay on: it raises the thread limiter, Spec 02 §2) |
| `docker-compose.yml` | `api` service; `environment:` with `${VAR:-default}` for every variable except `LLM_PROVIDER` (`${LLM_PROVIDER-}`: unset stays unset so auto-selection works) and `LLM_TEMPERATURE` (`${LLM_TEMPERATURE-0}`: `none` must survive); named volume `pitz-data:/data`; port `${API_PORT:-8000}`; healthcheck via Python `urllib` (no curl in slim) with `start_period`; `web` service under profile `web` (Spec 05) |
| `Makefile` | `-include .env` + `export`; repo-root path variables made absolute with `$(abspath …)`; targets below |
| `.env.example` | full inventory (§3), safe defaults, empty secrets |
| `scripts/smoke.sh` | end-to-end against a running stack; unique id per run; `API_URL`, `API_KEY` from env |

Make targets: `install test lint classify eval compare promote up down smoke web-types`.
`classify`/`eval`/`compare`/`promote` run through `uv --directory apps/api run …` with absolute paths;
`classify` prints provider/model/temperature and the call count before a paid run.
Prerequisites for non-Docker targets: `make`, `uv` (uv installs Python 3.12). Documented in README.

All providers run inside Docker, selected only by env (`mock`, `anthropic_api`, `claude_agent_sdk`,
future ones). For `claude_agent_sdk` the image ships the Claude Code CLI the SDK drives: bundled by
the Python SDK package if the plan's verification confirms it, otherwise installed at build time.
The container runs it as the non-root `app` user with `CLAUDE_CODE_OAUTH_TOKEN` from the env; the
compose verification (§7) covers this provider too.

### 2. Flow — `docker compose up`

```
docker compose up
   │  environment: ${VAR:-default}  (no .env needed; no credential → mock, API_KEY=dev-local-key;
   │   only ANTHROPIC_API_KEY set → anthropic_api auto-selected, Spec 01 §8.2)
   ▼
build api image (uv.lock frozen)
   ▼
start ──► create_app() → parse_api_settings(env) ── invalid ──► explicit log line, exit 1 (container stops)
   │        provider=mock → WARNING "mock mode"; credential set but provider=mock → WARNING
   ▼
migrate(/data/pitz_pulse.db) ── error ──► exit 1
   ▼
uvicorn :8000 ──► healthcheck GET /health ──► healthy; /health shows provider/model/prompt_version
```

### 3. Environment inventory (single source; `.env.example` mirrors it)

| Variable | Owner | Code default | Compose default | Required |
|---|---|---|---|---|
| `LLM_PROVIDER` | 01 | unset → auto-selection | passed only if set | no (`.env.example`: `anthropic_api`, D29) |
| `LLM_MODEL` | 01 | `claude-haiku-4-5` | same | no |
| `LLM_TEMPERATURE` | 01 | unset → `0`; `none` = not sent | `${LLM_TEMPERATURE-0}` | no |
| `PROMPT_VERSION` | 01 | active version | same (test enforces equality with code and `.env.example`) | no |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` / `INVALID_OUTPUT_RETRIES` | 01 | 30 / 3 / 1 | same | no |
| `LLM_CONCURRENCY` | 01 | 4 | 4 | no |
| `CONFIDENCE_THRESHOLD` | 01 | 0.7 | 0.7 | no |
| `LOG_LEVEL` | 01 | `INFO` | `INFO` | no |
| `ANTHROPIC_API_KEY` | 01 | — | empty | iff `anthropic_api` |
| `CLAUDE_CODE_OAUTH_TOKEN` | 01 | — | empty | iff `claude_agent_sdk` |
| `API_KEY` | 02 | — | `dev-local-key` | yes (API only) |
| `DB_PATH` | 02 | `<APP_ROOT>/data/pitz_pulse.db` (never the cwd) | `/data/pitz_pulse.db` | no |
| `PENDING_STALE_SECONDS` | 02 | unset → derived minimum (530 with defaults) | unset | no (explicit value ≥ derived minimum, Spec 02 §6) |
| `API_PORT` | 04 | — | 8000 | no |
| `SLACK_SIGNING_SECRET` / `SLACK_BOT_TOKEN` | 06d | — | empty | no (feature off when empty) |
| `DUPLICATE_THRESHOLD` | 06c | 0.85 | 0.85 | no |
| `SLACK_CHANNEL_AREAS` | 06d | — | empty | no |
| `APP_ROOT` | 01 | package-relative | `/app` | no |
| `LANGSMITH_TRACING` / `LANGSMITH_TRACING_V2` / `LANGCHAIN_TRACING` / `LANGCHAIN_TRACING_V2` | 01 | forced `false` in-process | not passed | — (never enable) |

Ranges and forbidden variables are defined in Spec 01 §8.2 / §8.8.

Empty string = unset everywhere except `LLM_TEMPERATURE` (Spec 01 §8.4). Any spec adding a variable
updates this table in the same change. `test_env_inventory.py` asserts the code default, compose
default and `.env.example` agree for `PROMPT_VERSION` and that every inventory variable appears in
`.env.example`. Two entries in `.env.example` deliberately diverge from the compose default shown
above: `APP_ROOT` is left empty (it is a Docker-only override — the code default already resolves
correctly for local/non-Docker runs), and `LLM_TEMPERATURE=0` is written out explicitly (documents
the default of the three-state variable instead of leaving it to inference). A third divergence is
deliberate (D29): `.env.example` sets `LLM_PROVIDER=anthropic_api` with an empty
`ANTHROPIC_API_KEY`, so `cp .env.example .env` without a key fails fast naming the variable; the
README says to paste Pitz's key there, or set `LLM_PROVIDER=mock`. With no `.env` at all compose
leaves `LLM_PROVIDER` unset and auto-selection picks mock (marked, D25), so a clean clone still
starts.

### 4. Module view

```
Makefile ─► uv run pytest | ruff | python -m pitz_pulse.{batch,evaluate,promote}
        └─► docker compose up|down ─► Dockerfile ─► pitz_pulse.api:create_app() (factory, reads ApiSettings)
                                                        ├─ db.migrate()
                                                        └─ build_adapter(settings)  (mock | anthropic_api | claude_agent_sdk)
```

### 5. State diagram — service startup

```
┌──────────┐ settings ok ┌───────────┐ migrations ok ┌─────────┐ /health 200 ┌─────────┐
│ starting │────────────►│ migrating │──────────────►│ serving │────────────►│ healthy │
└────┬─────┘             └─────┬─────┘               └─────────┘             └─────────┘
     │ settings error          │ migration error
     ▼                         ▼
┌─────────────────────────────────┐
│ exited(1) — explicit log line   │
└─────────────────────────────────┘
```

### 6. Edge cases

- **G22** runs without `.env` → mock + dev key; README warns the dev key is local-only.
- Named volume (not bind mount) avoids SQLite locking issues on macOS; `/data` owned by `app`.
- Smoke is re-runnable against a persistent volume (unique id `SMOKE-<epoch>`).
- `test` never loads `.env` (autouse fixture clears env, Spec 01 §11).

### 7. Verification (evidence shown in the phase review)

1. `docker compose down -v && docker compose up --build` → healthy; `make smoke` passes.
2. For each provider (`anthropic_api`, `claude_agent_sdk`): with a dummy credential the container
   starts and `/health` shows the provider. No real POST is made during development (D29): the
   README gives the exact command for whoever activates the integration with Pitz's key.
3. `docker compose down -v`.
Test: `test_app_factory.py` — refuses to start without `API_KEY`; mock selected by default.

---

## 04b — Docs

### 8. README outline
1. What it is · 2. Quick start (`docker compose up`, curl examples; how to switch to a real
provider — set `LLM_PROVIDER` **and** its credential) · 3. Providers (API key, OAuth via Agent SDK,
mock: what each is and is not) · 4. Prerequisites + test / lint / classify / eval / promote ·
5. Stack and why (D1–D6, D18) · 6. Assumptions (from MASTER register) · 7. Last eval report + prompt
iteration + threshold choice · 8. Sample `llm_call` log line · 9. Pending items, why, how ·
10. Link to AI conversation (candidate).

### 9. DECISIONES.md (one criterion: ≤ 2 rendered A4 pages; prose ≤ 900 words excluding the diagram)
1. Prompt design and discarded alternatives (few-shot from golden sets, free-text JSON, single call
   without graph, Agent SDK as the only path).
2. `confianza`: review flag, threshold from the sweep, calibration caveat.
3. Masking: coverage, residual risks (original text in DB, over-masking), provider considerations
   (retention, region, no-training terms, key scoping; subscription token terms).
4. Cost per message and per month at 500 and 50,000 — mean over logged calls **including retries**,
   with the retry rate, × dated prices. Without a real run: estimate from the rendered prompt + tool
   size and an assumed output size, labeled as an estimate. What changes per scale (50k: Batch API
   for non-urgent, dedup, caching once the prefix passes the model minimum, cheaper routing).
5. Slack production architecture:
```
Slack ─event─► /slack/events ─ verify signature ─ ignore bots/subtypes/thread replies
                    │ url_verification → challenge
                    ▼
      background task scheduled ─► 200 OK (< 3 s)
                    ▼
      task ─► app's TriageService.create(id = "slack-" + event_id)
              (idempotent: Slack retries are no-ops)
                    ▼
      chat.postMessage(thread_ts) ─► reply in thread
```
6. Two more weeks: eval set from corrections, multi-turn clarification in the graph, MCP server over
   the API, Postgres + real queue, SSO for reviewers, more provider adapters.

### 10. Acceptance
Clean clone → `docker compose up` → `make smoke` passes; README and DECISIONES reviewed by the
candidate; DECISIONES prose word count ≤ 900.
