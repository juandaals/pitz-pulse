# Review Web UI (Spec 05) Implementation Plan — fast mode

> Fast mode (candidate decision 2026-09-27): no gates except one final reviewer.

**Goal:** a minimal internal page (`apps/web`) to work the low-confidence queue and confirm or
correct classifications, served by nginx under compose profile `web`.

**Spec:** `docs/superpowers/specs/2026-09-25-05-review-web-ui-design.md` (rev 2), aligned with the
API as built (Spec 02 rev 5.1): error body `{error, detail, fields?, kind?}`; 422
`validation_error` with `fields: [{loc, msg}]` (cross-field rules `loc: ["body"]`); 409
`not_classified`; list filters `categoria, prioridad, area_sugerida, status, needs_review, limit,
offset`; Item carries `provider`, `needs_review`, `corrected`; PATCH accepts only the 7 correctable
fields + `author` + `reason`.

## Global constraints
- Dependencies exactly as Spec 05 §2 (react, react-dom, vite, typescript; dev: openapi-typescript,
  vitest, @testing-library/react, jsdom, plus `@testing-library/jest-dom`/`@vitejs/plugin-react`
  if needed). Pin versions in `package.json` and commit `package-lock.json`. No router, no state
  library, no UI kit. Plain CSS in one file.
- TypeScript strict. Every source file < 300 lines. English UI text except contract values.
- Never call a real model; the API is mocked in tests (`fetch` stubbed). `npm test` and
  `npx tsc --noEmit` green. Python suite and ruff stay green.
- Conventional commits with body + `Co-Authored-By: <your model> <noreply@anthropic.com>`.

### Task 1: scaffold, generated types, client, validation
- `apps/web`: `package.json` (scripts `dev`, `build`, `test` = `vitest run`, `typecheck` =
  `tsc --noEmit`, `gen:types` = openapi-typescript from `openapi.json` to `src/api/schema.ts` with
  enum values), `tsconfig.json`, `vite.config.ts` (dev proxy `/api` → `http://localhost:8000`,
  rewrite prefix), `index.html`, `src/main.tsx`.
- `make web-types` (root Makefile): writes `apps/web/openapi.json` via
  `LLM_PROVIDER=mock API_KEY=web-types DB_PATH=<mktemp> uv --directory apps/api run python -c
  "import json; from pitz_pulse.api import create_app; print(json.dumps(create_app().openapi(), indent=2))"`
  then `npm --prefix apps/web run gen:types`. Commit `openapi.json` and `schema.ts`.
- `src/api/client.ts` and `src/validation.ts` exactly as Spec 05 §3; tests
  `client.test.ts`, `validation.test.ts` as Spec 05 §8 (shared word-count vector
  `"uno  dos\ntres"` → 3).
- Commit `feat: scaffold the review web app with generated API types`.

### Task 2: components, serving, CI
- `App.tsx`, `components/KeyPrompt.tsx`, `QueueList.tsx`, `RequestDetail.tsx`,
  `CorrectionForm.tsx`, `styles.css` — behavior, flows and states exactly as Spec 05 §3–§7
  (mock badge when `provider === "mock"`; Confirm sends only `author`; only changed fields sent;
  `requiere_info` off → `pregunta_seguimiento: null`; 401 clears the key; 409 message + back;
  422 fields shown, `["body"]` above the form; empty queue state; refetch same offset clamped).
- Tests as Spec 05 §8 (`App`, `QueueList`, `RequestDetail`, `CorrectionForm`).
- `apps/web/Dockerfile` (node build → `nginx:alpine`), `apps/web/nginx.conf` (SPA fallback;
  `location /api/ { proxy_pass http://api:8000/; }`; `client_max_body_size 64k`); compose service
  `web` under profile `web`, `depends_on: api: condition: service_healthy`, port
  `${WEB_PORT:-8080}:80`; add `WEB_PORT` to `.env.example` and the inventory test.
- CI (`.github/workflows/ci.yml`): job `web` — setup-node (LTS, npm cache on
  `apps/web/package-lock.json`), `npm ci`, `npm run typecheck`, `npm test`; then setup-uv and
  `make web-types` + `git diff --exit-code apps/web/src/api/schema.ts apps/web/openapi.json`.
- Verify: `docker compose --env-file /dev/null --profile web -p pitzweb up --build -d --wait`,
  page served on the web port, `curl localhost:<web>/api/health` via the nginx proxy works, then a
  POST + PATCH through `/api/` shows `corrections[]`; `down -v`.
- README: a "Review web UI" section (how to start it, API key in the browser is local-demo only).
  CLAUDE.md: `make web-types` exists. Spec 05 status → implemented.
- Commit `feat: add the review web UI and serve it under the web profile`.
