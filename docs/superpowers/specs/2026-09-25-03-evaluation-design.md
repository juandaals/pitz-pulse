# Spec 03 — Evaluation, golden sets & prompt iteration

- **Covers:** R3.1, R3.2, R1.9 (promotion to `resultados.json`); tooling for R3.3 and R1.10 —
  their evidence (a measured v1 → v2 iteration, a sweep-derived threshold) is **blocked on a real
  run with Pitz's key** (G35)
- **Depends on:** Spec 01 as built (`python -m pitz_pulse.batch --set case|edge [--suffix]
  [--force]`, run files + meta with `results_sha256`, `input_sha256`, `prompt_sha256`,
  `tool_schema_sha256`, `runs.run_paths/canonical_sha256`), Spec 02 (`review.needs_review`),
  candidate-approved labels. Spec 04a wraps the CLIs below in `make` targets.
- **Status:** implemented on feat/spec-03-evaluation (rev 5); edge labels pending candidate
  approval. Reviews: `docs/superpowers/reviews/2026-09-27-03-spec-review.md`,
  `docs/superpowers/reviews/2026-09-27-03-plan-review.md`
- **Decisions used:** D12, D14, D15, D19, D20, D23, D29, D31 (see `docs/MASTER.md`)

> **No real model calls in development (D29).** Every tool here is built and tested with the mock
> provider. `resultados.json` is produced from a mock run with the explicit `--allow-mock` flag
> (D31) and says so in its meta; the README gives the exact commands to regenerate it — and to run
> the v1 → v2 iteration with real numbers — with Pitz's key.

## 1. Goal

Know — with numbers, independent of any model — whether the classifier works, where it fails,
whether `confianza` predicts failure, and whether a prompt change helped.

## 2. Golden sets (model-agnostic, D20)

| Set | Messages | Labels | Author |
|---|---|---|---|
| `case` | `/mensajes.json` (the 12 case messages) | `/etiquetas_esperadas.json` | drafted by the assistant at the candidate's request, approved by the candidate 2026-09-25 (disclosed in README/AI_LOG) |
| `edge` | `apps/api/eval/golden/edge_cases.messages.json` | `apps/api/eval/golden/edge_cases.labels.json` | messages drafted by the assistant; labels proposed as `draft`, each approved or changed by the candidate |

Golden files contain only input and truth — never a model, provider or prompt version. Any run
from any provider/model/prompt is scored against the same files. No golden message is ever used
as a prompt example (D14).

Edge set (15–20 messages) covers: each PII type, prompt injection, mixed ES/PT, very vague, two
requests in one message, a long message (within Spec 01 limits: ≤ 4000 characters stripped,
≤ 8000 raw, so `runs.load_requests` accepts the file), emoji/Slack markup, explicit urgency words
without real impact, real impact without urgency words. A third language is left out: the
contract's `idioma` only admits `es`/`pt`, so no label could be right. It also mitigates G20
(tuning to 12 messages): a prompt change must not regress the edge set. It is drafted by the same
assistant that wrote v1, so it is a regression guard, not an independent benchmark.

Edge labels are `draft` until the candidate approves them at the end of Spec 03 (D31); runs made
before that report every edge item as not scored.

## 3. Label file format

```json
[
  {
    "id": "EX-01",
    "categoria": "consulta",
    "prioridad": "baja",
    "area_sugerida": "producto",
    "idioma": "es",
    "requiere_info": false,
    "label_status": "approved",
    "justificacion": "One line, only for doubtful cases."
  }
]
```
(`EX-01` is a synthetic illustration; fixtures and examples never use `MSG-xx` values.)
`label_status` is `approved` or `draft`; only `approved` labels are scored — draft ones are listed
in the report as "not scored". `etiquetas_esperadas.json` entries are always `approved`.

Scored by exact match: `categoria`, `prioridad`, `area_sugerida`, `idioma`, `requiere_info`.
Free-text fields are checked by rules only: `resumen` 1–20 words; `pregunta_seguimiento` present
iff `requiere_info`.

