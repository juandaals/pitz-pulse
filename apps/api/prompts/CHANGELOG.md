# Prompt changelog

| Version | Change | Hypothesis | Eval (case) before → after | Eval (edge) before → after | Notes |
|---|---|---|---|---|---|
| v1 | Initial prompt: case rubric, boundary rules, requiere_info criterion, confidence anchors | Baseline | — | — | Disclosure: some boundary rules (slowness → bug, CRM/analytics syncs → data, "prefer bug when a question reveals something broken") were written with the case messages and labels in view, so case-set accuracy for v1 is optimistic; the edge set is a regression guard drafted by the same assistant, not an independent benchmark |

v1 is active; no v2 was written because no real run exists (D29/G35); the real iteration commands live in the README.
