# Spec 03 implementation-gate review — 2026-09-27

Target: `feat/spec-03-evaluation` `75c369d..c6fdcf3`. Reduced rigor: one dr-strange worker
(read-only; mock evaluate runs, promote and batch re-run in a scratch worktree, two intermediate
commits checked). No High findings: `/resultados.json` is a byte copy of the mock case run, meta
`mock: true` with matching hash, regeneration is byte-identical, evaluate reports are honest
(MOCK header; edge scores nothing while labels are draft).

| # | Sev | Finding | Resolution |
|---|---|---|---|
| M1 | Med | A mock run with a hand-edited meta could be promoted as `mock: false` | Refused when billing is `none`, tokens are 0 or the mock summary appears |
| M2 | Med | Nothing pins the committed deliverable to the current prompt/schema/inputs | `test_committed_deliverable.py` via `promote.verify` |
| M3 | Med | Spec 04a did not define how `make promote` passes flags | `ALLOW_MOCK=1`/`FORCE=1` mapping; README never uses `FORCE` |
| M4 | Med | `resultados.json` is committed before the README disclosure exists | MASTER §9: no merge to main before Spec 04b discloses it |
| M5 | Med | CHANGELOG called the edge set "the independent check" | Reworded (regression guard, same author) |
| M6 | Med | Edge set lacked CNPJ and BR phones | EDGE-17/18 added (draft); PII-coverage test |
| M7 | Med | Noise-floor per-field flip count missing | Added to the COMPARE section |
| M8 | Med | D14 no-print test did not exercise the real check | Shared `_leaked_ids` helper |
| L | Low | "scores high by construction" false as measured; degenerate sweep; missing meta treated as mock; label read outside try; duplicated 0.7 default; APP_ROOT unstripped; Python booleans in failure rows; missing refusal cases (hash, len); MASTER plan column | Fixed |

Fix wave `c6fdcf3..128d532` (4 commits), verified by the orchestrator: deliverable and case run
unchanged, edge mock run regenerated for the 18-message set (mock only), 667 passed, ruff clean,
no file ≥ 300 lines, both new edge messages masked as expected. Accepted as documented: a suffixed
run renamed by hand is not detected (meta records no suffix); only the first rule violation per
item is reported.