## 4. Components

| File | Responsibility |
|---|---|
| `apps/api/src/pitz_pulse/evaluate.py` | CLI `python -m pitz_pulse.evaluate --run <stem> [--compare <stem>] [--threshold T]`: load + validate, call `scoring`, print Markdown; exit codes |
| `apps/api/src/pitz_pulse/scoring.py` | Pure functions: `score(labels, results, threshold)`, `compare_runs(a, b)`, `threshold_sweep`; "routed to review" uses Spec 02's `review.needs_review` (strict `<`) |
| `apps/api/src/pitz_pulse/promote.py` | CLI `python -m pitz_pulse.promote --run <stem> [--allow-mock]`: verify (below), then write `/resultados.meta.json` then `/resultados.json` (atomic, meta first) |
| `apps/api/src/pitz_pulse/labels.py` | `Label` model and `load_labels(path)` (shared by evaluate and the golden-file tests) |
| `apps/api/eval/runs/` | Run files from Spec 01 batch (`<set>__<prompt>__<provider>__<model>[__<suffix>].json` + `.meta.json`), committed after every real run (D26) |
| `apps/api/prompts/CHANGELOG.md` | Per version: change, hypothesis, eval before → after, stability note |

No new dependencies.

## 5. Flows

```
make classify SET=case|edge [SUFFIX=b] [FORCE=1]  (Spec 01 batch; writes only eval/runs/)
make eval RUN=<stem> [COMPARE=<stem>] [THRESHOLD=<default CONFIDENCE_THRESHOLD>]
   │  evaluate and promote never build LLMSettings (no provider/credential checks): they read
   │  only APP_ROOT, CONFIDENCE_THRESHOLD (0–1, default 0.7) and config.ACTIVE_PROMPT_VERSION,
   │  so they work with the D29 `.env`. Host-only (uv); not run inside the container.
   ▼
load labels ─ extra/missing field, bad enum, duplicate id ─► exit 2 (explicit message)
load run + meta ─ missing meta, results_sha256 mismatch, len ≠ meta.n, duplicate result id,
                 any item's version_prompt ≠ meta.prompt_version,
                 meta.input_sha256 ≠ sha256(current SETS[meta.set] file) ─► exit 2
labels chosen by meta.set (case → /etiquetas_esperadas.json, edge → edge labels)
   │ results validated STRUCTURALLY with Spec 01 `ClassificationShape` (no rule validators),
   │ so rule violations can be reported instead of crashing (G28)
   ▼
meta.provider == "mock" ─► header "MOCK RUN — NOT MODEL QUALITY"
align by id (approved labels only):
   missing result  → wrong in all 5 fields, failure row got="<missing>", excluded from confidence
   result for a draft-labelled id → not_scored_draft
   extra result id (no label at all) → listed under extra_ids, not scored
   0 approved labels → every percentage "n/a" (no division by zero), exit 0
   ▼
per-field accuracy (denominator = approved labels) · exact-match (all 5) · rule violations
per-message confianza vs correct · "routed at T" line · threshold sweep 0.50–0.90 step 0.05
(routed to review, catch rate; "wrong" = not all 5 exact among present results)
rule violations = items that fail Spec 01 `Classification` validation (the contract rules
themselves, not a reimplementation); batch runs never contain them (invalid items become
failures), so the section is non-empty only for hand-edited or foreign files
COMPARE given → both runs must share meta.set and input_sha256 (else exit 2); header shows
both models, prompt_sha256 and tool_schema_sha256; diff = the 5 scored fields plus confianza
changes > 0.05; "noise floor" = per-field count of ids whose value flipped between two v1 runs
   ▼
Markdown to stdout · exit 0

make promote RUN=<stem> [ALLOW_MOCK=1] [FORCE=1]   refuses (exit 2, never a traceback) unless ALL hold (D23, D31):
   meta.provider == "anthropic_api", meta.model == config.DEFAULT_MODEL and
   meta.temperature is the number 0 (a JSON `false` is rejected)
     — or, only with --allow-mock, meta.provider == "mock" (temperature not checked);
       claude_agent_sdk is never promotable; a (provider, model) pair missing from the catalog
       is refused · the stem has no suffix (compare/stability runs are never promoted, Spec 06)
   meta.set == "case" · meta.failures empty · len == meta.n · the sorted id list equals the 12
   ids of /mensajes.json exactly · every item's version_prompt == meta.prompt_version ·
   results_sha256 matches
   meta.input_sha256 == sha256(/mensajes.json) · every item passes full Classification validation
   meta.prompt_version == active version · meta.prompt_sha256 == sha256 of the current
   prompts/<version>.md · meta.tool_schema_sha256 == canonical hash of the tool schema the
   run's provider would send today (strict flag from the catalog caps)
   a mock promote over an existing /resultados.meta.json with `mock: false` is refused unless
   --force (a real result is never silently replaced by mock output)
   then writes /resultados.meta.json (run meta + `promoted_at`, `source_run`, `mock`: true or
   false, always explicit), then /resultados.json as a byte-for-byte copy of the run file, so
   the meta's results_sha256 verifies it (atomic)
```

