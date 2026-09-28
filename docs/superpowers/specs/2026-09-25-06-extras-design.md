# Spec 06 — Extras: CI, model/cost comparison, duplicates, Slack

- **Covers:** X2, X4, X3, X1 (build order: cheapest and most valuable first)
- **Depends on:** Parts 1–4 verified. Each extra is independent; anything not built goes to README
  "pending" with the plan below.
- **Status:** 06a (CI), 06b (comparison tooling, no paid rows under D29) and 06c (near-duplicate
  resubmission detection) implemented on feat/spec-06cd; 06d not started

---

## 06a — CI (X2)

```
push / PR ──► GitHub Actions
                ├─ job api: setup uv ─► uv sync --frozen ─► ruff check ─► pytest
                └─ job web: npm ci ─► typecheck ─► vitest
                           └─ setup uv ─► make web-types ─► git diff --exit-code (types fresh)
```
State per job: `queued → running → passed | failed`.
No secrets exist in CI, which proves the tests never call a real provider. Cache uv/npm by lockfile
hash. Evidence: link to a green run.

---

## 06b — Model / cost comparison (X4)

**Goal:** a measured cost-vs-quality table, not a claim. Never touches `/resultados.json`.

```
make compare MODELS="claude-haiku-4-5 claude-sonnet-4-6 claude-sonnet-5" [PROVIDER=anthropic_api]
   preflight: every (provider, model, temperature) validated before the first paid call
   for each model: batch SET=case and SET=edge with SUFFIX=cmp
                   ─► eval/runs/<set>__<prompt>__<provider>__<model>__cmp.json + meta
   ─► compare script reads every meta + scoring results
   ─► table: model · temperature · accuracy per field · exact-match · mean in/out tokens ·
             cost/msg (incl. retries) · p50 latency · measured prompt-prefix tokens
```
- `claude-sonnet-5` runs with `LLM_TEMPERATURE=none` (not supported, D2); the table shows `temperature: —`.
- Optional row: the same model through `claude_agent_sdk` (subscription; equivalent API cost shown).
- Compare runs are never promoted (D23).
- **"Simple case" routing** is proposed only if defined and measured: simple = categories where the
  cheap model's accuracy equals the expensive model's on both sets, AND cheap-model
  `confianza ≥ CONFIDENCE_THRESHOLD`. N = 12 (+ edge) and G19 noise stated next to the table.
- **Caching (G13):** prefix tokens (tools + system) measured with the provider's token counting per
  prompt version, shown next to each model's cache minimum (Haiku 4.5: 4096). Caching is only
  enabled where the prefix qualifies; the prompt is never padded to trigger it.
Tests: compare-table math from synthetic metas and reports.

---

## 06c — Near-duplicate resubmission detection (X3)

**Goal:** flag near-identical resubmissions (same text re-sent with small edits). Topic-level
similarity ("third case like this this month") needs embeddings — listed as the upgrade path.

```
TriageService.create ─► classify ok ─► before complete() (read-only, no write lock held):
   candidates = last N=200 classified rows, same idioma, id != current
   normalized = lowercase, strip accents/punctuation, collapse spaces, truncate 1000 chars
   len(normalized) < 30 ─► skip (short-message guard)
   SequenceMatcher(autojunk=False): real_quick_ratio ─► quick_ratio ─► ratio ≥ DUPLICATE_THRESHOLD
   ─► possible_duplicate_of = best id (else null), written by complete() in its transaction
```
- `migrations/002_possible_duplicate.sql` adds the column. stdlib only.
- `possible_duplicate_of` exists on the API Item only — never on `Classification` or run files;
  web types regenerated in the same change.
- Never blocks creation, never merges. Concurrent near-duplicates are not detected (documented).

```
DuplicateDetector(window, threshold, min_len)
 + find(message, candidates: list[(id, text)]) -> str | None
```
State: `classified → checked → flagged | unique`.
Tests: identical → flagged; small edit → flagged; different wording → not flagged; short-message
guard; window bound; never flags itself; quick-ratio prefilter does not change results.

---

## 06d — Slack Events endpoint (X1)

**Goal:** receive `message` events, classify, reply in thread — reusing `TriageService`.

```
POST /slack/events         (mounted only when SLACK_SIGNING_SECRET and SLACK_BOT_TOKEN are set;
   │                        otherwise 404 + one "slack_disabled" log at startup)
   ▼  async def (documented exception to D5: needs raw body bytes)
verify: timestamp header present, integer, within 5 min;
        hmac.compare_digest("v0=" + HMAC_SHA256(secret, f"v0:{ts}:{raw_body}"), X-Slack-Signature)
   │ fail ─► 401            (auth is the signature; X-API-Key is NOT required here)
   ▼
type == url_verification ─► 200 {challenge}
   ▼
process only: type == event_callback, event.type == message, no subtype, no bot_id,
              thread_ts absent or == ts                         (else 200, ignored)
   ▼
BackgroundTasks.add(process, event) ─► 200 immediately
   ▼ background (the app's single TriageService: shares its model-slot semaphore, Spec 02 §2)
create(id = "slack-" + event_id, message = text, source_area = SLACK_CHANNEL_AREAS.get(channel))
   ▼ created=True (this call classified)
chat.postMessage(channel, thread_ts = ts, text = reply)
```
Reply template: categoria · prioridad · area_sugerida · resumen; `pregunta_seguimiento` when
`requiere_info`; "a human will review this" when `needs_review`.

| Background outcome | Action |
|---|---|
| created=True | reply in thread |
| created=False (Slack retry, already classified) | nothing (no second reply) |
| InProgress | ignore (the first worker replies) |
| ValidationError (empty/too long) | short reply explaining the limit + log |
| ClassificationFailed / Busy / DbBusy / ClassificationCrash | log (event_id, class name) + reply "could not classify, a human will review" |
| IdConflict (same event id, different text) | error log with event_id, no reply |
| any other `DomainError` or exception | error log with event_id and class name, never re-raised from the task |
| postMessage `ok:false` / 429 | error log with event_id |

```
SlackVerifier  + verify(headers, raw_body, now) -> None      raises InvalidSignature
SlackNotifier  + reply(channel, thread_ts, text) -> None       httpx + SLACK_BOT_TOKEN
slack_routes.py  route, filter, background wiring
```
State: `received → verified → acked → classifying → replied | ignored | failed(logged)`.
Dependencies: `httpx` becomes runtime (already a dev dep) — no `slack_sdk`. Bot scopes:
`channels:history`, `chat:write`. `SLACK_CHANNEL_AREAS` optional (`C123=Comercial MX,…`); unknown
channel → `source_area = null`.
Production caveat: `BackgroundTasks` dies with the process; production uses the durable enqueue in
DECISIONES §5.
Tests: valid / invalid / expired / missing-timestamp signatures; challenge; bot message, edit and
thread reply ignored; valid message → exactly one `reply(channel, thread_ts=ts)`; Slack retry → no
second reply; no `X-API-Key` needed; background task persists a row; failure table rows; Slack
`<mailto:a@b.com|a@b.com>` markup is masked before the LLM; endpoint absent when secrets are empty.

---

## Not planned (DECISIONES "two more weeks")

MCP server exposing `list_solicitudes` / `correct_solicitud` over the API; multi-turn clarification
nodes in the LangGraph graph for Slack; Postgres + real queue; SSO; more provider adapters.
