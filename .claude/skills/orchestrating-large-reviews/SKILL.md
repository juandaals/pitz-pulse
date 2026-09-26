---
name: orchestrating-large-reviews
description: >
  Use to run a decomposed, coverage-complete review of Pitz Pulse at three
  lifecycle gates: (1) right after the CANDIDATE APPROVES A SPEC, (2) right
  after the CANDIDATE APPROVES A PLAN, and (3) once a PHASE IMPLEMENTATION IS
  COMPLETE — all plan tasks done, before proposing the phase commits (NOT on
  per-task edits). Applies to specs in docs/superpowers/specs/ and plans in
  docs/superpowers/plans/. Reads the approved spec/plan to slice the work by
  flow/boundary, fans out N dr-strange agents for failure-mode coverage, runs
  the tool suite once at the implementation gate, and merges everything into
  one report with a coverage ledger of what was and wasn't reviewed, saved to
  docs/superpowers/reviews/. Triggers: "spec approved", "plan approved",
  "phase done", "finished implementing", "all tasks done", "ready to commit
  the phase", "did we cover everything". Do NOT trigger on per-task edits.
---

# Orchestrating Large Reviews

A single fresh-context review pass degrades as the change grows — not at the
context-window limit, but well inside it ("context rot": the more tokens a
reviewer holds, the worse it recalls any one of them). On a big spec or phase
this shows up as **uneven coverage**: the reviewer anchors on the first few
areas and whole boundaries or flows never get looked at. `dr-strange` is
excellent *per unit* — the gap is that one invocation can't reach everything a
large change touches.

This skill closes that gap by **orchestrating** dr-strange, not replacing it.
It reads the approved spec/plan, carves the change into focused slices, fans
out one worker per slice, and merges the results into a single report with an
explicit **coverage ledger** — so "I hope we covered everything" becomes "here
is exactly what we did and didn't reach."

The agent stays exactly as it is. This skill seeds context through its
**existing** inputs (objective intro + investigation hints).

## When to invoke

Invoke at any of these three gates (they map to the per-spec workflow in
`docs/MASTER.md` §5: approve spec → write plan → approve plan → implement →
verify → propose commits). Step 2 decides whether one pass is enough.

| Gate | What it reviews | dr-strange | Tool suite (Step 6) |
|---|---|---|---|
| **Spec approved** by the candidate | the spec document + `MASTER.md` | ✅ N passes | ❌ no code yet |
| **Plan approved** by the candidate | the plan document vs its spec | ✅ N passes | ❌ no code yet |
| **Phase complete** (all plan tasks done, before proposing commits) | the phase diff | ✅ N passes | ✅ once |

**Do NOT invoke on per-task edits** inside a phase. Wait for the whole phase.

At the spec/plan gates the review target is the *document* (catch design,
contract and cross-spec gaps before code exists — the cheapest place to find
them). At the implementation gate it is the *diff*.

**Cost rule:** workers are read-only and must never call a real model. Nothing
in this skill spends provider money. If a finding can only be confirmed with a
real run, it goes to "Open concerns" and the candidate decides.

## Core principle: the orchestrator owns the scope

This is the **orchestrator-workers** pattern. The rule that makes it work:
**the orchestrator derives each slice mechanically and feeds it to the worker —
never let the worker pick its own scope.** Workers that choose their own
boundaries duplicate each other and leave gaps.

## Step 1 — Map the change (do this yourself, cheaply)

Build a lightweight map before dispatching. It stays in your context; workers
get only their slice.

1. **Read `docs/MASTER.md`** — spec index (§5), requirement traceability (§6),
   decisions (§7), gaps register (§8). Note which `R*`/`X*`/`D*`/`G*` IDs the
   target spec owns.
2. **Locate the spec/plan** in `docs/superpowers/specs/` and
   `docs/superpowers/plans/`. Pull out: intent, acceptance criteria / verification
   section, and the task list. These drive the slicing and the per-worker facts.