The only thing that writes `/resultados.json` is `make promote` (D19).

## 6. Report shape

```
## Eval — set case · run case__v1__anthropic_api__claude-haiku-4-5 · temperature 0 · labels <sha> · 12 scored
| field          | accuracy     |
| categoria      | 11/12 (92%)  |
| …              |              |
| all 5 fields   |  8/12 (67%)  |

### Failures        | id | field | expected | got | confianza |
### Rule violations | id | rule |
### Confidence      | id | confianza | all-fields correct |
### Threshold sweep | threshold | routed to review | wrong caught | catch rate |   (n/a when 0 wrong)
### Diff vs <stem>  | id | field | this run | other run |          (only with COMPARE; both runs must share meta.set)
```

## 7. Class diagram

```
Label (pydantic, extra=forbid)                 ClassificationShape (Spec 01)
 id, categoria, prioridad, area_sugerida,       contract fields with types/enums only
 idioma, requiere_info, label_status,           (no rule validators)
 justificacion?
        │                                               │
        └──────────────┬────────────────────────────────┘
                       ▼
score(labels, results, threshold) -> EvalReport          compare_runs(a, b) -> list[Diff]
threshold_sweep(pairs) -> list[SweepRow]                  Diff(id, field, a, b)

EvalReport (dataclass)
 scored: int · not_scored_draft: list[str] · field_accuracy: dict[str, (int, int)]
 exact_match: (int, int) · failures: list[Failure] · missing_ids · extra_ids
 rule_violations: list[(id, rule)] · per_message_confidence: list[(id, float, bool)]
 sweep: list[SweepRow]
 + to_markdown(header) -> str
```
`scoring.py` is pure (no I/O); `evaluate.py` owns files, printing and exit codes.

## 8. State diagram — prompt version lifecycle

```
┌───────┐ classify+eval ┌───────────┐ better on case AND no edge  ┌────────┐ make promote ┌──────────┐
│ draft │──────────────►│ evaluated │─── regression beyond noise ►│ chosen │─────────────►│ active   │
└───────┘               └─────┬─────┘                             └────────┘              └────┬─────┘
                              │ worse / overfit / within noise                                  │ superseded
                              ▼                                                                 ▼
                        ┌───────────┐ (kept in repo + CHANGELOG as evidence)              ┌─────────┐
                        │ discarded │                                                     │ retired │
                        └───────────┘                                                     └─────────┘
```
v1 is active by default. The active version has one source of truth — code default = compose
default = `.env.example`, enforced by a test (Spec 04a); promote reads it from the code default.

## 9. Iteration protocol (R3.3)

Under D29 this protocol is delivered as tooling plus documentation: it is exercised end to end
with mock runs (proving the commands, hashes and reports work), and the README lists the exact
command sequence to produce real v1/v2 numbers with Pitz's key. No v2 prompt is written from mock
results — mock output carries no quality signal, so "improving" against it would be fiction.

