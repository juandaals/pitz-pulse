# Spec 01 implementation-gate review — 2026-09-26

Target: branch `feat/spec-01-classification-core`, `c6ff531..0026952` (16 commits, 46 files).
Workers: 4 dr-strange on the most capable model (classify flow · providers + config · batch → runs ·
cross-cutting + merge readiness). Two workers stalled on a stream watchdog and were resumed; all four
completed. High findings were re-executed by the orchestrator before acceptance.

## Tool suite (run once by the orchestrator at 0026952)

```
uv run pytest            311 passed
ruff format --check      clean
ruff check               clean
source files ≥ 300 lines none
tracked .env/.pdf/.db/.tmp none
```
Workers additionally verified: suite passes per file, in reverse file order, with a socket guard
(no network) and with `-W error`.

## Review summary

The architecture holds: masking before the graph, one `llm_call` log per attempt, code-owned `id` /
`version_prompt`, no text in logs or feedback, meta/run mismatch always detectable, a real SIGINT
exits 130 writing nothing, the Anthropic wire body carries `temperature: 0.0`, forced tool choice
and `strict`, and the Agent SDK child argv/env is isolated (verified with a fake CLI binary, no
network). The weak spots are the regex masking long tail, batch resilience, and tests that pin
shapes rather than the real boundary. Nothing has yet been verified against the live API.

## Findings (deduped, severity-sorted)

| # | Sev | Slice | Anchor | Finding | Fix (wave 1) | Verified |
|---|---|---|---|---|---|---|
| G1 | High | classify | masking.py `_PHONE` | Phones wrapped in `_…_` (Slack/Markdown italics) never masked: `\w` lookarounds include `_` | Alphanumeric-only lookarounds + fixtures | re-executed |
| G2 | High | classify | masking.py year guard + keyword rule | Year-shaped landlines (`tel (11) 2045-2078`, `whatsapp 2045 2078`) swallowed by the year-list guard even after a phone keyword | Keyword pass accepts separated local forms and runs before guards | re-executed |
| G3 | High (gate) | cross | Task 13 step 4 | Strict schema / plain-JSON Agent SDK reply never accepted by the live API; Anthropic tests stub the whole stack | Offline MockTransport test now; announced real smoke before any Spec 03 run | ledger |
| G4 | Med | classify, cross | masking.py `normalize` | Unicode format chars (U+200B/00AD/2060/200E) inside PII or tags bypass every rule | Strip category Cf before NFKC | re-executed |
| G5 | Med | classify | masking.py CNPJ/CPF, IPv4/date guards | CPF/CNPJ separator typos leak (`123.456.789.09` claimed by IPv4 guard) | Separator-tolerant CPF/CNPJ; IPv4 guard octets ≤ 255 | re-executed |
| G6 | Med | batch | batch.py `run_batch` | Window refills only after the whole snapshot finishes: one slow call drops concurrency to 1 | `wait(FIRST_COMPLETED)` loop | measured by worker |
| G7 | Med | batch, cross | batch.py `_record`, classifier.py | Exceptions other than ClassificationError/Crash escape `run_batch`: batch lost, pool not shut down | `except Exception` → `unexpected`; classifier wraps masking and final validation | reproduced by worker |
| G8 | Med | batch | batch.py stop rule | Any 400/413 (`llm_rejected`) stops the whole batch; edge set can never complete | Stop only on credential rejections (401/403/auth/billing/CLI missing) | code citation |
| G9 | Med | providers | logs.py | anthropic 1.8 uses `httpx2`/`httpcore2`; unpinned, INFO line per call; test audits old names | Pin both; test after `import anthropic` | reproduced by worker |
| G10 | Med | providers | claude_agent_sdk.py `check_ready` | `TimeoutExpired` escapes as a traceback, not ConfigError; no test | Catch SubprocessError → ConfigError; tests | code citation |
| G11 | Med | providers | claude_agent_sdk.py `_run` | Every exception mapped to retryable `unavailable` (programming errors absorbed) | Map only SDK errors + control timeout; re-raise others | code citation |
| G12 | Med | providers | claude_agent_sdk.py options | CLI stderr inherited raw (bypasses JSON logging) | `stderr` callback logging a constant event with a line count | code citation |
| G13 | Med | providers, cross | anthropic_api.py | Deadline is a budget check between attempts; an in-flight attempt is bounded by per-phase httpx timeouts | Document as best-effort; Spec 02 keeps the claim-token guard | code citation |
| G14 | Med | cross | schema.py descriptions | Rubric text in tool-schema descriptions is outside `prompt_sha256` | Meta `tool_schema_sha256`; Spec 03 promote checks it | code citation |
| G15 | Med | batch, classify, cross | tests | Tests cannot tell window from submit-all+cancel (mutant passes); D14 leak test can be vacuous; tracing wrapper untested; SDK argv/env merge untested; Anthropic real stack untested | Add the missing tests listed below | mutation run by worker |
| G16 | Med | cross | spec 01 §4/§5/§9 | `ClassificationCrash`, `LLMCall.actual_model/transport_retries`, state `sink` undocumented | Docs amendment | code citation |
| G17 | Low | several | — | Negative retry-after crashes; undeclared direct deps (`langsmith`, `anyio`); empty-input message; non-list JSON TypeError; misleading SDK zero-retries message; stale mock comment | Fixed in wave 1 where one-liners | code citation |