3. **Compute the diff scope mechanically** (implementation gate only):
   ```bash
   git diff --name-only $(git merge-base main HEAD)..HEAD
   ```
   If the phase is uncommitted, use `git status --porcelain` + `git diff`.
   At the spec/plan gate there is no diff — the "scope" is the document's sections.
4. **Build an inventory**: which boundaries are touched (domain · providers ·
   graph · service · persistence · API · web · delivery/compose · eval), which
   end-to-end flows the change implements, and which spec section maps to which
   files. Flows and boundaries are your candidate slices.

## Step 2 — Decide the split

**If the change is small / single-boundary → run ONE dr-strange (+ Step 6 at
the implementation gate) and stop.** Over-splitting just adds seams for no
coverage gain.

**If the change is large → carve slices, flow-driven first:**

- **Flow-driven (preferred).** One slice per end-to-end path. Pitz Pulse's main
  flows:
  - `POST /solicitudes` → auth → reservation (`BEGIN IMMEDIATE`, hash, claim
    token) → mask → LangGraph (`call_llm → validate → retry`) → adapter →
    complete/fail → response.
  - `PATCH /solicitudes/{id}` → transaction → merge/validate → original + correction.
  - `GET /solicitudes` list/filter/paginate on current values + review queue.
  - `make classify` batch → `eval/runs/*.json` + meta → `make eval` scoring →
    `make promote` → `resultados.json` + meta.
  - `docker compose up` (with and without `.env`) → migrations → `/health` → smoke.
  - Extras: Slack signed event → classify → thread reply; web → nginx `/api` →
    review/correct; duplicates; compare.

  A flow slice follows the data *across* boundaries, so the boundary lives
  **inside** the slice where one worker sees both sides — the best defense
  against missed cross-boundary bugs.
- **Boundary-driven (fallback).** When areas are genuinely independent, slice by
  boundary.
- **Cross-spec slice** (spec/plan gates with more than one spec in play): one
  worker whose only job is contradictions between specs and `MASTER.md`. This is
  what caught G25–G28 in the first spec review.

**Pick N:** single-flow change → 1; a handful of independent flows → one each;
sprawling phase → one per flow plus seam passes. Past ~5 truly independent passes
the marginal find drops off. **Independent, well-bounded** slices beat more slices.

## Step 3 — Name the seams explicitly

A seam is a boundary between two slices where a bug can hide that neither
worker sees alone. Typical Pitz Pulse seams:

- classifier/graph output ↔ contract validation in the service
- `LLMSettings` ↔ `ApiSettings` (D21): CLI vs API config
- batch run files ↔ eval scoring ↔ promote (D19)
- migration runner ↔ repository code on an existing named volume
- api ↔ nginx proxy ↔ web (D22); api ↔ Slack signature path (G26)
- stale re-claim window ↔ late worker completion (G24)

For each seam, either confirm it lives **inside** a flow slice, or give it a
**dedicated seam pass** whose entire scope *is* the boundary. Name every seam;
assign every seam an owner.

## Step 4 — Seed each worker (four-element contract)

Give each worker the facts; withhold the author's self-assessment so it stays
adversarial.

1. **Objective** — the one question for *this* slice ("validate that concurrent
   same-id POSTs make exactly one model call and a late worker can't overwrite").
2. **Boundaries** — the exact in-slice files / spec sections, **plus a compact
   "adjacent but outside your slice" map** so the worker knows the seam exists
   and doesn't crawl the whole repo.
3. **Facts** — the relevant `MASTER.md` IDs, the **specific spec/plan lines**
   this slice implements, and the **prior behavior** (`git show main:<path>`)
   when the phase changes existing code. This grounding lets the worker catch
   regressions and uncovered requirements.
4. **Output format** — dr-strange's existing report schema (don't re-specify it).

**Withhold** the author's "this is fine because…" rationale. Never instruct a
worker not to flag a specific issue.

Dispatch with the shape from dr-strange's "Canonical Parent Invocation" section.

## Step 5 — Dispatch in parallel

Launch all workers for a round in a single turn (multiple Agent calls,
`subagent_type: dr-strange`) so they run concurrently.

