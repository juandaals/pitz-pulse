# Pitz Pulse

Internal-request triage service. It classifies Spanish and Portuguese messages with an LLM
(`categoria`, `prioridad`, `area_sugerida`, `idioma`, `resumen`, `requiere_info`,
`pregunta_seguimiento`, `confianza`, `version_prompt`), stores them, lets people list, filter and
correct them, and evaluates the classifier against golden sets.

> **Read this first — `resultados.json` comes from a mock run.** No real model call was made while
> building this project (decision D29: no spending on personal credentials). `/resultados.json` was
> promoted from `apps/api/eval/runs/case__v1__mock__mock.json` with `--allow-mock`, and
> `/resultados.meta.json` says `"mock": true`. The mock classifies with keywords, returns a fixed
> `resumen` ("Solicitud clasificada por el modo mock sin modelo.") and a constant `confianza` of 0.5:
> its numbers say nothing about model quality (gap G35). Section 5 shows how to regenerate
> `resultados.json` with a real model in three commands.

## 1. Quick start

```bash
docker compose up --build        # no .env and no credential exported in your shell → mock mode, API key dev-local-key
curl -s localhost:8000/health
curl -s -X POST localhost:8000/solicitudes \
  -H 'X-API-Key: dev-local-key' -H 'Content-Type: application/json' \
  -d '{"id":"DEMO-1","message":"No puedo entrar al portal de proveedores","source_area":"Compras MX"}'
curl -s 'localhost:8000/solicitudes?needs_review=true' -H 'X-API-Key: dev-local-key'
curl -s -X PATCH localhost:8000/solicitudes/DEMO-1 \
  -H 'X-API-Key: dev-local-key' -H 'Content-Type: application/json' \
  -d '{"prioridad":"alta","author":"reviewer","reason":"blocks an operation"}'
make smoke                       # end-to-end check against the running stack
```

Compose also reads credentials exported in your shell: with `ANTHROPIC_API_KEY` exported the stack
auto-selects the paid provider (and `make smoke` makes billed calls); with `CLAUDE_CODE_OAUTH_TOKEN`
exported it selects the Agent SDK, which also needs `LLM_TEMPERATURE=none` or it refuses to start.
For a guaranteed mock run: `env -u ANTHROPIC_API_KEY -u CLAUDE_CODE_OAUTH_TOKEN docker compose up --build`.
`dev-local-key` is for local use only; if your shell exports `API_KEY`, use that value in the curls. Mock responses carry the header `X-Pitz-Provider: mock`, and
every stored row records the `provider` and `model` that classified it. OpenAPI docs:
`http://localhost:8000/docs`.

API summary (paths follow the case contract):

| Method | Path | Notes |
|---|---|---|
| POST | `/solicitudes` | `{id, message, source_area?}` · 201 classified now · 200 already classified, no model call (idempotent on id + exact text) · 409 same id, different text · a failed request is retried by a new POST |
| GET | `/solicitudes` | filters `categoria`, `prioridad`, `area_sugerida`, `status`, `needs_review`; `limit` 1–100, `offset` |
| GET | `/solicitudes/{id}` | current values + `original_classification` + correction history |
| PATCH | `/solicitudes/{id}` | correct any of `categoria, prioridad, area_sugerida, idioma, resumen, requiere_info, pregunta_seguimiento` + `author` (+ `reason`); the original classification is kept |

Every error has the shape `{error, detail}`; see `docs/superpowers/specs/2026-09-25-02-service-persistence-design.md` §4.

## 2. Providers

| `LLM_PROVIDER` | Credential | What it is |
|---|---|---|
| `mock` | none | Deterministic keyword rules. For development and tests only; never model quality. |
| `anthropic_api` | `ANTHROPIC_API_KEY` | Claude Messages API (`claude-haiku-4-5` by default, temperature 0). The only provider whose runs can become the official `resultados.json`. |
| `claude_agent_sdk` | `CLAUDE_CODE_OAUTH_TOKEN` | Claude Agent SDK as a single-turn transport. It cannot set temperature, so it needs `LLM_TEMPERATURE=none` (the service refuses to start otherwise) and its runs are never promoted. |