1. Run v1 twice (`SUFFIX=b`) → `COMPARE` gives the noise floor (G19). Record in CHANGELOG.
2. Read failures; write the hypothesis as a **general rule** (G20).
3. v2 with one change; run on case and edge sets; record per-field before → after.
4. Deltas within the v1 noise floor are reported as "no measurable change".
5. Choose `CONFIDENCE_THRESHOLD` from the sweep; record it.
6. Promote the chosen run; README gets the last report and the v1 → v2 story.

Every real run is announced with provider, model and expected call count before it runs.

## 10. Gaps, edge cases, contradictions (this spec)

- **G1** `requiere_info` → candidate-approved labels + `justificacion`.
- **G2** calibration measured with per-message table and sweep, not assumed.
- **G19** non-determinism → noise floor from a repeated run; Agent SDK runs have no temperature
  control (meta `temperature: null`): usable for development evals, never promoted.
- **G20** overfitting → edge set as regression guard; future set from PATCH corrections.
- **G21** mock runs flagged (report header, meta `mock`); promotable only with `--allow-mock`
  (D31). `resultados.json` itself cannot carry a marker (contract fields), so the README's first
  line about it says it is mock output (the mock `resumen` and a constant `confianza` 0.5 also
  give it away). Mock case scores are high by construction (the mock's keywords come from the
  case messages, `providers/mock.py`): never shown as model quality.
- **G35** no real run under D29 → no measured v1 → v2 iteration (R3.3), no sweep-derived
  threshold (D12: `CONFIDENCE_THRESHOLD=0.7` is a documented placeholder) and no calibration
  evidence (R1.10); the mock sweep is degenerate (constant 0.5) and is not presented as evidence.
- **G28** rule checks need structural (not contract) validation of results.
- 0 scored labels or zero wrong → `n/a`, no division by zero.
- Sonnet via `anthropic_api` can never be promoted (the catalog marks no temperature support).
- Label typos (e.g. `automatización`) → exit 2, not silent mismatch.

## 11. Tests

| File | Cases |
|---|---|
| `test_scoring.py` | perfect match; one wrong field; missing result (all 5 wrong, excluded from confidence); extra result id; draft labels not scored; rule violation (21-word resumen) reported; sweep math incl. zero wrong → n/a; `compare_runs` diffs; markdown snapshot of a small synthetic fixture |
| `test_evaluate_cli.py` | `main()` with tmp files: results_sha256 mismatch / len ≠ n / version_prompt ≠ meta → exit 2; COMPARE across different sets → exit 2; invalid label enum / extra field / missing field / duplicate id → exit 2 with message; invalid result item → exit 2; missing meta → exit 2; mock meta → header; valid → exit 0 |
| `test_promote.py` | writes meta then run; `--allow-mock` promotes a mock case run and marks `mock: true`; without it mock is refused; `--allow-mock` never admits `claude_agent_sdk`; refuses each D23 violation separately (incl. tool_schema_sha256 ≠ current): provider ≠ anthropic_api (mock, claude_agent_sdk), temperature null or ≠ 0, set = edge, failures present, ids ≠ mensajes.json ids, hash mismatch, contract-invalid item, prompt version ≠ active, prompt_sha256 ≠ current file |
| `test_golden_files.py` | both message files pass `runs.load_requests`; label ids equal message ids for both sets |
| `test_prompts.py` (extend Spec 01's) | the existing D14 shingle check covers the edge file too; a missing edge file fails instead of being skipped; failure messages never print golden text |

## 12. Acceptance

`uv run pytest` green · ruff clean · files < 300 lines · a mock run of each set evaluates with the
MOCK header · `python -m pitz_pulse.promote --run <case mock stem> --allow-mock` writes
`/resultados.json` + meta marked `mock: true` · edge labels approved by the candidate (drafts are
reported as not scored until then) · README section (Spec 04b) lists the real-key commands.