## Step 6 — Tool suite (implementation gate only, run ONCE yourself)

Don't fan this out — it is global, not per slice. From the repo root:

```bash
make test
make lint
# source files < 300 lines (Markdown, JSON, lockfiles excluded)
git ls-files -- '*.py' '*.ts' '*.tsx' '*.sql' '*.sh' | xargs wc -l | awk '$1>=300 && $2!="total"'
# nothing secret or confidential tracked
git ls-files | grep -Ei '(^|/)\.env$|\.pdf$|\.db$'
```

If a `make` target doesn't exist yet in this phase, run the underlying command
the spec defines (e.g. `uv run pytest -q`, `uv run ruff check`) and say so.
Paste the real output into the report. A red suite is a High finding.

## Step 7 — Merge, dedup, verify, and build the coverage ledger

1. **Normalize** every finding to High / Medium / Low.
2. **Dedup on content identity** — `(finding-type, file-or-spec-section,
   cited code/text)`, **not `<file, line>`** (lines shift between slices).
3. **Merge rules**: same location + same issue → one finding, credit both
   workers. Same location, different issue → keep both. **Conflicting severity →
   take the higher.**
4. **Verify High findings.** Dispatch one dr-strange whose objective is to
   *refute* each High finding (with citations). Mark each `confirmed` or
   `refuted` with the reason; refuted findings stay in the report, struck out.
5. **Coverage ledger** — table of *slice × dimension*, each cell reviewed /
   not-reviewed. Dimensions: contract fidelity · idempotency · concurrency ·
   failure/retries · provider boundary · privacy & logging · prompt injection ·
   auth · data fidelity · migrations · eval integrity · delivery · cost ·
   engineering rules. Every empty cell is an explicit, named gap.

## Step 8 — Converge, report, record

- **Bounded loop.** If the ledger shows gaps, or a worker surfaced an unsliced
  area, run **one more targeted round** on the gaps only. Stop when the ledger is
  full, a round surfaces nothing new, or after **3 rounds**.
- **Fail open.** A worker that dies or is skipped → its slice is **UNREVIEWED**
  in the ledger. Never drop a slice silently.
- **Save** the report to `docs/superpowers/reviews/<YYYY-MM-DD>-<spec##>-<gate>-review.md`
  (English, like every doc in the repo). New gaps/decisions go to `MASTER.md`
  §7/§8 only after the candidate agrees — propose the rows, don't apply them.
- **Talk to the candidate in Spanish**: summary + the High findings + the path
  to the report. Don't commit; propose the commit.

### Consolidated report format

```
# <Spec ##> <gate> review — <date>

Workers: N dr-strange (+ 1 verifier). Raw findings: X · confirmed: Y
(high a, medium b, low c) · duplicates merged: d · refuted: e.

## Review summary
Risk posture in 3–5 sentences + the 2–3 highest-severity findings.

## Findings (deduped, severity-sorted)
| # | Sev | Slice(s) | Anchor (file:line / spec §) | MASTER IDs | Finding | Mitigation | Verified |

## Tool suite (implementation gate)
Commands run + real output (pass/fail counts).

## Coverage ledger
Slice × dimension table; every not-reviewed / UNREVIEWED cell called out.

## Proposed MASTER.md updates
New G*/D* rows for the candidate to approve.

## Open concerns
Unresolved after 3 rounds, and anything that needs a real (paid) run to confirm.

## Workers run
One line per worker: slice, completed / failed.
```

## What this skill does NOT do

- It does **not** modify `dr-strange.md`.
- It does **not** auto-fix, commit, or call a real model. It only reports.
- It does **not** write `etiquetas_esperadas.json` or `AI_LOG.md` (candidate-owned).

## See also

- `.claude/agents/dr-strange.md` — the worker; its "Canonical Parent Invocation"
  is the Step 4 template.
- `docs/superpowers/reviews/2026-09-25-spec-review.md` — the first multi-agent
  spec review; the report format above follows it.
- `superpowers:dispatching-parallel-agents` — general fan-out guidance.