LangGraph is always the harness (`call_llm → validate → retry`); providers are only transports.
With no `LLM_PROVIDER` set, the provider is chosen from the one credential present, or mock when
there is none. Messages are masked (email, phones BR/MX, CPF, CNPJ incl. the 2026 alphanumeric
format, CURP, RFC) before they reach any provider; the database keeps the original text and logs
never contain it.

## 3. Run with a real model (Pitz's key)

```bash
cp .env.example .env
# edit .env: ANTHROPIC_API_KEY=<Pitz key>   (LLM_PROVIDER=anthropic_api is already set)
docker compose up --build        # the API now classifies with claude-haiku-4-5 at temperature 0
```

**First real call.** The strict tool schema and the full request shape have only been verified
offline (no real call was made). The first real request — a single POST, or `make classify
SET=case` (12 messages) — is the live acceptance check: if the API rejected the schema, every
request would fail with `classification_failed` / `llm_rejected` and nothing would be promoted.
The Agent SDK path (`CLAUDE_CODE_OAUTH_TOKEN`) has likewise only been started, never called.

With `LLM_PROVIDER=anthropic_api` and an empty key the service refuses to start and names the
missing variable (it never falls back to mock silently). Rows classified earlier by the mock stay
in the database volume and keep `provider: mock`; run `docker compose down -v` to start clean.

## 4. Development

