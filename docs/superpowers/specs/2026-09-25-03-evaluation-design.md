# Spec 03 — Evaluation, golden sets & prompt iteration

- **Covers:** R3.1–R3.3, R1.9 (promotion to `resultados.json`), R1.10 (calibration evidence)
- **Depends on:** Spec 01 (batch, run files), Spec 04a (Makefile), candidate-approved labels,
  a real provider credential
- **Status:** draft (rev 3 — aligned with Spec 01 rev 3; D23 promote rules)
- **Decisions used:** D12, D14, D15, D19, D20 (see `docs/MASTER.md`)

## 1. Goal

Know — with numbers, independent of any model — whether the classifier works, where it fails,
whether `confianza` predicts failure, and whether a prompt change helped.

## 2. Golden sets (model-agnostic, D20)

| Set | Messages | Labels | Author |
|---|---|---|---|
| `case` | `/mensajes.json` (the 12 case messages) | `/etiquetas_esperadas.json` | candidate, written **before** the first real run |
| `edge` | `apps/api/eval/golden/edge_cases.messages.json` | `apps/api/eval/golden/edge_cases.labels.json` | messages drafted by the assistant; labels proposed as `draft`, each approved or changed by the candidate |

Golden files contain only input and truth — never a model, provider or prompt version. Any run
from any provider/model/prompt is scored against the same files. No golden message is ever used
as a prompt example (D14).

Edge set (15–20 messages) covers: each PII type, prompt injection, mixed ES/PT, a third language,
very vague, two requests in one message, maximum-length message, emoji/Slack markup, explicit
urgency words without real impact, real impact without urgency words. It also mitigates G20
(tuning to 12 messages): a prompt change must not regress the edge set.

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
| `apps/api/src/pitz_pulse/evaluate.py` | CLI: load + validate, call `scoring`, print Markdown; exit codes |
| `apps/api/src/pitz_pulse/scoring.py` | Pure functions: `score(labels, results, threshold)`, `compare_runs(a, b)`, `threshold_sweep` |
| `apps/api/src/pitz_pulse/promote.py` | `promote(stem)`: verify (below), then write `/resultados.meta.json` then `/resultados.json` (atomic, meta first) |
| `apps/api/eval/runs/` | Run files from Spec 01 batch (`<set>__<prompt>__<provider>__<model>[__<suffix>].json` + `.meta.json`), committed after every real run (D26) |
| `apps/api/prompts/CHANGELOG.md` | Per version: change, hypothesis, eval before → after, stability note |

No new dependencies.

## 5. Flows

```
make classify SET=case|edge [SUFFIX=b] [FORCE=1]  (Spec 01 batch; writes only eval/runs/)
make eval RUN=<stem> [COMPARE=<stem>] [THRESHOLD=<default CONFIDENCE_THRESHOLD>]
   │
   ▼
load labels ─ extra/missing field, bad enum, duplicate id ─► exit 2 (explicit message)
load run + meta ─ missing meta, results_sha256 mismatch, len ≠ meta.n, any item's
                 version_prompt ≠ meta.prompt_version ─► exit 2
labels chosen by meta.set (case → /etiquetas_esperadas.json, edge → edge labels)
   │ results validated STRUCTURALLY with Spec 01 `ClassificationShape` (no rule validators),
   │ so rule violations can be reported instead of crashing (G28)
   ▼
meta.provider == "mock" ─► header "MOCK RUN — NOT MODEL QUALITY"
align by id (approved labels only):
   missing result  → wrong in all 5 fields, failure row got="<missing>", excluded from confidence
   extra result id → listed under extra_ids, not scored
   ▼
per-field accuracy (denominator = approved labels) · exact-match (all 5) · rule violations
per-message confianza vs correct · threshold sweep 0.50–0.90 (routed to review, catch rate)
COMPARE given → per-(id, field) differences between the two runs (stability or v1→v2 diff)
   ▼
Markdown to stdout · exit 0

make promote RUN=<stem>          refuses unless ALL hold (D23):
   meta.provider == "anthropic_api" · meta.temperature == 0 · meta.set == "case"
   meta.failures empty · ids == the 12 ids of /mensajes.json · results_sha256 matches
   meta.input_sha256 == sha256(/mensajes.json) · every item passes full Classification validation
   meta.prompt_version == active version · meta.prompt_sha256 == sha256 of the current
   prompts/<version>.md
   then writes /resultados.meta.json, then /resultados.json (atomic)
```

The only thing that writes `/resultados.json` is `make promote` (D19).

## 6. Report shape

```
## Eval — set case · run v2__anthropic_api__claude-haiku-4-5 · temperature 0 · 12 scored
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

1. Run v1 twice (`SUFFIX=b`) → `COMPARE` gives the noise floor (G19). Record in CHANGELOG.
2. Read failures; write the hypothesis as a **general rule** (G20).
3. v2 with one change; run on case and edge sets; record per-field before → after.
4. Deltas within the v1 noise floor are reported as "no measurable change".
5. Choose `CONFIDENCE_THRESHOLD` from the sweep; record it.
6. Promote the chosen run; README gets the last report and the v1 → v2 story.

Every real run is announced with provider, model and expected call count before it runs.

## 10. Gaps, edge cases, contradictions (this spec)

- **G1** `requiere_info` → candidate's labels + `justificacion`.
- **G2** calibration measured with per-message table and sweep, not assumed.
- **G19** non-determinism → noise floor from a repeated run; Agent SDK runs have no temperature
  control (meta `temperature: null`): usable for development evals, never promoted.
- **G20** overfitting → edge set as regression guard; future set from PATCH corrections.
- **G21** mock runs flagged; cannot be promoted (only `anthropic_api` at temperature 0 can, D23).
- **G28** rule checks need structural (not contract) validation of results.
- Empty confidence bucket / zero wrong → `n/a`, no division by zero.
- Label typos (e.g. `automatización`) → exit 2, not silent mismatch.

## 11. Tests

| File | Cases |
|---|---|
| `test_scoring.py` | perfect match; one wrong field; missing result (all 5 wrong, excluded from confidence); extra result id; draft labels not scored; rule violation (21-word resumen) reported; sweep math incl. zero wrong → n/a; `compare_runs` diffs; markdown snapshot of a small synthetic fixture |
| `test_evaluate_cli.py` | `main()` with tmp files: results_sha256 mismatch / len ≠ n / version_prompt ≠ meta → exit 2; COMPARE across different sets → exit 2; invalid label enum / extra field / missing field / duplicate id → exit 2 with message; invalid result item → exit 2; missing meta → exit 2; mock meta → header; valid → exit 0 |
| `test_promote.py` | writes meta then run; refuses each D23 violation separately: provider ≠ anthropic_api (mock, claude_agent_sdk), temperature null or ≠ 0, set = edge, failures present, ids ≠ mensajes.json ids, hash mismatch, contract-invalid item, prompt version ≠ active, prompt_sha256 ≠ current file |
| `test_golden_files.py` | both golden pairs load; message ids equal label ids; no golden message text appears in any `prompts/*.md` (D14) |

## 12. Acceptance

`make eval` prints real reports for v1 (twice) and v2 on both sets; `make promote` produced
`/resultados.json` + meta; README and `prompts/CHANGELOG.md` contain the numbers.
