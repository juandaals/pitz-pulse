# Spec 01 — Classification core

- **Covers:** R1.1–R1.8, R1.10, R2.6 (LLM call logs), R4.1 (schema + masking tests). R1.9
  (promotion to `resultados.json`) is Spec 03.
- **Depends on:** nothing
- **Status:** draft (rev 3 — spec-gate review `reviews/2026-09-25-01-spec-review.md` applied)
- **Decisions used:** D1–D5, D9–D12, D14–D16, D18–D21, D23–D26 (see `docs/MASTER.md`)
- **Gaps owned:** G1, G3, G4, G7, G8, G9, G10, G17, G18, G23, G29–G34

## 1. Goal

Turn one raw request (`id`, `message`, optional `source_area`) into a validated `Classification`
that matches the case contract exactly, never sending unmasked PII to any provider or third party,
and classify a golden set concurrently into a run file with honest usage/cost metadata. Providers
are pluggable (Strategy/Adapter); the flow is a LangGraph graph identical for every provider.

Out of scope: HTTP, persistence (Spec 02), evaluation and promotion (Spec 03).

## 2. Contract (exact names — do not translate)

| Field | Type | Produced by |
|-------|------|-------------|
| `id` | str | code (from input; never sent to the model) |
| `categoria` | `bug \| datos \| acceso \| automatizacion \| consulta \| otro` | model |
| `prioridad` | `alta \| media \| baja` | model |
| `area_sugerida` | `backend \| frontend \| data \| devops \| producto \| digital_transformation` | model |
| `idioma` | `es \| pt` | model |
| `resumen` | str, 1–20 words (`len(text.split())`), ≤ 200 chars, Spanish | model |
| `requiere_info` | bool | model |
| `pregunta_seguimiento` | str (1–300 chars after strip) when `requiere_info`; `null` otherwise (`""` rejected) | model |
| `confianza` | float in [0, 1] | model |
| `version_prompt` | str | code (prompt file stem, D15) |