Prerequisites: `make`, [`uv`](https://docs.astral.sh/uv/) (it installs Python 3.12), Docker; `make smoke` also needs `curl` and `python3` on the host.

```bash
make install        # uv sync in apps/api
make test           # pytest — no test calls a real model or the network
make lint           # ruff format --check + ruff check
make up / make down # docker compose
```

## 5. Evaluation and `resultados.json`

Golden sets (model-agnostic; `apps/api/eval/golden/`, `/mensajes.json`):

- **case** — the 12 case messages with `/etiquetas_esperadas.json`.
- **edge** — 18 messages that exercise every PII type, prompt injection, mixed ES/PT, vague
  requests, two requests in one message, a long message, emoji/Slack markup, urgency words without
  impact and impact without urgency words. It is a regression guard, not an independent benchmark:
  the same assistant wrote it and prompt v1.

**Label provenance.** The labels of both sets were drafted by the AI assistant at the candidate's
request and approved by the candidate (case set on 2026-09-25; edge set on 2026-09-27, where the
candidate changed EDGE-15 to `backend` and EDGE-16 to `otro` with a follow-up question). The
candidate has the final say on every label.

Commands (a real run bills the provider; `make classify` prints provider, model and call count
first):

```bash
make classify SET=case                      # writes apps/api/eval/runs/case__v1__<provider>__<model>.json + meta
make eval RUN=case__v1__anthropic_api__claude-haiku-4-5
make classify SET=case SUFFIX=b && make compare RUN=case__v1__anthropic_api__claude-haiku-4-5 \
     COMPARE=case__v1__anthropic_api__claude-haiku-4-5__b   # noise floor between two identical runs
make promote RUN=case__v1__anthropic_api__claude-haiku-4-5  # replaces the mock resultados.json
```

`make promote` refuses anything that is not a clean `anthropic_api` / `claude-haiku-4-5` /
temperature-0 run of the case set with matching hashes (prompt, tool schema, inputs, results). A
mock run needs `ALLOW_MOCK=1`, and a mock promotion never replaces a real result unless you also
pass `FORCE=1` (don't). To regenerate the committed mock deliverable exactly:
`make classify SET=case FORCE=1 LLM_PROVIDER=mock && make promote RUN=case__v1__mock__mock ALLOW_MOCK=1`
(a command-line `LLM_PROVIDER` overrides the one loaded from `.env`).

**Last eval report (mock, not model quality).** `make eval RUN=case__v1__mock__mock`: categoria
10/12, prioridad 5/12, area_sugerida 9/12, idioma 12/12, requiere_info 5/12, all five fields 2/12.
The mock's keywords come from the case messages, so these numbers only prove the tooling works.

**Prompt iteration and threshold (blocked on a real run, G35).** Prompt v1 is active
(`apps/api/prompts/CHANGELOG.md`). No v2 was written: improving a prompt against mock output would
be fiction. `CONFIDENCE_THRESHOLD=0.7` is a placeholder; the sweep in the eval report is meant to
choose it from real runs. With Pitz's key the protocol is: run v1 twice (noise floor), write one
general rule as v2, run v2 on both sets, keep it only if it beats v1 beyond the noise floor on the
case set without regressing the edge set, then promote.

## 6. Stack and why

- **Python 3.12 + FastAPI + stdlib `sqlite3`** — small surface, sync routes, versioned SQL
  migrations with an atomic runner; no ORM for two tables.
- **LangGraph as the harness** — the retry loop that feeds validation errors back to the model is a
  real cycle, and the graph can grow (e.g. multi-turn clarification).
- **Structured output by forced tool use with `strict`**, re-validated by pydantic.
- **Providers as adapters** behind one interface, with a model catalog (capabilities and prices).
- **uv**, **ruff**, **pytest**; Docker + compose with a named volume.

## 7. Structured logs

One JSON line per model attempt (latency, tokens, estimated cost), one `request_outcome` per POST,
never message text, model output or exception messages. Real line (mock provider) for a message
that contained an email address:

```json
{"message_id": "DEMO-2", "provider": "mock", "model": "mock", "actual_model": "mock", "prompt_version": "v1", "attempt": 1, "outcome": "ok", "latency_ms": 0.0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "equivalent_api_cost_usd": 0.0, "billing": "none", "transport_retries": 0, "pii_masked": {"email": 1}, "ts": "2026-09-27T16:14:08-0500", "level": "INFO", "logger": "pitz_pulse.llm", "event": "llm_call"}
```

## 8. Assumptions

- Idempotency is defined by `id` + exact message text; different whitespace or Unicode form is a
  different text (409).
- `pregunta_seguimiento` is written in the message's language; `resumen` is always Spanish.
- A request that cannot be classified is stored as `failed` and a new POST retries it.
- A request whose `confianza` is below the threshold goes to the review queue until someone PATCHes
  it (confirming counts).

## 9. Pending items

| Item | Why | How |
|---|---|---|
| Real `resultados.json`, measured prompt iteration, chosen threshold | No real calls during development (D29, G35) | Section 5 with Pitz's key |
| A request re-claimed while its first worker still runs can cost a second model call | The stale window cannot bound an in-flight HTTP call | Accepted; the claim token prevents any overwrite |
| Chunked request bodies without `Content-Length` are not size-limited in the app | Only `Content-Length` is checked | Put nginx (`client_max_body_size`) in front |
| Stuck `pending` rows after a container kill answer 409 until they go stale (~9 min) | Conservative stale window | Wait for `Retry-After`, or `docker compose down -v` in development |
| `POST /solicitudes/` (trailing slash) redirects with 307 | Starlette default | Use the exact path |
| Live acceptance of the strict tool schema and the Agent SDK path | No real call in development (D29) | The first real call (section 3) |
| `AI_LOG.md` | Written by the candidate | — |
| Extras (web UI, CI, model comparison, duplicates, Slack) | Parts 1–4 first | Specs 05–06 |

## 10. Documentation map

`DECISIONES.md` (design decisions) · `docs/MASTER.md` (requirements, decisions, gaps) ·
`docs/superpowers/specs/` and `plans/` · `docs/superpowers/reviews/` (every review gate) ·
`AI_LOG.md` (written by the candidate: how AI was used, including the conversation link).
