# CLAUDE.md — Pitz Pulse

Technical case: internal request triage service (classify ES/PT requests with an LLM, store,
query, correct, evaluate). Start every session by reading `docs/MASTER.md`.

## Source of truth
- The case PDF (outside the repo, confidential — never copy it or its text into the repo).
- `docs/MASTER.md`: requirement IDs (R*, X*), decisions (D*), gaps register (G*), spec index.
- Specs in `docs/superpowers/specs/`, plans in `docs/superpowers/plans/`. Do not implement
  anything that is not in an approved spec + plan. Do not invent requirements.

## Contract — never translate or rename
- Output fields: `id categoria prioridad area_sugerida idioma resumen requiere_info
  pregunta_seguimiento confianza version_prompt` and their enum values exactly as in the case.
- Files: `resultados.json etiquetas_esperadas.json DECISIONES.md AI_LOG.md README.md .env.example`.
  Endpoint: `/solicitudes`.
- `resumen` in Spanish (≤ 20 words). `pregunta_seguimiento` in the message's language.
- The 12 test messages stay in their original Spanish/Portuguese.

## Language
- Everything else is English: code, identifiers, comments, docs, commits, logs, API descriptions.
- Talk to the candidate in Spanish.

## Ownership
- `etiquetas_esperadas.json`: labels were drafted by the assistant at the candidate's request and
  approved by the candidate (2026-09-25); the candidate has the final say on any change. Disclose
  this in README and AI_LOG. Edge-set labels follow the same rule (`label_status`).
- `AI_LOG.md` belongs to the candidate. Never generate it.

## Engineering rules
- KISS; boundaries: domain · providers · graph (LangGraph harness) · service · persistence · API · web.
- Only multi-implementation interface: `ProviderAdapter` (anthropic_api, claude_agent_sdk, mock); LangGraph is always the harness.
- Source files < 300 lines — split by responsibility before continuing.
- Minimal dependencies, each justified in its spec.
- Never swallow errors; log with context; never log request message text or API keys.
- Tests ship with the code in the same phase; tests never call the real model API.

## Commands (from repo root)
```
make install  make test  make lint  make classify  make eval  make compare  make promote
make up  make down  make smoke        (make web-types arrives with Spec 05)
```

## Workflow
1. One spec/phase at a time. End each phase by running tests/commands and showing real output.
2. Propose commits (conventional: `feat: fix: test: docs: chore:`); the candidate approves before
   committing. Small commits; real history.
3. Any real model run costs money: announce it before running.
4. Disagree when there is a better option; record decisions in `docs/MASTER.md` §7.
5. No "done" without evidence (tests, `docker compose up`, `make eval`).
