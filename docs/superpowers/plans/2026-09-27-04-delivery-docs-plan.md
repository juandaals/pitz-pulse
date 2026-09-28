# Delivery & Docs (Spec 04) Implementation Plan — fast mode

> Fast mode (candidate decision 2026-09-27): no plan gate, one reviewer at the end. Behavior,
> files and checks are exact; routine code is left to the implementer. Checkbox steps.

**Goal:** `docker compose up` on a clean clone serves the API (mock by default), `make` drives
every workflow, the env inventory is tested, and README + DECISIONES document the system honestly.

**Spec:** `docs/superpowers/specs/2026-09-25-04-delivery-docs-design.md` (rev 3) with two
alignments recorded here: the Dockerfile copies `pyproject.toml uv.lock src/ prompts/`
(migrations live in `src/pitz_pulse/migrations/`, no separate dir); `make web-types` is added by
Spec 05, not now.

## Global Constraints

- Never call a real model or the network except Docker image pulls and PyPI via uv inside the
  build. No new Python dependencies.
- Files < 300 lines; ruff clean; `uv run pytest` green.
- Never commit `.env`, databases or secrets; `.env.example` has empty secrets.
- Conventional commits with body + `Co-Authored-By: <your model> <noreply@anthropic.com>`.
- Never leave containers running at the end (`docker compose down -v`).

---

### Task 1: Image, compose, smoke

**Files (repo root unless noted):** `apps/api/Dockerfile`, `apps/api/.dockerignore`,
`docker-compose.yml`, `scripts/smoke.sh` (executable).

- **Dockerfile:** `python:3.12-slim`; `COPY --from=ghcr.io/astral-sh/uv:<pinned tag> /uv /bin/uv`;
  `WORKDIR /app`; copy `pyproject.toml uv.lock` first and `uv sync --frozen --no-dev
  --no-install-project` (layer cache), then copy `src/ prompts/` and `uv sync --frozen --no-dev`;
  `useradd -m app`; `mkdir -p /data && chown app:app /data`; `ENV APP_ROOT=/app
  PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1`; `USER app`; `EXPOSE 8000`;
  `CMD ["uvicorn", "--factory", "pitz_pulse.api:create_app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]`
  (lifespan on). `.dockerignore`: `.venv`, `__pycache__`, `.pytest_cache`, `.ruff_cache`, `tests`,
  `eval`, `data`, `*.db`.
- **docker-compose.yml:** service `api` built from `apps/api`; `environment:` every inventory
  variable with `${VAR:-default}` except `LLM_PROVIDER: ${LLM_PROVIDER-}` and
  `LLM_TEMPERATURE: ${LLM_TEMPERATURE-0}`; `API_KEY: ${API_KEY:-dev-local-key}`,
  `DB_PATH: /data/pitz_pulse.db`, `APP_ROOT: /app`; `ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN`
  passed as `${VAR:-}`; ports `${API_PORT:-8000}:8000`; volume `pitz-data:/data`; healthcheck
  `python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"`
  interval 10s, timeout 5s, retries 5, start_period 20s. No `web` service yet (Spec 05).
  Note: compose reads a root `.env` automatically; that is intended (D29).
- **scripts/smoke.sh:** `set -euo pipefail`; `API_URL=${API_URL:-http://localhost:8000}`,
  `API_KEY=${API_KEY:-dev-local-key}`; id `SMOKE-$(date +%s)`; checks with curl + python json:
  `/health` 200; POST → 201 with the 10 contract keys; same POST → 200; POST without key → 401;
  GET `/solicitudes/<id>` 200; PATCH `{"prioridad":"baja","author":"smoke"}` → 200 and
  `corrected` true; GET list with `?categoria=` of the returned value includes the id. Prints
  `SMOKE OK` or exits 1 naming the failed step. No message text beyond a synthetic sentence.

- [ ] Step 1: write the files.
- [ ] Step 2: from the repo root with **no `.env`** (move any local `.env` aside temporarily and
  restore it after): `docker compose down -v && docker compose up --build -d`; wait until
  `docker compose ps` shows healthy; `curl -s localhost:8000/health` shows `"provider":"mock"`;
  `./scripts/smoke.sh` → `SMOKE OK`; `docker compose logs api | grep -c '"event": "llm_call"'` ≥ 1.