`pregunta_seguimiento = null` when `requiere_info = false` is our stricter reading of the case
(documented in README assumptions). All models use `ConfigDict(extra="forbid")`, with `strict=True`
applied field-by-field to the non-enum fields (`StrictStr`, `StrictBool`, the `Confidence` float) —
model-level `strict=True` would reject the enum fields' plain string values coming back from the
tool call JSON; validators are `mode="after"` and never echo values in their messages ("has 23
words", not the text).

Request input (D11): `id` `^[A-Za-z0-9_-]{1,64}$`; `message` 1–4000 chars after strip (the stored
and hashed value is the original, unstripped) and ≤ 8000 raw chars before stripping; `source_area`
optional, 1–100 chars.

`schema.py` exposes `ClassificationShape` (types, enums, extra=forbid, no rule validators) —
Spec 03 validates run files structurally with it — and `Classification(ClassificationShape)` with
the rule validators.

## 3. Components (`apps/api/src/pitz_pulse/`)

| File | Responsibility | Depends on |
|---|---|---|
| `schema.py` | Enums, `RequestInput`, `ModelOutput`, `ClassificationShape`, `Classification` | pydantic |
| `masking.py` | `mask(text) -> MaskResult` | stdlib `re`, `unicodedata` |
| `prompts.py` | Load `prompts/<version>.md` (system, user template, feedback template); `render_user(masked_req, feedback=None)` | stdlib |
| `models_catalog.py` | Catalog keyed by `(provider, model)`: prices, capabilities, billing | stdlib |
| `tool_schema.py` | `build_tool_schema()` for strict tool use | schema |
| `providers/base.py` | `ProviderAdapter` protocol, `LLMCall`, `LLMError` | stdlib |
| `providers/anthropic_api.py` | `AnthropicApiAdapter` — `langchain-anthropic` `ChatAnthropic`, forced tool + strict | langchain-anthropic |
| `providers/claude_agent_sdk.py` | `ClaudeAgentSdkAdapter` — `claude-agent-sdk` `query()` used directly, plain-JSON reply | claude-agent-sdk |
| `providers/mock.py` | `MockAdapter` — deterministic keyword rules | stdlib |
| `providers/__init__.py` | `build_adapter(settings)` factory over a registry | above |
| `graph.py` | LangGraph `StateGraph`: `call_llm → validate → (retry \| done \| fail)` + attempt logging | langgraph |
| `classifier.py` | `build_classifier(settings, adapter=None)`; `Classifier.classify(req) -> ClassifyOutcome` | graph, masking |
| `runs.py` | Run stems, meta, hashes, atomic writes, path confinement | stdlib |
| `batch.py` | CLI: golden set → run file + meta; exit codes; stderr summary | batch_run, runs |
| `batch_run.py` | Sliding concurrency window, per-item failure records, credential stop rule | classifier |
| `config.py` | `LLMSettings`: read + validate env once; provider auto-selection; tracing off | stdlib |
| `logs.py` | JSON formatter; third-party loggers pinned to WARNING | stdlib |

Dependencies (MASTER §2.6): `langgraph` + `langchain-core` (D1 harness), `langchain-anthropic`
(API transport, forced tool calling across providers), `claude-agent-sdk` (OAuth transport; used
directly because no LangChain wrapper exposes the isolation options we require — review HR/HP),
`pydantic`, `langsmith` (imported directly for `tracing_context(enabled=False)`, G31) and `anyio`
(runs the Agent SDK's async `query()` from a worker thread) — both already transitive, declared
because the code imports them; dev: `pytest`, `ruff`. No other provider packages until a provider
is added.

## 4. Class diagram

```
«StrEnum» Categoria · Prioridad · Area · Idioma

RequestInput(strict, extra=forbid)      ClassificationShape(strict, extra=forbid)
 id, message, source_area?               10 contract fields, types + enums only
                                                ▲
ModelOutput(strict, extra=forbid)        Classification(ClassificationShape)
 8 model fields + rule validators         + rule validators (words, pregunta ⇔ requiere_info)

MaskedRequest (frozen): masked_message · masked_source_area · pii_counts

«Protocol» ProviderAdapter
 + provider: str · + model: str                     (effective values, used in logs/meta/health)
 + caps: ProviderCaps
 + invoke(system, user, tool_schema, deadline_s) -> LLMCall
      deadline_s is a budget checked before each attempt/wait (best effort, see §8.8);
      raises LLMError for provider failures
      ▲                        ▲                         ▲
AnthropicApiAdapter     ClaudeAgentSdkAdapter       MockAdapter          future: OpenAIAdapter…
 ChatAnthropic           claude_agent_sdk.query      keyword rules
 .bind_tools(forced,     (isolated options,          model="mock"
   strict per caps)       JSON reply)

ProviderCaps (frozen, catalog row keyed by (provider, model))
 input_usd_per_mtok · output_usd_per_mtok · supports_temperature · supports_forced_tool
 supports_strict · billing: "api" | "subscription" | "none"

LLMCall (frozen)
 tool_input: dict | None · stop_reason: str | None · model: str (configured)
 actual_model: str (reported by the provider, e.g. a dated snapshot) · input_tokens
 · output_tokens · latency_ms · cost_usd · equivalent_api_cost_usd
 · transport_retries: int (adapter-owned retries inside this attempt)
LLMError(Exception)
 kind: Literal["unavailable", "rejected"] · error_type: str (class/literal name only) · latency_ms

AttemptRecord (frozen): attempt · outcome · input_tokens · output_tokens · cost_usd
                        · equivalent_api_cost_usd · latency_ms · transport_retries
                        · error_type: str | None (class/literal name of an LLMError or crash)
ClassifyOutcome (frozen): classification: Classification · attempts: list[AttemptRecord]
ClassificationError(Exception): kind: Literal["llm_unavailable","llm_rejected","invalid_output"]
                                · attempts: list[AttemptRecord]
ClassificationCrash(RuntimeError): error_type: str (class name only) · attempts: list[AttemptRecord]
                                   (any unexpected exception, incl. masking or final validation)

ClassifyState (TypedDict): masked: MaskedRequest · message_id: str · attempt: int
                           · feedback: str | None · last_call: LLMCall | None
                           · output: ModelOutput | None · error_kind: str | None
                           · sink: list[AttemptRecord] (one list object owned by classify(): it
                             survives a node exception, so billed attempts reach the crash)

Classifier(adapter, prompt, settings)
 + classify(req: RequestInput) -> ClassifyOutcome        raises ClassificationError
                                                          or ClassificationCrash
 + tool: dict (the bound tool schema; hashed into run meta)
build_classifier(settings, adapter=None) -> Classifier   (adapter=None → build_adapter(settings))
```

Masking happens in `Classifier.classify` **before** the graph, so the raw text never enters graph
state (G31).

## 5. Flow — the graph (same for every provider)

```
Classifier.classify(req)
  masked = mask(message), mask(source_area)            raw text never enters the graph
  │
  ▼
START ─► [call_llm]  attempt += 1 (1-based)
  │       user = prompt.render_user(masked, feedback)
  │       adapter.invoke(system, user, tool_schema, deadline_s)
  ├── LLMError(unavailable) ─► log attempt(outcome=unavailable) ─► [fail: llm_unavailable] ─► END
  ├── LLMError(rejected) ───► log attempt(outcome=rejected) ────► [fail: llm_rejected] ────► END
  ├── any other exception ──► log attempt(outcome=error, error_type=class) ─► re-raise
  ▼
[validate]  tool_input None, stop_reason ∈ {max_tokens, refusal}, or ModelOutput invalid?
  ├── valid ──────► log attempt(outcome=ok) ─► [done] ─► END
  └── invalid ────► log attempt(outcome=invalid_output)
        ├── attempt < 1 + INVALID_OUTPUT_RETRIES ─► feedback = prompt.feedback(errors) ─► [call_llm]
        └── otherwise ─────────────────────────────► [fail: invalid_output] ─► END
```

- Exactly one `llm_call` log line per attempt, for every outcome. `LLMError.kind` routing is
  exhaustive; an unknown kind raises.
- Feedback lists `(field path, error type, message)` from `errors(include_input=False,
  include_url=False)` — never input values — rendered from the versioned prompt's feedback template
  inside its own `<feedback>` block.
- No backoff between invalid-output retries (the model is not overloaded; the input changes);
  transport backoff lives inside the adapter. Documented in DECISIONES.
- `recursion_limit` is set explicitly from `INVALID_OUTPUT_RETRIES`.
- Masking, the graph run and the final `Classification.model_validate` all sit inside one `try`
  in `Classifier.classify`: any unexpected exception (and a "neither output nor error" end state)
  surfaces as `ClassificationCrash(error_type, attempts)` carrying the attempts already billed
  (collected through the state `sink`). Results are never built with `model_construct`.

`llm_call` log: `{event, message_id, provider, model, actual_model, prompt_version, attempt,
outcome: ok|invalid_output|unavailable|rejected|error, error_type?, latency_ms, input_tokens,
output_tokens, cost_usd, equivalent_api_cost_usd, billing, transport_retries, pii_masked:
{email: 1, ...}}`. Never message text,
`source_area`, model output text, or credentials. Transport-level retries inside an SDK are not
individually visible; the attempt's latency includes them (documented gap).

## 6. Flow — batch

```
make classify SET=case|edge [SUFFIX=x] [FORCE=1]
  │ SET → input: case = /mensajes.json · edge = apps/api/eval/golden/edge_cases.messages.json
  │ input must be a non-empty JSON list; parse all items as RequestInput; duplicate ids,
  │ invalid items, bad SUFFIX
  │ (^[a-z0-9-]{1,20}$) ─► exit 2 before any LLM call (errors show id + field, never text)
  │ target run exists and not FORCE ─► exit 2
  ▼
ThreadPoolExecutor(max_workers=LLM_CONCURRENCY), sliding window: at most LLM_CONCURRENCY futures
in flight; wait(FIRST_COMPLETED) refills each slot as soon as it frees up, so one slow item never
stalls the others; the pool is always shut down
  │ per future: ClassifyOutcome, or ClassificationError(kind), or ClassificationCrash / any other
  │ Exception → kind "unexpected" (logged with id and exception class only); failure kinds:
  │ llm_unavailable | llm_rejected | invalid_output | unexpected | cancelled
  │ credential rejection (last attempt's error_type: HTTP 401/403, authentication_failed,
  │   billing_error, CLINotFoundError) ─► stop submitting new items; futures already in flight
  │   run to completion and are recorded normally; requests never submitted are recorded as
  │   failures with kind "cancelled". Any other llm_rejected (e.g. 400/413 on one oversized
  │   message) is recorded and the batch continues
  │ Ctrl-C ─► shutdown(wait=False, cancel_futures=True), exit 130, nothing written, in-flight
  │   calls abandoned (may still be billed)
  ▼
results sorted by id; failures listed (id, kind)
  ▼
runs.write_pair(run_items, meta): meta written first (with results_sha256), then run; both via
unique temp file in the same dir + fsync + os.replace
  ▼
exit 0 if no failures; exit 1 if some items failed (files still written so paid evidence is kept;
promote refuses); exit 1 with nothing written when there are no successful outcomes and every
failure is `llm_rejected` or `cancelled` (the whole run was rejected)
```

**Stem:** `<set>__<prompt>__<provider>__<model>[__<suffix>]` (D26); model id sanitized to
`[a-z0-9.-]` in the stem, raw id kept in meta. All paths resolved under `apps/api/eval/runs/`.
Runs are committed after every real run (D26). The batch never writes `resultados.json` (D19).

**Run file:** JSON list of objects with exactly the 10 contract keys (`null` kept), `ensure_ascii=False`.

**Meta (`<stem>.meta.json`):** `set, input_file (repo-relative), input_sha256, provider, model,
billing, prompt_version, prompt_sha256, tool_schema_sha256 (sha256 of the canonical JSON — sorted
keys, no whitespace — of the tool schema actually bound; its descriptions carry rubric text outside
the prompt file, G14; `null` for `claude_agent_sdk`, which never sends the tool), temperature (number | null), invalid_output_retries,
llm_max_retries, timeout_s, concurrency, n, n_input (count of parsed input items, including any
never classified), failures: [{id, kind}], results_sha256, total_input_tokens, total_output_tokens,
total_cost_usd, total_equivalent_api_cost_usd, attempts_total, invalid_output_attempts,
transport_retries_total, p50_latency_ms_per_message, run_at (UTC ISO-8601 Z)`.
Totals sum **every attempt**, including failed ones.

`make classify` prints provider, model, temperature and the worst-case call count
`n × (1 + INVALID_OUTPUT_RETRIES) × (1 + LLM_MAX_RETRIES)` before a paid run.

## 7. State diagram — one classification

```
          ┌──────────┐   masked before the graph
START ───►│  masked  │
          └────┬─────┘
               ▼
          ┌─────────┐ LLMError(unavailable) ┌───────────────────────┐
   ┌─────►│ calling │──────────────────────►│ failed:llm_unavailable│
   │      └──┬──┬───┘ LLMError(rejected)    ├───────────────────────┤
   │         │  └──────────────────────────►│ failed:llm_rejected   │
   │         │  other exception             └───────────────────────┘
   │         │  └──► raised (logged, outcome=error)
   │         ▼
   │   ┌────────────┐ valid  ┌───────────┐
   │   │ validating │───────►│ succeeded │
   │   └─────┬──────┘        └───────────┘
   │         │ invalid / no tool input / max_tokens / refusal
   │         ▼
   │  ┌──────────────┐ no retries left ┌───────────────────────┐
   └──│ retry with   │────────────────►│ failed:invalid_output │
      │ feedback     │                 └───────────────────────┘
      └──────────────┘
```

## 8. Key design details

### 8.1 Structured output per adapter
- `build_tool_schema()` starts from `ModelOutput.model_json_schema()`, inlines `$defs` (no `$ref`,
  `allOf`), keeps `pregunta_seguimiento` as `anyOf [string, null]`, sets `additionalProperties:false`
  and all fields required, and strips keywords strict mode rejects (`minimum`, `maximum`,
  `minLength`, `maxLength`, `pattern`, `default`, `title`), moving each constraint into the field's
  `description`. Every field has a description stating its rule (e.g. `resumen`: "Spanish, at most
  20 words"). Pydantic enforces everything again (G23).
- `AnthropicApiAdapter`: `ChatAnthropic(model, api_key=…, base_url=default, temperature?,
  max_retries=0, timeout, max_tokens=1024).bind_tools([tool], tool_choice="record_classification")`;
  `strict` is set inside the tool dict itself (`tool["strict"] = True` when `caps.supports_strict`),
  not passed to `bind_tools`, because `langchain-anthropic` ignores `bind_tools(strict=...)` for
  dict-shaped tools. Reads `tool_calls` (empty → `tool_input=None`, never `IndexError`;
  `invalid_tool_calls` → `None`), `usage_metadata`, `response_metadata["stop_reason"]`. Exact
  LangChain signatures verified with current docs in the plan.
- `ClaudeAgentSdkAdapter`: the Agent SDK has no forced tool choice and its `output_format` runs a
  hidden re-prompt loop (conflicts with D4). So the prompt's JSON instruction section asks for **one
  JSON object with the 8 fields and nothing else**; the adapter extracts the final assistant text,
  parses it with `json.loads` (failure → `tool_input=None`), and returns it as `tool_input`. The
  graph validates and retries exactly as for any provider. `caps`: forced tool no, strict no.
- `MockAdapter`: returns a valid dict directly.

### 8.2 Providers, credentials and selection (D18, D23, D25)

| Provider | Credential env | Temperature | Billing | Role |
|---|---|---|---|---|
| `anthropic_api` | `ANTHROPIC_API_KEY` | yes (0 for official runs) | api | evaluators; the **only** provider that can be promoted (D23) |
| `claude_agent_sdk` | `CLAUDE_CODE_OAUTH_TOKEN` | no | subscription | candidate's development runs (host or Docker) |
| `mock` | none | n/a | none | no-credential demo; never quality |

Selection (`config.py`):
- `LLM_PROVIDER` set → used as is.
- `LLM_PROVIDER` unset and exactly one credential present → that credential's provider, logged at
  INFO ("provider auto-selected from credential").
- Unset and no credential → `mock`, logged at WARNING. Unset and both credentials → startup error.
- Selected provider's credential missing → startup error. The non-selected credential must be empty
  (startup error otherwise), so the wrong account is never billed silently.
- `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_API_URL`, `ANTHROPIC_LOG`,
  `ANTHROPIC_CUSTOM_HEADERS`, `ANTHROPIC_UNIX_SOCKET`, `CLAUDE_CODE_USE_BEDROCK`,
  `CLAUDE_CODE_USE_VERTEX`, `CLAUDE_CODE_USE_FOUNDRY`, `CLAUDE_CODE_USE_ANTHROPIC_AWS`,
  `CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD`, `CLAUDE_CODE_EXTRA_BODY`, `CLAUDE_CODE_HOST_CREDS_FILE`
  set → startup error (each would redirect provider auth, backend, headers, body or logging).
- Credential values are stripped; values starting/ending with a quote → startup error.
- In mock mode every API response carries `X-Pitz-Provider: mock` (Spec 02).

**Agent SDK isolation (D1, D24, G29, G32).** LangGraph is always the harness; the Agent SDK is a
transport making one model turn per `invoke`. Options, all asserted by a test:
`tools=[]`, `allowed_tools=[]`, `mcp_servers={}`, `strict_mcp_config=True`, `setting_sources=[]`,
`skills=[]`, `plugins=[]`, `agents=None`, `hooks=None`, `max_turns=1`, fixed non-interactive
`permission_mode`, explicit `system_prompt`, `model=<configured>`, `cwd=<fresh empty temp dir>`,
`verbatim_prompts=True`, `thinking={"type": "disabled"}`, `extra_args={"no-session-persistence":
None}`, and an explicit `env`: every inherited variable is blanked except `PATH`, `TMPDIR`, `LANG`,
`LC_ALL`, plus `CLAUDE_CODE_OAUTH_TOKEN`, `API_TIMEOUT_MS` / `CLAUDE_CODE_MAX_RETRIES` derived from
settings, a writable `HOME` / `CLAUDE_CONFIG_DIR` temp dir, and nonessential traffic/telemetry
disabled. No inherited `ANTHROPIC_*`, `API_KEY`, `SLACK_*`. The plan's first task verifies the exact
option and env names against the installed SDK and confirms `max_turns=1` completes with a
plain-JSON reply (stubbed stream first, then one announced real call). A process-wide semaphore
sized `LLM_CONCURRENCY` bounds concurrent CLI subprocesses (batch and API); it is acquired with a
timeout bounded by the call's remaining deadline, never blocking past it. Startup readiness: CLI
resolvable and executable, `HOME` writable, and one `claude -v` subprocess version check inside
`check_ready` (called once at startup); once it passes, `CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK=1` is
set so the SDK itself does not repeat that check on every call. `AssistantMessage.model` differing
from the configured model is logged as a warning and recorded. At construction, `ClaudeAgentSdkAdapter`
rejects (`ConfigError`) any settings whose `deadline_s` leaves less than a 15 s cleanup margin plus
one full `LLM_TIMEOUT_SECONDS` attempt (the SDK's shielded transport close can itself take up to
that margin).

Agent SDK error mapping: `authentication_failed`, `billing_error`, `invalid_request`,
`CLINotFoundError` → rejected; `rate_limit`, `server_error`, `api_error_status` 429 / ≥ 500,
`CLIConnectionError` and other `ClaudeSDKError`s, any exception whose type is exactly `Exception`
(the SDK's control-protocol failures, e.g. control request timeouts) → `SDKControlError` without
its text, deadline exceeded → unavailable; any other exception (a subclass such as `ValueError`)
is a programming error and is re-raised unchanged (never absorbed as a retryable failure); a `claude -v`
probe that times out or cannot start → `ConfigError`; CLI stderr is piped to a callback that logs
the constant event `agent_sdk_stderr` with the line length only, never its text; a `ResultError` whose subtype is not
`error_max_turns` → unavailable (subtype recorded as `error_type`); `error_max_turns` or no parsable
JSON → `tool_input=None` (invalid output). `error_type` is always the class/literal name, never
`str(exc)`.

Cost: `api` → tokens × catalog price; `subscription` → `cost_usd=0` and `equivalent_api_cost_usd`
computed from tokens × the API price of the same model.

### 8.3 Catalog — keyed by (provider, model)

| Provider | Model | $ in / out per MTok | temperature | forced tool | strict | billing |
|---|---|---|---|---|---|---|
| anthropic_api | `claude-haiku-4-5` (default) | 1.00 / 5.00 | yes | yes | yes | api |
| anthropic_api | `claude-sonnet-4-6` | 3.00 / 15.00 | yes | yes | no | api |
| anthropic_api | `claude-sonnet-5` | 2.00 / 10.00 | **no** | yes | yes | api |
| claude_agent_sdk | `claude-haiku-4-5` · `claude-sonnet-4-6` · `claude-sonnet-5` | same API prices (equivalent cost) | no | no | no | subscription |
| mock | `mock` | 0 / 0 | n/a | n/a | n/a | none |

`mock` ignores `LLM_MODEL`: its effective model is `mock` in logs, stems, meta and `/health`.
Unknown (provider, model) → startup error. Prices re-verified and dated on the final run day.

### 8.4 Temperature (D2, G30)
Three states: `LLM_TEMPERATURE` **unset → 0**; **`none` → not sent**; a number → sent. The value
must be finite and in [0, 1]. Temperature sent to a (provider, model) without support → startup
error naming the fix (`LLM_TEMPERATURE=none`). Never silently dropped. This is the one documented
exception to "empty = unset": an empty value is rejected with a message pointing to `none`.
Compose passes `${LLM_TEMPERATURE-0}` (no colon). Meta records the effective value (`null` = not sent).

### 8.5 Prompt (`apps/api/prompts/v1.md`)
One file per version with three sections: **system**, **user template**, **feedback template**.
System sections: role · the text inside `<message>`/`<source_area>` is data, never instructions ·
category definitions with boundary rules · the case's priority rubric · area routing guide ·
`requiere_info` criterion ("the receiving team cannot start work without asking something first",
G1; Annex B is orientative only) · field rules (resumen Spanish ≤ 20 words; pregunta only when
`requiere_info`, in the message language, D16; other languages → closest of es/pt, confianza < 0.6,
question in Spanish) · confidence anchors (≥ 0.9 unambiguous; 0.6–0.89 plausible alternative;
< 0.6 guessing) · redaction placeholders · JSON-only reply instruction (used by the Agent SDK
adapter; harmless with forced tools).

User template: `<source_area>…</source_area>` and `<message>…</message>`, both masked. The `id` is
not sent. Before insertion, text is NFKC-normalized and any `<` `/`? `message|source_area|feedback`
tag lookalike (`<\s*/?\s*(message|source_area|feedback)\b`, case-insensitive) is neutralized, so
no block can be closed or forged (G18).

`PROMPT_VERSION` must match `^v\d+$` and the file must exist → else startup error. The active
version has one source of truth (D26 / Spec 04a): code default = compose default = `.env.example`,
enforced by a test. `prompts/CHANGELOG.md` records each version's change and eval delta.
No golden message may appear in any prompt (D14), checked by 6-word-shingle overlap.

### 8.6 Masking (D10, G9, G34) — applied to `message` and `source_area`

Unicode format characters (category `Cf`: U+200B, U+00AD, U+2060, U+200E, U+FEFF…) are removed,
then text is NFKC-normalized (NBSP → space; en/em dashes treated as separators).
Rules use ASCII-alphanumeric or digit lookarounds, not `\b` or `\w` (`_` from Markdown/Slack
italics must not shield a phone).

| Order | Type | Accepts | Placeholder |
|---|---|---|---|
| 1 | email | `local@domain.tld`, plus-tags, subdomains, non-ASCII local parts, Slack `<mailto:…\|…>` | `[EMAIL]` |
| 2 | CNPJ | `NN.NNN.NNN/NNNN-NN` with each separator optional (incl. `12345678/0001-90`), 14 bare digits, 2026 alphanumeric format formatted or bare, case-insensitive | `[CNPJ]` |
| 3 | CPF | `NNN.NNN.NNN-NN` with `.`/`-` tolerated in any position and the last separator required (so bare 11 digits stay phones); with two or more separators only digits bound it (`CPF123.456.789-09` is masked), the looser `NNNNNNNNN-NN` shape keeps alphanumeric bounds; runs before the IPv4 guard, so an address of this exact 3.3.3.2 shape is masked as `[CPF]` (accepted, privacy first) | `[CPF]` |
| 4 | CURP | `[A-Z]{4}\d{6}[HMX][A-Z]{5}[A-Z0-9]\d`, case-insensitive | `[CURP]` |
| 5 | RFC | compact `[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}` (case-insensitive); separated `[A-ZÑ&]{3,4}[-\s]\d{6}[-\s][A-Z0-9]{3}` with the hyphen form case-insensitive and the space form uppercase-only (a case-insensitive space form would eat ordinary prose like "del 230415 com"); all forms require the 6 digits to be a valid YYMMDD date | `[RFC]` |
| 6 | phone | optional `+`/`(`, 10–14 digits total with runs of space/`-`/`.`/`()` between groups, matched only in the text outside guard spans; local `NNNN[- ]NNNN`, `NNNNN[- ]NNNN`; a local number behind an explicit area code — `(DD)`/`(0DD)`, or `+CC DD` — at any time; bare 8–9 digit numbers, and separated local forms with an optional `(DD)`/`(0DD)`/`DD`/`DD-` area code, only when immediately preceded (within up to 3 connector characters) by a phone keyword — `tel`, `teléfono`/`telefono`, `telefone`, `cel`, `celular`, `whats`, `whatsapp`, `número`/`numero`, `fone`, `ligue`, `llame`, `llamar` | `[PHONE]` |

Guards (spans the phone rule never searches inside): year lists/ranges (e.g. `2024-2025`,
`2024, 2025 2026`), year-month (e.g. `2024-09`), ISO/dotted/slashed dates and date ranges, IPv4
addresses (every octet ≤ 255), and amounts after a currency sign — bounded to a plausible number
(`\d{1,3}(?:[.,]\d{3})+` or `\d{1,6}`, optional 1–2 decimal digits, not followed by another digit)
so it cannot swallow an adjacent phone number sitting right after the currency symbol.
The area-code and keyword passes run before the guards, so `+55 11 2045-2078` or
`tel 11-2045-2078` are not taken for a year list.
Accepted over-masking (documented, pinned by tests): `tel 2024-2025` → `[PHONE]`,
`SKU BR-SPO-001-2024-01` → `[CNPJ]`, 8–9 digit order/ticket numbers after `número`/`numero`
(privacy first, D14 ruling; only the prompt copy is masked), bare 11-digit sequences (CPF or phone →
`[PHONE]`), 10–14-digit
IDs/timestamps not covered by a guard, and 14-character alphanumeric codes ending in two digits that
match the CNPJ shape but are not real CNPJs (`[CNPJ]`).
Not covered (documented): names, addresses, CLABE/bank accounts, card numbers, NF-e access keys,
obfuscated emails ("arroba"), secrets, attachments, bare 8–9 digit phones with no phone keyword
nearby, lowercase space-separated RFCs, homoglyph
tags, IDN email domains.
Synthetic PII in fixtures uses invalid check digits, `example.com` domains and repeated-digit phones.

### 8.7 Mock mode
`MockAdapter`: deterministic keyword rules (ES + PT) → valid output, `confianza=0.5` (so every mock
item is `needs_review` — documented), tokens 0, `model="mock"`, `billing="none"`.

### 8.8 Config — `LLMSettings` (batch, eval, API)
`LLM_PROVIDER` (unset → auto-selection §8.2), `LLM_MODEL` (default `claude-haiku-4-5`),
`LLM_TEMPERATURE` (§8.4), `PROMPT_VERSION` (active version), `LLM_TIMEOUT_SECONDS` (30, 5–120),
`LLM_MAX_RETRIES` (3, 0–5), `INVALID_OUTPUT_RETRIES` (1, 0–3), `LLM_CONCURRENCY` (4, 1–16),
`CONFIDENCE_THRESHOLD` (0.7, 0–1), `LOG_LEVEL` (`INFO`), credentials (§8.2), `APP_ROOT`
(package-relative default for `prompts/`, `migrations/`, `eval/`; never the cwd).
- Per-invoke deadline budget `deadline_s = LLM_TIMEOUT_SECONDS × (1 + LLM_MAX_RETRIES) +
  LLM_MAX_RETRIES × 30 + 10` (220 s with defaults). It is a **budget checked between attempts**,
  not a wall-clock kill: the Anthropic adapter owns all of its transport retries itself
  (`ChatAnthropic(max_retries=0)`), caps each `retry-after` wait at 30 s (negative values clamp to
  0, non-numeric ones fall back to backoff) and never starts a wait + attempt that cannot fit the
  remaining budget, but an attempt already in flight is bounded only by the httpx per-phase
  timeouts (`LLM_TIMEOUT_SECONDS` per phase), so one attempt can overrun the budget by up to one
  such timeout. The Agent SDK adapter enforces its budget with `anyio.fail_after` plus a 15 s
  cleanup margin. Spec 02's claim token (stale-window re-claim) is the safety net for any overrun;
  Spec 02 derives its stale window from this value (G33, G13).
- Tracing forced off: all four LangSmith/LangChain tracing env vars (`LANGSMITH_TRACING`,
  `LANGSMITH_TRACING_V2`, `LANGCHAIN_TRACING`, `LANGCHAIN_TRACING_V2`) are set to `false` in-process
  at startup and `langsmith.utils.get_env_var.cache_clear()` is called so any value LangSmith had
  already cached is invalidated; every graph run is additionally wrapped in
  `langsmith.tracing_context(enabled=False)`. `run_trees.configure(enabled=False)` is deliberately
  **not** used: it sets a process-global context var with no way back to "unset", which would leak
  its effect into unrelated code running afterwards (verified). If a LangSmith API key is present a
  WARNING says tracing is disabled (G31).
- Empty string = unset, except `LLM_TEMPERATURE` (§8.4).
- `evaluate`/`promote` read only what they need (`CONFIDENCE_THRESHOLD`, `PROMPT_VERSION`) and never
  validate providers or credentials.

## 9. Error handling

| Situation | Behavior |
|---|---|
| Anthropic: `APIConnectionError` (incl. timeout), status 408/409/429/≥ 500 after SDK retries, deadline exceeded | `LLMError("unavailable")` → `llm_unavailable` |
| Anthropic: other 4xx (400, 401, 403, 404, 413, 422) | `LLMError("rejected")` → `llm_rejected` |
| Agent SDK | mapping in §8.2 |
| No tool input, unparsable JSON, `max_tokens`, `refusal`, schema violation | feedback retry, then `invalid_output` |
| Unexpected exception (graph node, masking, final validation) | logged (class only, constant message); `Classifier.classify` raises `ClassificationCrash(error_type, attempts)`; batch records kind `unexpected` and continues; Spec 02 marks the row failed |
| Credential rejection (401/403, `authentication_failed`, `billing_error`, `CLINotFoundError`) | `llm_rejected`; batch stops submitting (remaining items `cancelled`) |
| Every item rejected | nothing written, exit 1; stderr lists each distinct `error_type` with its count (e.g. `APIStatusError:400 ×12`) and says "check the credential" only when a credential type is among them |
| Other rejection (e.g. 400/413 on one message) | `llm_rejected` for that item; batch continues |
| Input file not UTF-8, not JSON, or not a non-empty JSON list | `RunError`, exit 2 before any call |
| Bad config (provider, model, credentials, temperature, prompt version, ranges) | startup error, explicit message |

## 10. Gaps, edge cases, contradictions (this spec)

G1 `requiere_info` criterion · G3 `source_area` as masked context · G4/G30 temperature ·
G7 word limit via Pydantic + feedback · G8 pregunta language · G9/G34 masking formats and guards ·
G10 synthetic PII · G17 input limits · G18 delimiter neutralization · G23 strict schema stripping ·
G29/G32 Agent SDK isolation and env · G31 tracing egress · G33 per-invoke deadline budget
(checked between attempts; an in-flight attempt can overrun it — §8.8, Spec 02 claim token).
Other edge cases: already-masked text is unchanged by re-masking; mixed ES/PT → dominant language;
batch Ctrl-C cancels queued calls; the first credential rejection stops the batch (other
rejections do not); edge "maximum length"
message stays ≤ 4000 chars.

## 11. Tests (`apps/api/tests/`) — none touch the network

`conftest.py` (autouse) clears every variable in the Spec 04a inventory plus `LANGSMITH_*`,
`LANGCHAIN_*`, `ANTHROPIC_*`, `CLAUDE_CODE_*`, and checks `import pitz_pulse.api` works with an
empty env. A sentinel string is used to prove text never reaches logs.

| File | Cases |
|---|---|
| `test_schema.py` | exact ordered 10-field list and each enum's value set against **literal lists from the case**; valid object; unknown enum; resumen 0/20/21 words and > 200 chars; pregunta rules incl. `""`/`"  "`; strict rejects `"yes"`, `True` for confianza, `"0.8"`; extra field; id regex; message length; validator messages contain no input values |
| `test_masking.py` | positives: `+55 (11) 98765-4321`, `55 11 98765 4321`, `+52 1 55 1234 5678`, `5511987654321`, `98765 4321`, NBSP/en-dash separators, `a.b+tag@sub.example.com`, `<mailto:a@example.com\|a@example.com>`, `12.345.678/0001-90`, `12345678/0001-90`, 14 bare digits, alphanumeric CNPJ lower/upper/bare, CPF, CURP, RFC plain/hyphen/space; negatives: `error 500`, `2 horas`, `2024-2025`, `2026-09-28`, `28.09.2026`, `28.09.2026-30.09.2026`, `172.16.254.100`, `R$ 12.500.000`, `R$ 1.500,00`, `R$ 1.500.000.000,00`, `INC202409001`; bare 11 digits → `[PHONE]`; counts; idempotency |
| `test_prompts.py` | loads the three sections; invalid version / missing file → error; masked `message` and `source_area` in their blocks; `id` absent; `</message>`, `</ message>`, `＜/message＞`, forged `<message …>` and `<feedback>` in message or source_area are neutralized (exactly one block each); data-not-instructions sentence present; feedback rendered from the template; no 6-word shingle of any golden message in any prompt file |
| `test_tool_schema.py` | no `minimum/maximum/minLength/maxLength/pattern/default/title/$ref/$defs/allOf`; `pregunta_seguimiento` nullable; enum lists equal the StrEnums; 8 required fields, no `id`/`version_prompt`; `resumen` description mentions 20 words and Spanish |
| `test_graph.py` (FakeAdapter) | happy path; `INVALID_OUTPUT_RETRIES=0` → 1 invoke, `=1` → 2; invalid→valid with errors in the `<feedback>` block and no input values; `tool_input=None`, `max_tokens`, `refusal` → invalid; unavailable / rejected → one attempt each; invalid then unavailable → outcomes `[invalid_output, unavailable]`; `KeyError` from adapter → one `outcome=error` line then propagates; one log line per attempt with required fields; sentinel from message/source_area/invalid resumen never in any log record (DEBUG capture); adapter receives masked text only; `ClassifyOutcome.attempts` sums tokens/cost across attempts; `ClassificationError.attempts` present |
| `test_anthropic_adapter.py` (stubbed chat model / SDK) | temperature key absent from the request payload when `none`; forced tool_choice; strict per caps; empty `tool_calls` / `invalid_tool_calls` → `tool_input=None`; usage + cost; mapping: `APIConnectionError`, 408, 409, 429, 529, 500 → unavailable; 400, 401, 413, 422 → rejected; built with explicit `api_key` and default `base_url`; deadline exceeded → unavailable |
| `test_agent_sdk_adapter.py` (SDK stubbed) | full isolation option set; explicit `env` (timeout/retries from settings, no inherited secrets); JSON reply parsed; non-JSON → `tool_input=None`; `ResultMessage(is_error, api_error_status=529)` → unavailable; `AssistantMessage.error="authentication_failed"` → rejected; `CLINotFoundError` → rejected; `ProcessError` with sentinel stderr → no sentinel in `LLMError` or logs; equivalent cost; semaphore caps concurrent calls; deadline → unavailable; model mismatch warning |
| `test_mock_adapter.py` | deterministic valid output for all 12 case messages; effective model `mock` |
| `test_models_catalog.py` | lookup by (provider, model); Agent SDK rows have no temperature/forced/strict and `subscription` billing; unknown pair → error |
| `test_config.py` | defaults (no env) → mock, model `mock`, no error; auto-selection from one credential; both credentials → error; selected provider without credential → error; forbidden env vars → error; quoted credential → error; temperature unset/`none`/`0.2`/empty/`abc`/`1.5`/`nan`; temperature with Sonnet 5 or Agent SDK → error naming `none`; ranges for retries/concurrency/timeout; `PROMPT_VERSION` format; tracing forced off |
| `test_runs.py` | stem includes set; model id sanitized; suffix validation; paths confined to `eval/runs/`; `write_pair` writes meta first and leaves no temp file on failure; results hash matches |
| `test_batch.py` | peak in-flight == min(concurrency, n) via an Event gate; output sorted, 10 keys with `null` kept; meta fields incl. totals across retries, failures list, hashes; partial failure → exit 1 with files written; existing stem without `FORCE` → exit 2 and files byte-identical; meta `tool_schema_sha256`; first credential rejection cancels remaining calls; non-list or empty input → exit 2; KeyboardInterrupt cancels queued futures; duplicate/invalid input → exit 2 before any call and stderr has no message text |
| `test_logs.py` | at `LOG_LEVEL=DEBUG`, `anthropic`/`httpx`/`httpcore`/`httpx2`/`httpcore2`/`langchain`/`langgraph` records below WARNING are suppressed (asserted after `import anthropic`) |
| `test_anthropic_wire.py` | real langchain-anthropic + anthropic stack with only the httpx2 transport mocked: wire body has `temperature`, forced `tool_choice`, `strict` inside the tool; canned `tool_use` reply parsed |
| `test_agent_sdk_boundary.py` | `claude -v` timeout/OSError → `ConfigError`; non-SDK exceptions propagate; control timeout → unavailable; stderr logged as length only; SDK-built argv and merged child env keep the isolation flags and blank inherited secrets |
| `test_batch_window.py` | window refills while a slow item is in flight (a submit-all mutant fails); unexpected exceptions → `unexpected`, pool shut down; credential rejections stop the batch, other rejections do not |

## 12. Acceptance

`make test` green · `make lint` clean · every source file < 300 lines · mock batch writes a run +
meta under `eval/runs/` · one **announced** real call per available real provider using a synthetic
smoke message (never an `MSG-xx` message) proves the live API accepts the tool schema /
plain-JSON reply. Per D29 this call is not made during development: it runs when the real integration is activated with Pitz's key (README).
