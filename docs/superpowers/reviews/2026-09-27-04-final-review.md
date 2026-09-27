# Spec 04 final review — 2026-09-27 (fast mode)

One dr-strange reviewer on `feat/spec-04-delivery-docs` (`d792732..e543b6d`): clean clone with
no `.env`, `docker compose up --build`, `scripts/smoke.sh` → `SMOKE OK`, every README §1 curl as
documented, `make test` (672) and `make lint` clean, non-root container, no secrets tracked,
DECISIONES prose 710 words.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| H1 | High | "no .env → mock" fails if the shell exports `CLAUDE_CODE_OAUTH_TOKEN` (Agent SDK rejects temperature 0) and bills if it exports `ANTHROPIC_API_KEY` | README states the condition, the risk and an `env -u …` command for a guaranteed mock run |
| H2 | High | `test_env_inventory.py` survived 7 harmful mutations | Exact compose expressions, empty secrets, parseable `.env.example`, non-empty compose block, `make -n` flag tests; each RED-confirmed |
| M1–M3 | Med | `make up && make smoke` race; same-second smoke id collision; smoke ignored `API_PORT` and leaked curl exit codes | `--wait`; random id suffix; `API_PORT` default; named-step failures; health wait |
| M4 | Med | README log sample was not real output | Real line |
| M5–M6 | Med | `classify` echo lacked temperature; `FORCE=0` still forced | Echo fixed; literal-1 rule like promote |
| M7 | Med | First live call (schema acceptance) not surfaced | README "First real call" + pending row |
| M8–M9 | Med | README overstatements (promotion, PATCH fields, "never twice"); `.env.example` comments contradicted code; no exact mock-regeneration commands | Reworded; exact commands with a command-line `LLM_PROVIDER=mock` |
| L | Low | MASTER not updated; `web-types` in CLAUDE.md; AI_LOG not in pending; Slack described as existing; undated prices; python3 prerequisite | Fixed |

Fix commits `9a53580`, `d0c8151`: 678 passed, lint clean, stack up `--wait` + two back-to-back
smoke runs OK, unreachable URL exits 1 naming the step, nothing left running.