- [ ] Step 3: provider start checks with dummy credentials (no requests beyond `/health`):
  `LLM_PROVIDER=anthropic_api ANTHROPIC_API_KEY=dummy docker compose up -d` → healthy,
  `/health` provider `anthropic_api`; `LLM_PROVIDER=claude_agent_sdk CLAUDE_CODE_OAUTH_TOKEN=dummy`
  → healthy, provider `claude_agent_sdk` (the bundled CLI answers `claude -v` in the container).
  If the Agent SDK case fails, capture the log and report it (do not work around silently).
- [ ] Step 4: `docker compose down -v`; paste all real output in the report; commit
  `feat: add the API image, compose stack and smoke test`.

### Task 2: Makefile, `.env.example`, env inventory test

**Files:** `Makefile`, `.env.example`, `apps/api/tests/test_env_inventory.py`.

- **Makefile:** `-include .env` + `export`; `ROOT := $(abspath .)`; `API := $(ROOT)/apps/api`;
  `UV := uv --directory $(API) run`. Targets (all `.PHONY`):
  `install` (`uv --directory $(API) sync`), `test` (`$(UV) pytest`), `lint` (`$(UV) ruff format
  --check . && $(UV) ruff check .`), `classify` (requires `SET`; echoes
  `provider=$${LLM_PROVIDER:-auto} model=$${LLM_MODEL:-claude-haiku-4-5} set=$(SET)` and
  "N calls" from the message count, then `$(UV) python -m pitz_pulse.batch --set $(SET)
  $(if $(SUFFIX),--suffix $(SUFFIX)) $(if $(FORCE),--force)`), `eval` (requires `RUN`;
  `$(UV) python -m pitz_pulse.evaluate --run $(RUN) $(if $(COMPARE),--compare $(COMPARE))
  $(if $(THRESHOLD),--threshold $(THRESHOLD))`), `compare` (requires `RUN` and `COMPARE`, same
  command), `promote` (requires `RUN`; `$(if $(filter 1,$(ALLOW_MOCK)),--allow-mock)
  $(if $(filter 1,$(FORCE)),--force)` — only the literal `1` enables a flag), `up`
  (`docker compose up --build -d`), `down` (`docker compose down`), `smoke`
  (`./scripts/smoke.sh`). Missing required variables → a clear `$(error …)`.
- **.env.example:** every variable of Spec 04 §3 with a one-line comment; secrets empty;
  `LLM_PROVIDER=anthropic_api` (D29), `LLM_TEMPERATURE=0`, `APP_ROOT=` empty,
  `PROMPT_VERSION=v1`, `API_KEY=dev-local-key` with a "local only" comment. Never enable tracing.
- **test_env_inventory.py:** parses `.env.example` and `docker-compose.yml` as text (no new deps:
  simple regex for `NAME=` and `NAME:` under `environment:`); asserts every inventory variable
  (list in the test, from Spec 04 §3 minus the tracing ones) appears in `.env.example`; every
  compose env var is in the inventory; `PROMPT_VERSION` in `.env.example` ==
  `config.ACTIVE_PROMPT_VERSION` and appears in compose with the same default; `.env.example` has
  empty `ANTHROPIC_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN`; no tracing variable is set to true.

- [ ] Step 1: failing test → Step 2: files → Step 3: `make test`, `make lint`,
  `make eval RUN=case__v1__mock__mock` (paste head), `make promote RUN=x` without the flag on a
  scratch copy is NOT run (would touch the deliverable) — instead show `make -n promote
  RUN=case__v1__mock__mock ALLOW_MOCK=1` prints `--allow-mock` and `ALLOW_MOCK=0` prints no flag.
- [ ] Step 4: commit `feat: add the Makefile, env example and env inventory test`.

### Task 3: README and DECISIONES (controller)

Written by the controller from MASTER, the specs, the review reports and the real outputs of
Tasks 1–2. Must include (candidate request 2026-09-27): label provenance (AI-drafted,
candidate-approved; EDGE-15/16 changed by the candidate); `resultados.json` comes from a mock run
(D31, G35) and what that means; exactly how to activate the real batch (`ANTHROPIC_API_KEY` in
`.env`, `make classify SET=case`, `make eval`, `make promote RUN=…`). DECISIONES prose ≤ 900 words;
cost section labeled as an estimate.

### Final review

One reviewer over the whole branch: clean-clone `docker compose up` + smoke, docs accuracy against
code and MASTER, no secrets committed.