Deferred with rulings (not in wave 1): `número` keyword over-masking (privacy-safe direction,
documented); homoglyph tags; IDN emails; proxy variables not forbidden (TLS stays end-to-end);
Ctrl-C meta-only record; eval-side hash checks (Spec 03 scope); ApiSettings construction note and
API-wide concurrency bound (Spec 02 plan); red intermediate commit 1c86f20 and missing commit bodies
(history rewrite needs the candidate's consent).

## Coverage ledger

| Dimension | classify | providers | batch | cross |
|---|---|---|---|---|
| contract fidelity | ✔ | ✔ | ✔ | ✔ |
| concurrency | – | ✔ | ✔ | ✔ |
| failure / retries | ✔ | ✔ | ✔ | ✔ |
| provider boundary | ✔ | ✔ | – | ✔ |
| privacy & logging | ✔ | ✔ | ✔ | ✔ |
| prompt injection | ✔ | ✔ | – | ✔ |
| data fidelity | ✔ | – | ✔ | ✔ |
| eval integrity | ✔ | – | ✔ | ✔ |
| cost | – | ✔ | ✔ | ✔ |
| engineering rules / tests / history | ✔ | ✔ | ✔ | ✔ |

Not reviewed: idempotency, auth, migrations, delivery (Specs 02/04, not implemented yet).

## Open concerns (need a real, announced call)

- Live acceptance of the strict tool schema and of the Agent SDK plain-JSON reply.
- `strict_mcp_config` vs claude.ai connectors; `--no-session-persistence` without `--print`;
  macOS Keychain fallback if the OAuth token is rejected.

## Outcome (fix wave + scoped re-review)

Fix wave `0026952..49b5aa1` (6 commits), scoped re-review by one dr-strange, residual pass
`49b5aa1..ef2ac98` (4 commits). Verified by the orchestrator at `ef2ac98`:

```
uv run pytest            386 passed
ruff format --check      clean
ruff check               clean
source files ≥ 300 lines none (batch.py split into batch.py + batch_run.py)
```

| # | Final state |
|---|---|
| G1, G4–G12, G14–G17 | Addressed (re-review verdicts + probes) |
| G2 | Addressed in residual pass: area/country-coded and keyword landlines masked |
| G5 | Addressed; re-review regression H1 (CPF glued to a letter) fixed in residual pass |
| G13 | Addressed: Spec 01 and Spec 02 state the deadline is a budget; the G24 claim token is the re-claim guarantee |
| G3 | Open by ruling — needs one announced real call |
| Re-review M1 | Bare `Exception` from the SDK query → `unavailable` / `SDKControlError` |
| Re-review M2 | All-rejected summary names error types; credential hint only for credential types |
| Re-review L4 | `tool_schema_sha256` is null when the provider does not send the tool |

Accepted (prompt-only over-masking, pinned by tests): separated alphanumeric codes as `[CNPJ]`,
keyword + year list as `[PHONE]`, area-coded `NNNN-NNNN` masked without a keyword.
Deferred: CPF with `/` or `,` separators; edge-file skip in `test_prompts.py` (Spec 03).
D28 (gate rulings) is proposed, pending the candidate.
