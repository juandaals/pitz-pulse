# Decisiones

## 1. Prompt design

The system prompt (`apps/api/prompts/v1.md`) holds the rubric: what each category, priority and
area means, boundary rules for the ambiguous cases, when `requiere_info` is true, and anchors for
`confianza`. The message goes in the user turn inside a delimited block and is treated as data, so
instructions inside it are ignored (the edge set carries a prompt-injection message to check this
with a real model). The answer comes
through a forced tool call with a `strict` JSON schema; pydantic then enforces what JSON Schema
cannot (`resumen` ≤ 20 words, the question only when `requiere_info`). An invalid answer is retried
once with the validation errors fed back — a plain retry at temperature 0 would repeat the same
mistake. `id` and `version_prompt` are set by code, never by the model.

Discarded: **few-shot examples from the golden sets** (they would leak the evaluation data and
inflate accuracy — a test forbids any overlap); **free-text JSON parsing** (tool use gives
schema-valid arguments); **a single call without a graph** (the feedback retry is a real loop and
LangGraph leaves room for multi-turn clarification); **the Agent SDK as the only path** (it cannot
set temperature 0, which the case requires).

## 2. `confianza`

`confianza` below `CONFIDENCE_THRESHOLD` sends a request to the review queue
(`GET /solicitudes?needs_review=true`); a human PATCH (even a confirmation with no changes) takes
it out. The flag is computed when reading, so a new threshold applies to existing rows. The
evaluation prints a threshold sweep (how many wrong answers each threshold would catch) to choose
the value from data. Caveat: model-reported confidence is not calibrated by default. With no real
run yet, 0.7 is a placeholder and there is no calibration evidence (G35).

## 3. Masking and privacy

Before any provider call the text is masked: emails, phones (BR/MX, with and without keywords),
CPF, CNPJ (numeric and the 2026 alphanumeric format), CURP and RFC; invisible Unicode characters
are stripped first so they cannot hide PII. Dates, years, amounts, versions and IPs are guarded
against over-masking. Residual risks: names and free-form identifiers are not masked; the original
text is stored in the database (reviewers need it), so the database is internal-only; some
number-like strings after a phone keyword are masked on purpose (privacy first). Provider side:
use an organization API key scoped to this service, check the provider's retention and
no-training terms and data region before production, and never use a personal subscription token
for company data (the Agent SDK path exists for development).

## 4. Cost (estimate — no real run was made)

Per message, from the rendered prompt and tool schema (≈ 4,600 characters) plus an average case
message (≈ 115 characters): about 1,800 input tokens and 150 output tokens. At Haiku 4.5 list
prices in `models_catalog.py` (US$1 / US$5 per million input / output tokens) that is ≈ US$0.0026
per call; adding ~5 % invalid-output retries gives ≈ US$0.0027 per message.

| Volume per month | Estimated cost |
|---|---|
| 500 messages | ≈ US$1.4 |
| 50,000 messages | ≈ US$135 |

The real figure comes from the `llm_call` logs (tokens and cost per attempt, retries included) and
the run metadata once Pitz's key is used. At 50,000 per month: send non-urgent traffic through the
Batch API (about half price), skip duplicates before calling the model, cache the static prompt
prefix once it passes the model's minimum cacheable size, and route easy messages to the cheapest
model while escalating low-confidence ones.

## 5. Slack in production

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

The signature replaces the API key on that route. The event id makes Slack's retries harmless
because intake is idempotent. The reply only happens for the call that actually classified. At
scale the background task becomes a durable queue so a restart loses nothing.

## 6. With two more weeks

1. Build an evaluation set from real PATCH corrections, the best signal of where the model fails.
2. Multi-turn clarification in the graph: when `requiere_info`, ask, wait, reclassify.
3. An MCP server over the API so agents can triage and query requests.
4. Postgres and a real queue instead of SQLite and in-process concurrency.
5. SSO for reviewers and an audit trail per correction.
6. More provider adapters behind the same interface, compared on the same golden sets.
