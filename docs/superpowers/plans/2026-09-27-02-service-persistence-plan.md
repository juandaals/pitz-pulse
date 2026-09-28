# Service & Persistence (Spec 02) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An HTTP service (`/solicitudes`) that classifies requests idempotently, stores them in SQLite with versioned migrations, lists/filters/paginates them and records human corrections without losing the original classification.

**Architecture:** FastAPI sync routes → one app-scoped `TriageService` (model-slot semaphore, reads, corrections; idempotent intake and lost-claim mapping in the `IntakeMixin`) → `Repository` (SQL + typed row mapping, rollback-always transactions) on a per-call `sqlite3` connection. Classification is Spec 01's `Classifier` (LangGraph harness), called outside any DB transaction.

**Tech Stack:** Python 3.12, uv, FastAPI 0.141 (starlette 1.7.0 as locked), uvicorn 0.54, stdlib `sqlite3` (WAL), pydantic 2.13, pytest 9.

**Spec:** `docs/superpowers/specs/2026-09-25-02-service-persistence-design.md` (rev 5.1, plan-gate corrections). Reviews: `docs/superpowers/reviews/2026-09-27-02-spec-review.md`, `docs/superpowers/reviews/2026-09-27-02-plan-review.md`. Decisions: `docs/MASTER.md` D5–D9, D11, D12, D17, D21, D25, D28–D30.

**Plan version:** v2 (2026-09-27). v2 was executed verbatim in a scratch worktree (Tasks 1–9, commits skipped): 542 passed, ruff clean, every file < 300 lines, 41/41 named mutants killed.

## v2 changes (plan-gate review, `2026-09-27-02-plan-review.md`)

| # | Change | Why |
|---|---|---|
| 1 | `Repository.list` / `TriageService.list` → `list_requests` everywhere | A method named `list` shadows the builtin inside the class body; a later `-> list[...]` annotation raised `TypeError` at import (P1) |
| 2 | `db.split_statements` never appends `;` to the tail; the tail may hold only whitespace and comments, else `ValueError("…incomplete…")`. Tests for a trailing comment, three incomplete tails and the spec literal through `migrate` | v1 made every tail "complete", so an incomplete statement was never detected (P2) |
| 3 | `executescript` ban checked with an AST walk over `db.py` | The v1 substring test failed on `migrate`'s own docstring (P3) |
| 4 | Task 3 helper `classified()` passes `now` to `pending()`; ordering and pagination assert exact ids | `created_at DESC` was never exercised (P4) |
| 5 | Non-ASCII key test sends bytes (`"clé".encode()`, `b"\xff"`) | httpx2 refuses a non-ASCII `str` header before sending (P5) |
| 6 | Reads map rows with `ClassificationShape` (`StoredRequest.classification: ClassificationShape \| None`); writes/PATCH still validate with `Classification` | A row that a later, stricter rule rejects must not 500 a whole page |
| 7 | Intake never strands a row `pending` except on `DbBusy`: crash → best-effort `internal_error` (lost claim → answer from the current row); non-`DbBusy` errors from `complete` → best-effort fail and re-raise; every failed fail-write logs `request_fail_write_failed`; a locked `fail` on the classification/busy path logs and raises `DbBusy` with the attempts | v1 answered 500 after a lost claim, swallowed `DbBusy` with `pass`, and left rows pending on unexpected `complete` errors |
| 8 | Lost claim on a failed row keeps `ClassificationFailed(row.error)`; Spec 02 §4 `kind` list amended | The row's kind can be `internal_error` or `busy` |
| 9 | Slot fast path only when nobody waits (checked under `_waiters_lock`) | New arrivals overtook queued requests |
| 10 | `service.py` split up front: intake in `intake.py` (`IntakeMixin`), connections/slots/reads/corrections in `service.py` | v1 `service.py` was at 274 lines before corrections |
| 11 | `api_models.ErrorBody` (+ `FieldError`); every `/solicitudes` route documents its error responses in OpenAPI | Spec §2 promised it; OpenAPI showed FastAPI's default 422 shape |
| 12 | New tests killing the surviving mutants (repository, service, corrections, API/factory; list in Self-review); service tests split into `test_idempotency.py`, `test_lost_claims.py`, `test_service_failures.py`; API tests into `test_api.py`, `test_api_errors.py`; lock probe in `tests/db_support.py` | Each file stays < 300 lines |
| 13 | Task 1 uses `mktemp`; Task 8 line check globs files; Task 9 uses a free port, PID file, `/health` wait loop and full JSON output | Fixed `/tmp` paths, missed new files, `sleep 3`, `kill %1`, truncated output |
| 14 | Every code block is `ruff format` clean (≤ 100 columns) | The `ruff format .` step changes nothing |

## Global Constraints

- All commands run from `apps/api/` with `uv run …`; Python `>=3.12,<3.13`.
- Contract field names and enum values, `/solicitudes`, and the case files are never translated or renamed. Everything else (code, identifiers, comments, logs, commits) is English.
- Source files < 300 lines each (tests included); split by responsibility before crossing it.
- Dependencies: only `fastapi` and `uvicorn` are added (runtime). No ORM, no Alembic, no new dev deps (`TestClient` uses the locked `httpx2`).
- Never swallow errors; log with context; **never log message text, model output, exception messages or keys** (`log_event` fields and exception class names only).
- Tests never call a real model API or the network; every API test uses `FakeAdapter`/`GateAdapter` or mock and a temp SQLite file.
- `ruff format --check .` and `ruff check .` clean and the full suite green at every commit.
- Conventional commits, small, each ending with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; a short body saying why.
- Timestamps: UTC `YYYY-MM-DDTHH:MM:SS.mmmZ`, written by Python only (`db.format_ts`).
- `executescript` is forbidden; every write goes through `Repository.transaction()`.
- No `from __future__ import annotations`; no method may be named after a builtin used in annotations (`list`, `dict`, …).
- Code blocks are copied verbatim. A file-creating block is always preceded by its path line (`` `path`: ``); modifications say exactly where the text goes.

## Review Focus

1. A Portuguese/Spanish message with accents and emoji (`"Não consigo acessar 🚀"`) must round-trip byte-identical through POST → GET, and a byte-identical re-POST must replay (200) — Task 5 `test_unicode_message_round_trips_and_replays`.
2. The server restarts while a row is `pending` (new app/service on the same DB file): the next POST must get 409 `in_progress` with a positive `Retry-After`, not a second model call — Task 5 `test_pending_row_survives_restart_as_in_progress`.
3. `DB_PATH` pointing into a directory that does not exist yet must just work (parent created) — Task 2 `test_connect_creates_parent_directory`.
4. Clients send the header in any case (`x-api-key`) — HTTP headers are case-insensitive, auth must accept it — Task 8 `test_api.py::test_lowercase_api_key_header_is_accepted`.
5. A 503 `busy` over HTTP must carry `Retry-After: 30` so clients back off instead of hammering — Task 8 `test_api_errors.py::test_busy_is_503_with_retry_after`.
6. A worker whose row was re-claimed while it was still calling the model must never overwrite the new owner's result — Task 5 `test_lost_claims.py::test_late_worker_never_completes_a_row_re_claimed_in_flight`.

---

## File map

| File | Task | Responsibility |
|---|---|---|
| `pyproject.toml`, `uv.lock` | 1 | add `fastapi`, `uvicorn` |
| `tests/test_http_stack.py` | 1 | FastAPI + TestClient run on the locked starlette |
| `src/pitz_pulse/db.py` | 2 | connection, timestamps, statement splitter, migration runner |
| `src/pitz_pulse/migrations/001_init.sql` | 2 | schema |
| `tests/db_support.py`, `tests/test_db.py` | 2 | write-lock probe; db tests |
| `src/pitz_pulse/repository.py` | 3 | `StoredRequest`, `ListFilters`, `Repository` |
| `src/pitz_pulse/review.py` | 3 | `needs_review` comparator |
| `tests/test_repository.py`, `tests/test_review_flag.py` | 3 | |
| `src/pitz_pulse/errors.py` | 4 | domain errors, `field_errors` |
| `src/pitz_pulse/settings_api.py` | 4 | `ApiSettings`, `parse_api_settings`, `stale_floor_s` |
| `src/pitz_pulse/schema.py`, `src/pitz_pulse/config.py` | 4 | `ID_PATTERN`, UTF-8 validator; `deadline_s` docstring |
| `tests/test_settings_api.py`, `tests/test_errors.py`, `tests/test_schema.py` | 4 | |
| `src/pitz_pulse/service.py` | 5 (6) | `TriageService(IntakeMixin)`: connections, model slots, `get`/`list_requests`/`needs_review`; `correct` in Task 6 |
| `src/pitz_pulse/intake.py` | 5 | `IntakeMixin`: idempotent `create`, classify, complete/fail, lost claims, outcome log |
| `tests/fakes.py` (modify), `tests/service_support.py` | 5 | `GateAdapter`; `FakeClock`, `make_service`, `start`, `unavailable` |
| `tests/test_idempotency.py`, `tests/test_lost_claims.py`, `tests/test_service_failures.py` | 5 | |
| `src/pitz_pulse/corrections.py` | 6 | `apply_correction` |
| `tests/test_corrections.py` | 6 | |
| `src/pitz_pulse/api_models.py`, `src/pitz_pulse/http_errors.py` | 7 | HTTP models incl. `ErrorBody`, handlers, catch-all |
| `tests/test_api_models.py` | 7 | |
| `src/pitz_pulse/api.py` | 8 | `create_app`, router, auth, OpenAPI error responses, mock marking |
| `tests/api_support.py`, `tests/test_api.py`, `tests/test_api_errors.py`, `tests/test_app_factory.py` | 8 | |
| docs | 9 | status, verification output |

---

### Task 1: Verify FastAPI on the locked starlette (gate — STOP on failure)

**Files:**
- Modify: `apps/api/pyproject.toml`, `apps/api/uv.lock`
- Test: `apps/api/tests/test_http_stack.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `fastapi` and `uvicorn` importable; locked versions unchanged for every package already in `uv.lock`.

**Stop rule (binding):** if any step below fails — `uv add` cannot resolve, any *existing* package changes version (in particular `starlette` must stay `1.7.0`, `mcp` `2.2.0`, `uvicorn` `0.54.0`, `pydantic`, `anthropic`, `langgraph`, `langchain-*`, `claude-agent-sdk`), or the smoke test fails — then run `git checkout -- pyproject.toml uv.lock`, do **not** commit, write what failed (exact command output) to the report and return status **BLOCKED**. Do not try another FastAPI version, do not relax pins, do not continue to Task 2. The controller stops the plan and asks the candidate.

- [ ] **Step 1: Record the current versions**

Run (one shell; `$BEFORE` is used again in Step 5):
```bash
cd apps/api
BEFORE=$(mktemp)
for p in starlette mcp uvicorn pydantic anthropic langgraph langchain-core langchain-anthropic claude-agent-sdk httpx2; do
  printf '%s ' "$p"; grep -A1 "^name = \"$p\"$" uv.lock | sed -n 2p
done > "$BEFORE"; cat "$BEFORE"
```
Expected: one line per package, `starlette version = "1.7.0"`, `mcp version = "2.2.0"`, `uvicorn version = "0.54.0"`.

- [ ] **Step 2: Write the smoke test**

`tests/test_http_stack.py`:
```python
"""Gate for Spec 02: FastAPI and its TestClient work on the starlette version locked by Spec 01."""

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_fastapi_serves_a_sync_route_through_the_test_client():
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    response = TestClient(app).get("/ping")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/test_http_stack.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'fastapi'`.

- [ ] **Step 4: Add the dependencies**

Run: `uv add 'fastapi>=0.141,<0.142' 'uvicorn>=0.54,<0.55'`
`uv add` appends two lines to `dependencies` in `pyproject.toml`; add the trailing comments by hand so they read exactly:
```toml
    "fastapi>=0.141,<0.142",  # HTTP API (Spec 02); starlette stays as locked
    "uvicorn>=0.54,<0.55",  # ASGI server; `uvicorn --factory pitz_pulse.api:create_app`
```
Run `uv lock` again after editing comments; it must not change `uv.lock` (`git diff --stat uv.lock` shows the same numbers before and after).

- [ ] **Step 5: Verify no existing version moved**

Run (same shell as Step 1):
```bash
AFTER=$(mktemp)
for p in starlette mcp uvicorn pydantic anthropic langgraph langchain-core langchain-anthropic claude-agent-sdk httpx2; do
  printf '%s ' "$p"; grep -A1 "^name = \"$p\"$" uv.lock | sed -n 2p
done > "$AFTER"; diff "$BEFORE" "$AFTER" && echo UNCHANGED
rm -f "$BEFORE" "$AFTER"
git diff --stat uv.lock
```
Expected: `UNCHANGED`; `uv.lock | 29 +++++…` (only additions: `fastapi`, `annotated-doc` and the two new root dependencies). If `diff` prints anything → Stop rule.

- [ ] **Step 6: Run the smoke test and the whole suite**

Run: `uv run pytest tests/test_http_stack.py -q && uv run pytest && uv run ruff format --check . && uv run ruff check .`
Expected: smoke PASS; `387 passed` (386 + 1); ruff clean. Any failure → Stop rule.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock tests/test_http_stack.py
git commit -F - <<'EOF'
chore: add fastapi and uvicorn for the HTTP service

Gate for Spec 02: FastAPI 0.141 resolves against the starlette 1.7.0 already
locked through mcp; no existing locked version changes. A smoke test pins
that the TestClient works on this stack.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: Database connection and atomic migrations

**Files:**
- Create: `src/pitz_pulse/db.py`, `src/pitz_pulse/migrations/001_init.sql`
- Test: `tests/db_support.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: `pitz_pulse.schema` enums (`Categoria`, `Prioridad`, `Area`, `Idioma`) for the parity test.
- Produces:
  - `db.connect(path: Path) -> sqlite3.Connection` (row factory `sqlite3.Row`, autocommit, `check_same_thread=False`)
  - `db.format_ts(moment: datetime) -> str`, `db.parse_ts(text: str) -> datetime`
  - `db.split_statements(sql: str) -> list[str]` (tail after the last complete statement: whitespace/comments only, else `ValueError`)
  - `db.migrate(conn, directory: Path = MIGRATIONS_DIR) -> list[str]` (versions applied now)
  - `db.MIGRATIONS_DIR`
  - test helper `db_support.write_lock_is_held(path) -> bool` (used again in Task 3)

- [ ] **Step 1: Write the failing tests**

`tests/db_support.py`:
```python
import sqlite3


def write_lock_is_held(path) -> bool:
    """True if another connection cannot start a write transaction right now."""
    other = sqlite3.connect(path, isolation_level=None)
    other.execute("PRAGMA busy_timeout = 0")
    try:
        other.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        assert "database is locked" in str(exc)
        return True
    else:
        other.execute("ROLLBACK")
        return False
    finally:
        other.close()
```

`tests/test_db.py`:
```python
import ast
import re
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from db_support import write_lock_is_held

from pitz_pulse import db
from pitz_pulse.schema import Area, Categoria, Idioma, Prioridad

NOW = "2026-09-27T10:00:00.000Z"


def _fresh(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.migrate(conn)
    return conn


def _insert_classified(conn, **overrides):
    values = {
        "id": "r1",
        "message": "m",
        "source_area": None,
        "message_hash": "h",
        "status": "classified",
        "categoria": "bug",
        "prioridad": "alta",
        "area_sugerida": "backend",
        "idioma": "es",
        "resumen": "r",
        "requiere_info": 0,
        "pregunta_seguimiento": None,
        "confianza": 0.9,
        "version_prompt": "v1",
        "provider": "mock",
        "model": "mock",
        "original_classification": "{}",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO requests ({cols}) VALUES ({marks})", tuple(values.values()))


def test_connect_creates_parent_directory(tmp_path):
    conn = db.connect(tmp_path / "a" / "b" / "pitz.db")
    assert (tmp_path / "a" / "b" / "pitz.db").exists()
    conn.close()


def test_connect_uses_wal_busy_timeout_and_foreign_keys(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.isolation_level is None


def test_connection_created_in_one_thread_works_in_another(tmp_path):
    conn = _fresh(tmp_path)
    errors = []

    def use():
        try:
            conn.execute("SELECT count(*) FROM requests").fetchone()
            conn.close()
        except Exception as exc:  # the assertion below reports it
            errors.append(exc)

    worker = threading.Thread(target=use)
    worker.start()
    worker.join()
    assert errors == []


def test_timestamps_round_trip_with_milliseconds():
    moment = datetime(2026, 9, 27, 10, 0, 0, 123456, tzinfo=UTC)
    assert db.format_ts(moment) == "2026-09-27T10:00:00.123Z"
    expected = datetime(2026, 9, 27, 10, 0, 0, 123000, tzinfo=UTC)
    assert db.parse_ts("2026-09-27T10:00:00.123Z") == expected


def test_split_statements_keeps_semicolons_inside_strings():
    sql = "CREATE TABLE a(x TEXT DEFAULT ';');\n-- comment\nCREATE TABLE b(y);\n"
    assert db.split_statements(sql) == [
        "CREATE TABLE a(x TEXT DEFAULT ';');",
        "-- comment\nCREATE TABLE b(y);",
    ]


def test_split_statements_accepts_a_trailing_comment():
    assert db.split_statements("CREATE TABLE a(x);\n-- end\n") == ["CREATE TABLE a(x);"]
    assert db.split_statements("CREATE TABLE a(x);\n/* end; */\n") == ["CREATE TABLE a(x);"]


@pytest.mark.parametrize(
    "sql", ["CREATE TABLE a(x); CREATE TABLE bad(", "CREATE TABLE a(x)", "SELECT 1; -- x\nSELECT"]
)
def test_split_statements_rejects_an_incomplete_tail(sql):
    with pytest.raises(ValueError, match="incomplete"):
        db.split_statements(sql)


def test_migrate_creates_the_schema_and_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert db.migrate(conn) == ["001_init"]
    assert db.migrate(conn) == []
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"requests", "corrections", "schema_migrations"} <= tables


@pytest.mark.parametrize(
    ("sql", "error"),
    [
        ("CREATE TABLE ok(x); CREATE TABLE bad(", ValueError),
        ("CREATE TABLE ok(x);\nCREATE TABLE ok(y);\n", sqlite3.OperationalError),
    ],
)
def test_failing_migration_leaves_nothing_behind(tmp_path, sql, error):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_bad.sql").write_text(sql)
    conn = db.connect(tmp_path / "t.db")

    with pytest.raises(error):
        db.migrate(conn, migrations)

    assert conn.in_transaction is False
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ok" not in tables
    assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 0


def test_migrate_holds_the_write_lock_from_begin(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_probe.sql").write_text("SELECT probe();\nCREATE TABLE t(x);\n")
    path = tmp_path / "t.db"
    conn = db.connect(path)
    seen = []
    conn.create_function("probe", 0, lambda: seen.append(write_lock_is_held(path)))

    db.migrate(conn, migrations)

    assert seen == [True]


def test_db_module_never_calls_executescript():
    tree = ast.parse(Path(db.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "executescript"
    ]
    assert calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"categoria": None},
        {"requiere_info": 2},
        {"confianza": 7.0},
        {"original_classification": "not json"},
        {"categoria": "Bug"},
        {"provider": None},
    ],
)
def test_checks_reject_inconsistent_classified_rows(tmp_path, overrides):
    conn = _fresh(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_classified(conn, **overrides)


def test_checks_accept_a_consistent_classified_row(tmp_path):
    conn = _fresh(tmp_path)
    _insert_classified(conn)
    assert conn.execute("SELECT status FROM requests").fetchone()[0] == "classified"


@pytest.mark.parametrize("column", ["previous_values", "new_values"])
def test_corrections_reject_invalid_json(tmp_path, column):
    conn = _fresh(tmp_path)
    _insert_classified(conn)
    values = {"previous_values": "{}", "new_values": "{}", column: "not json"}
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO corrections (request_id, previous_values, new_values, author,"
            " created_at) VALUES ('r1', ?, ?, 'ana', ?)",
            (values["previous_values"], values["new_values"], NOW),
        )


@pytest.mark.parametrize(
    ("column", "enum"),
    [
        ("categoria", Categoria),
        ("prioridad", Prioridad),
        ("area_sugerida", Area),
        ("idioma", Idioma),
    ],
)
def test_check_enum_lists_equal_the_contract_enums(column, enum):
    sql = (db.MIGRATIONS_DIR / "001_init.sql").read_text()
    match = re.search(rf"{column} TEXT CHECK \({column} IN \(([^)]*)\)\)", sql)
    assert match, column
    values = {v.strip().strip("'") for v in match.group(1).split(",")}
    assert values == {member.value for member in enum}
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_db.py -q`
Expected: FAIL — `ImportError: cannot import name 'db' from 'pitz_pulse'`.

- [ ] **Step 3: Write the migration**

`src/pitz_pulse/migrations/001_init.sql`:
```sql
-- Initial schema (Spec 02 §3). Contract fields keep their contract names.
CREATE TABLE requests (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    source_area TEXT,
    message_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'classified', 'failed')),
    claim_token TEXT,
    categoria TEXT CHECK (categoria IN ('bug', 'datos', 'acceso', 'automatizacion', 'consulta', 'otro')),
    prioridad TEXT CHECK (prioridad IN ('alta', 'media', 'baja')),
    area_sugerida TEXT CHECK (area_sugerida IN ('backend', 'frontend', 'data', 'devops', 'producto', 'digital_transformation')),
    idioma TEXT CHECK (idioma IN ('es', 'pt')),
    resumen TEXT,
    requiere_info INTEGER CHECK (requiere_info IN (0, 1)),
    pregunta_seguimiento TEXT,
    confianza REAL CHECK (confianza BETWEEN 0 AND 1),
    version_prompt TEXT,
    provider TEXT,
    model TEXT,
    reviewed INTEGER NOT NULL DEFAULT 0 CHECK (reviewed IN (0, 1)),
    corrected INTEGER NOT NULL DEFAULT 0 CHECK (corrected IN (0, 1)),
    original_classification TEXT CHECK (original_classification IS NULL OR json_valid(original_classification)),
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        status <> 'classified' OR (
            categoria IS NOT NULL AND prioridad IS NOT NULL AND area_sugerida IS NOT NULL
            AND idioma IS NOT NULL AND resumen IS NOT NULL AND requiere_info IS NOT NULL
            AND confianza IS NOT NULL AND version_prompt IS NOT NULL AND provider IS NOT NULL
            AND model IS NOT NULL AND original_classification IS NOT NULL
        )
    )
);

CREATE INDEX idx_requests_categoria ON requests (categoria);
CREATE INDEX idx_requests_prioridad ON requests (prioridad);
CREATE INDEX idx_requests_area_sugerida ON requests (area_sugerida);
CREATE INDEX idx_requests_status ON requests (status);
CREATE INDEX idx_requests_created ON requests (created_at, id);

CREATE TABLE corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES requests (id),
    previous_values TEXT NOT NULL CHECK (json_valid(previous_values)),
    new_values TEXT NOT NULL CHECK (json_valid(new_values)),
    author TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_corrections_request ON corrections (request_id, id);
```

- [ ] **Step 4: Write `db.py`**

`src/pitz_pulse/db.py`:
```python
"""SQLite access: one connection per call and atomic, versioned migrations (D6, no ORM)."""

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def connect(path: Path) -> sqlite3.Connection:
    """Autocommit connection; callers open transactions explicitly with BEGIN IMMEDIATE.

    check_same_thread=False: FastAPI may run one request's code on several pool threads;
    a connection is still used by one request at a time.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")  # before WAL: the switch itself may wait
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def format_ts(moment: datetime) -> str:
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def parse_ts(text: str) -> datetime:
    return datetime.strptime(text, _TS_FORMAT).replace(tzinfo=UTC)


def split_statements(sql: str) -> list[str]:
    """Split on ';' only where sqlite3 says a statement is complete (strings, comments).

    The text after the last complete statement may hold only whitespace and comments.
    """
    statements: list[str] = []
    pieces = sql.split(";")
    buffer = ""
    for piece in pieces[:-1]:
        buffer += piece + ";"
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement.strip(";").strip():
                statements.append(statement)
            buffer = ""
    tail = buffer + pieces[-1]
    if _COMMENTS.sub("", tail).strip():
        raise ValueError("migration ends with an incomplete statement")
    return statements


def migrate(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending *.sql files in order, each in its own BEGIN IMMEDIATE transaction.

    Statements are executed one by one: executescript would COMMIT the open transaction
    first and leave a failing file half-applied.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied: list[str] = []
    for file in sorted(directory.glob("*.sql")):
        statements = split_statements(file.read_text(encoding="utf-8"))
        conn.execute("BEGIN IMMEDIATE")
        try:
            done = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (file.stem,)
            ).fetchone()
            if not done:
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (file.stem, format_ts(datetime.now(UTC))),
                )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        if not done:
            applied.append(file.stem)
    return applied
```

Notes: `split_statements` checks the tail with a comment-stripping regex only after `sqlite3.complete_statement` has consumed every complete statement, so a `;` inside a string or comment never splits. `test_migrate_holds_the_write_lock_from_begin` registers a SQL function that probes the lock from a second connection while `migrate`'s transaction is open (a deferred `BEGIN` would not hold it yet).

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_db.py -q`
Expected: PASS (27 tests).

- [ ] **Step 6: Package data check**

Run: `uv run python -c "from pitz_pulse import db; print(sorted(p.name for p in db.MIGRATIONS_DIR.glob('*.sql')))"`
Expected: `['001_init.sql']`. (Hatch includes every file under `src/pitz_pulse`; nothing to configure.)

- [ ] **Step 7: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff format . && uv run ruff check --fix .`
Expected: `414 passed`; ruff reports nothing to change.
```bash
git add src/pitz_pulse/db.py src/pitz_pulse/migrations/001_init.sql tests/db_support.py tests/test_db.py
git commit -F - <<'EOF'
feat: add SQLite connection and atomic migration runner

Statements run one by one inside BEGIN IMMEDIATE (executescript would
commit first and leave partial tables); an incomplete trailing statement is
rejected before anything runs. The schema enforces status/field consistency,
enum parity with the contract, bool and range checks and valid JSON.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3: Repository with typed rows, rollback-always transactions, and the review comparator

**Files:**
- Create: `src/pitz_pulse/repository.py`, `src/pitz_pulse/review.py`
- Test: `tests/test_repository.py`, `tests/test_review_flag.py`

**Interfaces:**
- Consumes: `db.connect`, `db.migrate`; `schema.Classification`, `schema.ClassificationShape`, `schema.RequestInput`; `db_support.write_lock_is_held`.
- Produces:
  - `review.needs_review(confianza: float, threshold: float) -> bool`
  - `repository.StoredRequest` (frozen dataclass): `id, message, source_area, message_hash, status, claim_token, classification: ClassificationShape | None, provider, model, reviewed: bool, corrected: bool, original_classification: dict | None, error, created_at, updated_at`
  - `repository.ListFilters` (frozen dataclass, all `None` by default): `categoria, prioridad, area_sugerida, status, needs_review: bool | None`
  - `Repository(conn)` with: `transaction()` (context manager), `get(id) -> StoredRequest | None`, `insert_pending(req: RequestInput, message_hash, token, now) -> None`, `reclaim(id, token, now) -> None`, `complete(id, token, classification: Classification, provider, model, now) -> bool`, `fail(id, token, kind, now) -> bool`, `list_requests(filters, threshold, limit, offset) -> tuple[list[StoredRequest], int]`, `insert_correction(request_id, previous: dict, new: dict, author, reason, now) -> None`, `update_fields(id, fields: dict, corrected: bool, now) -> None`, `list_corrections(request_id) -> list[dict]` (oldest first)

- [ ] **Step 1: Write the failing tests**

`tests/test_review_flag.py`:
```python
import pytest

from pitz_pulse.review import needs_review


@pytest.mark.parametrize(
    ("confianza", "expected"), [(0.69, True), (0.7, False), (0.71, False), (0.0, True)]
)
def test_below_threshold_needs_review_equal_or_above_does_not(confianza, expected):
    assert needs_review(confianza, 0.7) is expected
```

`tests/test_repository.py`:
```python
import sqlite3

import pytest
from db_support import write_lock_is_held

from pitz_pulse import db
from pitz_pulse.repository import ListFilters, Repository
from pitz_pulse.review import needs_review
from pitz_pulse.schema import Classification, RequestInput

T0 = "2026-09-27T10:00:00.000Z"
T1 = "2026-09-27T10:00:01.000Z"
T2 = "2026-09-27T10:00:02.000Z"
T3 = "2026-09-27T10:00:03.000Z"


def classification(req_id="r1", **overrides):
    values = {
        "id": req_id,
        "categoria": "bug",
        "prioridad": "alta",
        "area_sugerida": "backend",
        "idioma": "es",
        "resumen": "Error al subir catálogo",
        "requiere_info": True,
        "pregunta_seguimiento": "¿Qué archivo?",
        "confianza": 0.9,
        "version_prompt": "v1",
    }
    values.update(overrides)
    return Classification.model_validate(values)


@pytest.fixture
def repo(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.migrate(conn)
    yield Repository(conn)
    conn.close()


def pending(repo, req_id="r1", token="t1", now=T0, message="hola"):
    with repo.transaction():
        repo.insert_pending(RequestInput(id=req_id, message=message), "hash", token, now)


def classified(repo, req_id="r1", now=T1, **overrides):
    pending(repo, req_id, token=f"tok-{req_id}", now=now)
    result = classification(req_id, **overrides)
    with repo.transaction():
        assert repo.complete(req_id, f"tok-{req_id}", result, "fake", "fake-model", now)


def ids(repo, threshold=0.7, limit=20, offset=0, **filters):
    rows, total = repo.list_requests(ListFilters(**filters), threshold, limit, offset)
    return [r.id for r in rows], total


def test_insert_pending_and_get(repo):
    pending(repo)
    row = repo.get("r1")
    assert (row.status, row.claim_token, row.classification, row.error) == (
        "pending",
        "t1",
        None,
        None,
    )
    assert row.created_at == row.updated_at == T0
    assert repo.get("missing") is None


def test_complete_stores_typed_values_and_original(repo):
    classified(repo)
    row = repo.get("r1")
    assert row.status == "classified"
    assert row.claim_token is None
    assert row.classification.requiere_info is True
    assert isinstance(row.classification.requiere_info, bool)
    assert (row.provider, row.model) == ("fake", "fake-model")
    assert row.original_classification["requiere_info"] is True
    assert row.original_classification["id"] == "r1"
    assert row.updated_at == T1


def test_complete_clears_a_previous_error(repo):
    pending(repo)
    repo.conn.execute("UPDATE requests SET error = 'busy' WHERE id = 'r1'")
    with repo.transaction():
        assert repo.complete("r1", "t1", classification(), "fake", "m", T1) is True
    assert repo.get("r1").error is None


def test_complete_with_a_stale_token_changes_nothing(repo):
    pending(repo, token="old")
    with repo.transaction():
        repo.reclaim("r1", "new", T1)
    with repo.transaction():
        assert repo.complete("r1", "old", classification(), "fake", "m", T2) is False
    assert repo.get("r1").status == "pending"


def test_fail_with_a_stale_token_changes_nothing(repo):
    pending(repo, token="old")
    with repo.transaction():
        repo.reclaim("r1", "new", T1)
    with repo.transaction():
        assert repo.fail("r1", "old", "llm_unavailable", T2) is False
    row = repo.get("r1")
    assert (row.status, row.error, row.claim_token, row.updated_at) == ("pending", None, "new", T1)


def test_fail_records_kind_and_reclaim_clears_it(repo):
    pending(repo)
    with repo.transaction():
        assert repo.fail("r1", "t1", "llm_unavailable", T1) is True
    row = repo.get("r1")
    expected = ("failed", "llm_unavailable", None, T1)
    assert (row.status, row.error, row.claim_token, row.updated_at) == expected
    with repo.transaction():
        repo.reclaim("r1", "t2", T2)
    row = repo.get("r1")
    assert (row.status, row.error, row.claim_token, row.updated_at) == ("pending", None, "t2", T2)


def test_fail_on_a_classified_row_changes_nothing(repo):
    classified(repo)
    with repo.transaction():
        assert repo.fail("r1", "tok-r1", "llm_unavailable", T2) is False
    assert repo.get("r1").status == "classified"


def test_transaction_takes_the_write_lock_at_begin(repo, tmp_path):
    with repo.transaction():
        assert write_lock_is_held(tmp_path / "t.db")
    assert not write_lock_is_held(tmp_path / "t.db")


def test_a_statement_error_rolls_back_and_leaves_no_open_transaction(repo):
    pending(repo)
    with pytest.raises(sqlite3.IntegrityError):
        with repo.transaction():
            repo.reclaim("r1", "t2", T1)
            repo.conn.execute("UPDATE requests SET categoria = 'nope' WHERE id = 'r1'")
    assert repo.conn.in_transaction is False
    assert repo.get("r1").claim_token == "t1"  # the reclaim was rolled back too
    with repo.transaction():
        assert repo.fail("r1", "t1", "llm_unavailable", T2) is True


def test_update_fields_and_corrections_history_oldest_first(repo):
    classified(repo)
    with repo.transaction():
        repo.insert_correction("r1", {"categoria": "bug"}, {"categoria": "datos"}, "ana", None, T2)
        repo.update_fields("r1", {"categoria": "datos"}, corrected=True, now=T2)
    with repo.transaction():
        repo.insert_correction("r1", {}, {}, "luis", "ok", T3)
    row = repo.get("r1")
    assert row.classification.categoria == "datos"
    assert (row.corrected, row.reviewed) == (True, True)
    assert row.original_classification["categoria"] == "bug"
    assert repo.list_corrections("r1") == [
        {
            "previous_values": {"categoria": "bug"},
            "new_values": {"categoria": "datos"},
            "author": "ana",
            "reason": None,
            "created_at": T2,
        },
        {
            "previous_values": {},
            "new_values": {},
            "author": "luis",
            "reason": "ok",
            "created_at": T3,
        },
    ]


def test_update_fields_sets_updated_at_and_keeps_corrected_false(repo):
    classified(repo)
    with repo.transaction():
        repo.update_fields("r1", {}, corrected=False, now=T2)
    row = repo.get("r1")
    assert (row.corrected, row.reviewed, row.updated_at) == (False, True, T2)


def test_rows_breaking_a_later_rule_still_read(repo):
    classified(repo)
    repo.conn.execute("UPDATE requests SET resumen = ?", (" ".join(["palabra"] * 25),))
    assert len(repo.get("r1").classification.resumen.split()) == 25
    assert ids(repo) == (["r1"], 1)


def test_list_filters_combine_and_paginate(repo):
    classified(repo, "a", now=T0, categoria="bug", confianza=0.5)
    classified(repo, "b", now=T1, categoria="bug", prioridad="baja", confianza=0.95)
    classified(repo, "c", now=T2, categoria="datos", area_sugerida="frontend", confianza=0.5)
    classified(repo, "e", now=T3, categoria="consulta", prioridad="baja", area_sugerida="frontend")
    pending(repo, "d", token="x", now=T0)

    assert ids(repo, categoria="bug") == (["b", "a"], 2)
    assert ids(repo, categoria="bug", needs_review=True) == (["a"], 1)
    assert ids(repo, prioridad="alta", area_sugerida="frontend") == (["c"], 1)
    assert ids(repo, needs_review=True) == (["c", "a"], 2)
    assert ids(repo, needs_review=False) == (["e", "b", "d"], 3)
    assert ids(repo, status="pending") == (["d"], 1)
    assert ids(repo) == (["e", "c", "b", "a", "d"], 5)
    assert ids(repo, limit=2, offset=1) == (["c", "b"], 5)
    assert ids(repo, offset=100) == ([], 5)


def test_list_orders_by_created_desc_then_id(repo):
    for req_id in ("b", "a", "c"):
        pending(repo, req_id, token=req_id, now=T0)
    pending(repo, "z", token="z", now=T1)
    assert ids(repo)[0] == ["z", "a", "b", "c"]


def test_sql_and_python_review_rules_agree_at_the_threshold(repo):
    for req_id, confianza in (("low", 0.69), ("equal", 0.7), ("high", 0.71)):
        classified(repo, req_id, confianza=confianza)
    in_queue = set(ids(repo, needs_review=True)[0])
    rows, _ = repo.list_requests(ListFilters(), 0.7, 20, 0)
    expected = {r.id for r in rows if needs_review(r.classification.confianza, 0.7)}
    assert in_queue == expected == {"low"}


def test_reviewed_rows_leave_the_review_queue(repo):
    classified(repo, confianza=0.5)
    with repo.transaction():
        repo.update_fields("r1", {}, corrected=False, now=T2)
    assert ids(repo, needs_review=True) == ([], 0)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_repository.py tests/test_review_flag.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pitz_pulse.repository'` (and `pitz_pulse.review`).

- [ ] **Step 3: Write `review.py`**

`src/pitz_pulse/review.py`:
```python
"""Review-queue rule shared by the API (Spec 02) and eval scoring (Spec 03): one comparator."""


def needs_review(confianza: float, threshold: float) -> bool:
    return confianza < threshold
```

- [ ] **Step 4: Write `repository.py`**

`src/pitz_pulse/repository.py`:
```python
"""SQL for requests and corrections. Rows come back typed (bool, enums); JSON columns as dicts."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from pitz_pulse.schema import CONTRACT_FIELDS, Classification, ClassificationShape, RequestInput

_VALUE_FIELDS = CONTRACT_FIELDS[1:]  # categoria … version_prompt (id is the row key)
_NEEDS_REVIEW = "(status = 'classified' AND reviewed = 0 AND confianza < ?)"


@dataclass(frozen=True)
class StoredRequest:
    id: str
    message: str
    source_area: str | None
    message_hash: str
    status: str
    claim_token: str | None
    classification: ClassificationShape | None
    provider: str | None
    model: str | None
    reviewed: bool
    corrected: bool
    original_classification: dict[str, Any] | None
    error: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ListFilters:
    categoria: str | None = None
    prioridad: str | None = None
    area_sugerida: str | None = None
    status: str | None = None
    needs_review: bool | None = None


def _to_stored(row: sqlite3.Row) -> StoredRequest:
    classification = None
    if row["status"] == "classified":
        values = {field: row[field] for field in _VALUE_FIELDS}
        values["requiere_info"] = bool(values["requiere_info"])
        # Types and enums only: a stored row that a later, stricter rule rejects still reads.
        classification = ClassificationShape.model_validate({"id": row["id"], **values})
    original = row["original_classification"]
    return StoredRequest(
        id=row["id"],
        message=row["message"],
        source_area=row["source_area"],
        message_hash=row["message_hash"],
        status=row["status"],
        claim_token=row["claim_token"],
        classification=classification,
        provider=row["provider"],
        model=row["model"],
        reviewed=bool(row["reviewed"]),
        corrected=bool(row["corrected"]),
        original_classification=json.loads(original) if original is not None else None,
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class Repository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @contextmanager
    def transaction(self) -> Iterator["Repository"]:
        """BEGIN IMMEDIATE … COMMIT; any exception (including a failing COMMIT) rolls back."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self
            self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def get(self, request_id: str) -> StoredRequest | None:
        row = self.conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
        return _to_stored(row) if row else None

    def insert_pending(self, req: RequestInput, message_hash: str, token: str, now: str) -> None:
        self.conn.execute(
            "INSERT INTO requests (id, message, source_area, message_hash, status, claim_token,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
            (req.id, req.message, req.source_area, message_hash, token, now, now),
        )

    def reclaim(self, request_id: str, token: str, now: str) -> None:
        self.conn.execute(
            "UPDATE requests SET status = 'pending', claim_token = ?, error = NULL,"
            " updated_at = ? WHERE id = ?",
            (token, now, request_id),
        )

    def complete(
        self,
        request_id: str,
        token: str,
        result: Classification,
        provider: str,
        model: str,
        now: str,
    ) -> bool:
        values = result.model_dump(mode="json")
        columns = ", ".join(f"{field} = ?" for field in _VALUE_FIELDS)
        cursor = self.conn.execute(
            f"UPDATE requests SET status = 'classified', {columns}, provider = ?, model = ?,"
            " original_classification = ?, error = NULL, claim_token = NULL, updated_at = ?"
            " WHERE id = ? AND status = 'pending' AND claim_token = ?",
            (
                *(values[field] for field in _VALUE_FIELDS),
                provider,
                model,
                json.dumps(values, ensure_ascii=False),
                now,
                request_id,
                token,
            ),
        )
        return cursor.rowcount == 1

    def fail(self, request_id: str, token: str, kind: str, now: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE requests SET status = 'failed', error = ?, claim_token = NULL, updated_at = ?"
            " WHERE id = ? AND status = 'pending' AND claim_token = ?",
            (kind, now, request_id, token),
        )
        return cursor.rowcount == 1

    def list_requests(
        self, filters: ListFilters, threshold: float, limit: int, offset: int
    ) -> tuple[list[StoredRequest], int]:
        clauses: list[str] = []
        params: list[Any] = []
        for field in ("categoria", "prioridad", "area_sugerida", "status"):
            value = getattr(filters, field)
            if value is not None:
                clauses.append(f"{field} = ?")
                params.append(str(value))
        if filters.needs_review is not None:
            clauses.append(_NEEDS_REVIEW if filters.needs_review else f"NOT {_NEEDS_REVIEW}")
            params.append(threshold)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        total = self.conn.execute(f"SELECT count(*) FROM requests{where}", params).fetchone()[0]
        rows = self.conn.execute(
            f"SELECT * FROM requests{where} ORDER BY created_at DESC, id ASC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [_to_stored(row) for row in rows], total

    def insert_correction(
        self,
        request_id: str,
        previous: dict[str, Any],
        new: dict[str, Any],
        author: str,
        reason: str | None,
        now: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO corrections (request_id, previous_values, new_values, author, reason,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                request_id,
                json.dumps(previous, ensure_ascii=False),
                json.dumps(new, ensure_ascii=False),
                author,
                reason,
                now,
            ),
        )

    def update_fields(
        self, request_id: str, fields: dict[str, Any], corrected: bool, now: str
    ) -> None:
        """Set corrected contract fields (JSON-mode values) and mark the row reviewed."""
        unknown = set(fields) - set(_VALUE_FIELDS)
        if unknown:
            raise ValueError(f"not a contract field: {sorted(unknown)}")
        assignments = "".join(f"{field} = ?, " for field in fields)
        self.conn.execute(
            f"UPDATE requests SET {assignments}corrected = ?, reviewed = 1, updated_at = ?"
            " WHERE id = ?",
            (*fields.values(), corrected, now, request_id),
        )

    def list_corrections(self, request_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT previous_values, new_values, author, reason, created_at FROM corrections"
            " WHERE request_id = ? ORDER BY id",
            (request_id,),
        ).fetchall()
        return [
            {
                "previous_values": json.loads(row["previous_values"]),
                "new_values": json.loads(row["new_values"]),
                "author": row["author"],
                "reason": row["reason"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
```

`update_fields` interpolates only column names that passed the `_VALUE_FIELDS` allow-list; values are always bound parameters. Reads use `ClassificationShape` (types and enums only) so a stored row that a later, stricter contract rule rejects still lists and reads; every write path (model output, PATCH) validates with `Classification`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_repository.py tests/test_review_flag.py -q`
Expected: PASS (16 + 4 tests).

- [ ] **Step 6: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff format . && uv run ruff check --fix .`
Expected: `434 passed`; ruff reports nothing to change.
```bash
git add src/pitz_pulse/repository.py src/pitz_pulse/review.py tests/test_repository.py tests/test_review_flag.py
git commit -F - <<'EOF'
feat: add typed repository with rollback-always transactions

Rows come back as typed contract values (types and enums only, so older rows
always read); complete/fail are guarded by the claim token; any error inside
a transaction rolls back and leaves the connection usable.
review.needs_review is the single comparator for the review queue.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: API settings, domain errors, UTF-8 input guard, deadline docstring

**Files:**
- Create: `src/pitz_pulse/settings_api.py`, `src/pitz_pulse/errors.py`
- Modify: `src/pitz_pulse/schema.py` (`ID_PATTERN`, `ensure_utf8`, validators), `src/pitz_pulse/config.py` (`deadline_s` docstring)
- Test: `tests/test_settings_api.py`, `tests/test_errors.py`, `tests/test_schema.py` (add a case)

**Interfaces:**
- Consumes: `config.parse_llm_settings`, `config.ConfigError`, `config.LLMSettings`.
- Produces:
  - `settings_api.QUEUE_WAIT_S = 30.0`
  - `settings_api.ApiSettings(llm, api_key, db_path, pending_stale_s)` (frozen; `api_key` `repr=False`)
  - `settings_api.stale_floor_s(llm: LLMSettings, queue_wait_s: float = QUEUE_WAIT_S) -> int`
  - `settings_api.parse_api_settings(env: Mapping[str, str]) -> ApiSettings`
  - `errors.DomainError` (attrs `http_status: int`, `code: str`, `detail: str`, `retry_after_s: int | None`, `attempts: int`), subclasses `IdConflict`, `InProgress(retry_after_s)`, `NotFound`, `NotClassified`, `ContractViolation(fields)`, `ClassificationFailed(kind)`, `Busy`, `DbBusy`
  - `errors.field_errors(exc: pydantic.ValidationError, prefix=("body",)) -> list[dict]`
  - `schema.ID_PATTERN`, `schema.ensure_utf8(value: str) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/test_settings_api.py`:
```python
import pytest

from pitz_pulse.config import ConfigError
from pitz_pulse.settings_api import parse_api_settings, stale_floor_s

BASE = {"LLM_PROVIDER": "mock", "API_KEY": "test-key"}


def test_api_key_is_required():
    with pytest.raises(ConfigError, match="API_KEY"):
        parse_api_settings({"LLM_PROVIDER": "mock"})


@pytest.mark.parametrize("key", ["clé-secreta", "tab\tkey"])
def test_api_key_must_be_printable_ascii(key):
    with pytest.raises(ConfigError, match="API_KEY must be printable ASCII"):
        parse_api_settings({**BASE, "API_KEY": key})


def test_stale_window_defaults_to_the_derived_minimum():
    settings = parse_api_settings(BASE)
    assert settings.pending_stale_s == stale_floor_s(settings.llm) == 530


def test_raising_the_timeout_raises_the_default_instead_of_failing():
    settings = parse_api_settings({**BASE, "LLM_TIMEOUT_SECONDS": "60"})
    assert settings.pending_stale_s == 770  # 2 × 340 + 30 + 60


def test_explicit_stale_window_below_the_minimum_names_both_variables():
    with pytest.raises(ConfigError) as info:
        parse_api_settings({**BASE, "PENDING_STALE_SECONDS": "100"})
    message = str(info.value)
    assert "PENDING_STALE_SECONDS" in message
    assert "LLM_TIMEOUT_SECONDS" in message
    assert "530" in message


def test_explicit_stale_window_must_be_an_integer():
    with pytest.raises(ConfigError, match="PENDING_STALE_SECONDS"):
        parse_api_settings({**BASE, "PENDING_STALE_SECONDS": "soon"})


def test_db_path_defaults_under_app_root_and_relative_paths_are_rooted(tmp_path):
    settings = parse_api_settings(BASE)
    assert settings.db_path == settings.llm.app_root / "data" / "pitz_pulse.db"
    settings = parse_api_settings({**BASE, "DB_PATH": "var/x.db"})
    assert settings.db_path == settings.llm.app_root / "var" / "x.db"
    settings = parse_api_settings({**BASE, "DB_PATH": str(tmp_path / "abs.db")})
    assert settings.db_path == tmp_path / "abs.db"


def test_repr_never_shows_keys():
    settings = parse_api_settings({**BASE, "API_KEY": "sentinel-api-key"})
    assert "sentinel-api-key" not in repr(settings)


def test_explicit_real_provider_without_its_key_fails_fast_naming_the_variable():
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        parse_api_settings({"LLM_PROVIDER": "anthropic_api", "API_KEY": "k"})
```

`tests/test_errors.py`:
```python
import pytest
from pydantic import ValidationError

from pitz_pulse.errors import (
    Busy,
    ClassificationFailed,
    ContractViolation,
    DbBusy,
    DomainError,
    IdConflict,
    InProgress,
    NotClassified,
    NotFound,
    field_errors,
)
from pitz_pulse.schema import RequestInput


@pytest.mark.parametrize(
    ("error", "status", "code", "retry_after"),
    [
        (IdConflict(), 409, "id_conflict", None),
        (InProgress(12), 409, "in_progress", 12),
        (NotFound(), 404, "not_found", None),
        (NotClassified(), 409, "not_classified", None),
        (ContractViolation([]), 422, "validation_error", None),
        (ClassificationFailed("llm_rejected"), 502, "classification_failed", None),
        (Busy(), 503, "busy", 30),
        (DbBusy(), 503, "db_busy", 1),
    ],
)
def test_each_domain_error_carries_its_http_mapping(error, status, code, retry_after):
    assert isinstance(error, DomainError)
    assert (error.http_status, error.code, error.retry_after_s) == (status, code, retry_after)
    assert error.detail and error.attempts == 0


def test_classification_failed_exposes_the_kind():
    assert ClassificationFailed("invalid_output").kind == "invalid_output"


def test_field_errors_prefix_locations_and_never_echo_input():
    sentinel = "SENTINEL-TEXT " * 700  # too long: the error is on the message field itself
    with pytest.raises(ValidationError) as info:
        RequestInput.model_validate({"id": "a", "message": sentinel})
    assert "SENTINEL-TEXT" in str(info.value)  # pydantic itself echoes the input
    fields = field_errors(info.value)
    assert [error["loc"] for error in fields] == [["body", "message"]]
    assert "SENTINEL-TEXT" not in str(fields)
```

Append to the end of `tests/test_schema.py` (after two blank lines; `pytest`, `ValidationError` and `RequestInput` are already imported at the top of that file):
```python
def test_lone_surrogates_are_rejected_as_invalid_text():
    with pytest.raises(ValidationError, match="valid UTF-8"):
        RequestInput.model_validate({"id": "a", "message": "hola \ud800"})
    with pytest.raises(ValidationError, match="valid UTF-8"):
        RequestInput.model_validate({"id": "a", "message": "hola", "source_area": "\udfff"})
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_settings_api.py tests/test_errors.py tests/test_schema.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pitz_pulse.settings_api'` (collection error; `pitz_pulse.errors` likewise).

- [ ] **Step 3: Add the UTF-8 guard and `ID_PATTERN` to `schema.py`**

In `src/pitz_pulse/schema.py`, replace everything from the line `class RequestInput(BaseModel):` up to (not including) the line `class _ModelFields(BaseModel):` with the block below, followed by two blank lines:
```python
ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


def ensure_utf8(value: str) -> str:
    """JSON allows lone surrogates; they cannot be hashed, stored or sent, so reject them."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("must be valid UTF-8 text") from None
    return value


class RequestInput(BaseModel):
    model_config = _FORBID_EXTRA

    id: StrictStr = Field(pattern=ID_PATTERN)
    message: StrictStr
    source_area: StrictStr | None = None

    @field_validator("message")
    @classmethod
    def _message_length(cls, value: str) -> str:
        ensure_utf8(value)
        size = len(value.strip())
        if not 1 <= size <= MAX_MESSAGE_CHARS or len(value) > MAX_MESSAGE_RAW_CHARS:
            raise ValueError(
                f"must have 1-{MAX_MESSAGE_CHARS} characters after strip "
                f"and at most {MAX_MESSAGE_RAW_CHARS} in total"
            )
        return value  # the original text is stored and hashed; never strip it

    @field_validator("source_area")
    @classmethod
    def _source_area_length(cls, value: str | None) -> str | None:
        if value is not None:
            ensure_utf8(value)
            if not 1 <= len(value.strip()) <= 100:
                raise ValueError("must have 1-100 characters after strip")
        return value
```

- [ ] **Step 4: Fix the `deadline_s` docstring (D28)**

In `src/pitz_pulse/config.py`, replace
```python
        """Hard per-invoke bound: every attempt timeout plus the capped waits between them."""
```
with
```python
        """Per-invoke budget, checked between attempts (D28): not a wall-clock kill."""
```

- [ ] **Step 5: Write `errors.py`**

`src/pitz_pulse/errors.py`:
```python
"""Domain errors. Each knows its HTTP mapping; messages never carry request or model text."""

from typing import Any

from pydantic import ValidationError


class DomainError(Exception):
    http_status = 500
    code = "internal_error"
    detail = "internal error"
    retry_after_s: int | None = None

    def __init__(self) -> None:
        super().__init__(self.code)
        self.attempts = 0  # model attempts spent on this request, for the outcome log


class IdConflict(DomainError):
    http_status, code = 409, "id_conflict"
    detail = "this id was already used with a different message"


class InProgress(DomainError):
    http_status, code = 409, "in_progress"
    detail = "this request is being classified; retry later"

    def __init__(self, retry_after_s: int):
        super().__init__()
        self.retry_after_s = retry_after_s


class NotFound(DomainError):
    http_status, code, detail = 404, "not_found", "request not found"


class NotClassified(DomainError):
    http_status, code = 409, "not_classified"
    detail = "only classified requests can be corrected"


class ContractViolation(DomainError):
    http_status, code = 422, "validation_error"
    detail = "the corrected values break the contract"

    def __init__(self, fields: list[dict[str, Any]]):
        super().__init__()
        self.fields = fields


class ClassificationFailed(DomainError):
    http_status, code = 502, "classification_failed"
    detail = "the request could not be classified; retry later"

    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind


class Busy(DomainError):
    http_status, code, retry_after_s = 503, "busy", 30
    detail = "too many classifications in progress; retry later"


class DbBusy(DomainError):
    http_status, code, retry_after_s = 503, "db_busy", 1
    detail = "the database is busy; retry"


def field_errors(exc: ValidationError, prefix: tuple[str, ...] = ("body",)) -> list[dict[str, Any]]:
    """Locations and messages only; pydantic's `input` is dropped (it may echo user text)."""
    return [
        {"loc": [*prefix, *(str(part) for part in error["loc"])], "msg": error["msg"]}
        for error in exc.errors(include_url=False)
    ]
```

- [ ] **Step 6: Write `settings_api.py`**

`src/pitz_pulse/settings_api.py`:
```python
"""HTTP-only settings. Composes LLMSettings (D21); never serialized (secrets, catalog checks)."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pitz_pulse.config import ConfigError, LLMSettings, parse_llm_settings

QUEUE_WAIT_S = 30.0  # longest wait for a model slot before 503 busy (Spec 02 §2)


@dataclass(frozen=True)
class ApiSettings:
    llm: LLMSettings
    api_key: str = field(repr=False)
    db_path: Path
    pending_stale_s: int


def stale_floor_s(llm: LLMSettings, queue_wait_s: float = QUEUE_WAIT_S) -> int:
    """Longest a live request can plausibly stay pending (budget per attempt, D28)."""
    return math.ceil((1 + llm.invalid_output_retries) * llm.deadline_s + queue_wait_s + 60)


def _text(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def parse_api_settings(env: Mapping[str, str]) -> ApiSettings:
    llm = parse_llm_settings(env)
    api_key = _text(env, "API_KEY")
    if not api_key:
        raise ConfigError("API_KEY is required for the HTTP API")
    if not (api_key.isascii() and api_key.isprintable()):
        raise ConfigError("API_KEY must be printable ASCII")
    raw_path = _text(env, "DB_PATH")
    db_path = Path(raw_path) if raw_path else Path("data") / "pitz_pulse.db"
    if not db_path.is_absolute():
        db_path = llm.app_root / db_path  # never the current working directory
    floor = stale_floor_s(llm)
    raw_stale = _text(env, "PENDING_STALE_SECONDS")
    if not raw_stale:
        return ApiSettings(llm, api_key, db_path, floor)
    try:
        stale = int(raw_stale)
    except ValueError:
        raise ConfigError("PENDING_STALE_SECONDS must be an integer") from None
    if stale < floor:
        raise ConfigError(
            f"PENDING_STALE_SECONDS={stale} is below the minimum {floor} derived from "
            "LLM_TIMEOUT_SECONDS, LLM_MAX_RETRIES and INVALID_OUTPUT_RETRIES; raise it or unset it"
        )
    return ApiSettings(llm, api_key, db_path, stale)
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_settings_api.py tests/test_errors.py tests/test_schema.py -q`
Expected: PASS. If `test_raising_the_timeout…` computes a different number, recompute by hand from `config.deadline_s` (`60 × 4 + 3 × 30 + 10 = 340`; `2 × 340 + 30 + 60 = 770`) and fix the code, not the test.

- [ ] **Step 8: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff format . && uv run ruff check --fix .`
Expected: `455 passed`; ruff reports nothing to change.
```bash
git add src/pitz_pulse/settings_api.py src/pitz_pulse/errors.py src/pitz_pulse/schema.py src/pitz_pulse/config.py tests/test_settings_api.py tests/test_errors.py tests/test_schema.py
git commit -F - <<'EOF'
feat: add API settings, domain errors and a UTF-8 input guard

ApiSettings composes LLMSettings (D21) and defaults the stale window to its
derived minimum so a timeout change never blocks startup. Domain errors
carry their HTTP mapping. Lone surrogates are rejected at validation instead
of crashing hashing and SQLite. deadline_s is documented as a budget (D28).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5: TriageService — idempotent intake, model slots, lost claims, get/list

**Files:**
- Create: `src/pitz_pulse/service.py`, `src/pitz_pulse/intake.py`, `tests/service_support.py`, `tests/test_idempotency.py`, `tests/test_lost_claims.py`, `tests/test_service_failures.py`
- Modify: `tests/fakes.py` (add `GateAdapter`)

**Interfaces:**
- Consumes: `db.connect/format_ts/parse_ts`, `Repository`, `StoredRequest`, `ListFilters`, `review.needs_review`, `errors.*`, `settings_api.QUEUE_WAIT_S`, `classifier.Classifier` (`classify`, `adapter`, `settings`), `ClassificationError(kind, attempts)`, `ClassificationCrash`, `ClassifyOutcome`, `logs.log_event`.
- Produces:
  - `TriageService(IntakeMixin)(db_path: Path, classifier: Classifier, *, threshold: float, pending_stale_s: int, queue_wait_s: float = QUEUE_WAIT_S, clock: Callable[[], datetime] = utc_now, complete_backoff_s: tuple[float, ...] = (0.2, 0.4, 0.8))`
  - `.create(req: RequestInput) -> tuple[StoredRequest, bool]` (bool = this call classified) — `IntakeMixin`
  - `.get(request_id) -> tuple[StoredRequest, list[dict]]` (row, corrections)
  - `.list_requests(filters: ListFilters, limit: int, offset: int) -> tuple[list[StoredRequest], int]`
  - `.needs_review(row: StoredRequest) -> bool`
  - `.connection()` context manager yielding a `Repository` (`database is locked` → `DbBusy`)
  - `._acquire_slot() -> bool`, `._release_slot()`; attributes `_waiters`, `_max_waiters` (tests read them)
  - `.correct(...)` is added in Task 6.
  - `service.utc_now() -> datetime`
  - Log events (logger `pitz_pulse.service`): `request_outcome`, `request_claim_lost`, `request_complete_failed`, `request_fail_write_failed`.
  - Test helpers: `fakes.GateAdapter`; `service_support.FakeClock`, `make_service`, `unavailable`, `start(fn, *args) -> (thread, outcome)`.

- [ ] **Step 1: Add `GateAdapter` to `tests/fakes.py`**

Insert these two lines at the very top of `tests/fakes.py` (before `from pitz_pulse.models_catalog import ProviderCaps`):
```python
import threading

```
Then append to the end of the file (after two blank lines):
```python
class GateAdapter(FakeAdapter):
    """invoke() blocks until `release` is set, so a test can hold calls in flight."""

    def __init__(self, responses):
        super().__init__(responses)
        self.release = threading.Event()
        self.entered = threading.Semaphore(0)
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def invoke(self, system, user, tool, deadline_s):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        self.entered.release()
        try:
            if not self.release.wait(timeout=10):
                raise AssertionError("GateAdapter was never released")
            return super().invoke(system, user, tool, deadline_s)
        finally:
            with self._lock:
                self.active -= 1
```

- [ ] **Step 2: Write `tests/service_support.py`**

`tests/service_support.py`:
```python
import threading
from datetime import UTC, datetime, timedelta

from pitz_pulse import db
from pitz_pulse.classifier import build_classifier
from pitz_pulse.config import parse_llm_settings
from pitz_pulse.providers.base import LLMError
from pitz_pulse.service import TriageService


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 9, 27, 10, 0, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += timedelta(seconds=seconds)


def make_service(tmp_path, adapter, *, concurrency=4, clock=None, **overrides):
    settings = parse_llm_settings({"LLM_PROVIDER": "mock", "LLM_CONCURRENCY": str(concurrency)})
    path = tmp_path / "t.db"
    conn = db.connect(path)
    db.migrate(conn)
    conn.close()
    options = {"threshold": 0.7, "pending_stale_s": 530, "queue_wait_s": 0.2}
    options.update(overrides)
    return TriageService(
        path,
        build_classifier(settings, adapter),
        clock=clock or FakeClock(),
        complete_backoff_s=(0.0, 0.0, 0.0),
        **options,
    )


def unavailable():
    return LLMError("unavailable", "APIConnectionError")


def start(fn, *args):
    """Run fn(*args) in a thread; the returned dict gets "result" or "error" once joined."""
    outcome = {}

    def run():
        try:
            outcome["result"] = fn(*args)
        except Exception as exc:  # the test asserts on it
            outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome
```

- [ ] **Step 3: Write the failing tests**

`tests/test_idempotency.py`:
```python
import json
import logging

import pytest
from fakes import FakeAdapter, GateAdapter, make_call
from service_support import FakeClock, make_service, start, unavailable

from pitz_pulse.classifier import ClassificationCrash
from pitz_pulse.errors import ClassificationFailed, IdConflict, InProgress
from pitz_pulse.schema import RequestInput

REQ = RequestInput(id="MSG-01", message="Error 500 al subir catálogo")


def test_same_id_and_text_calls_the_model_once_then_replays(tmp_path):
    adapter = FakeAdapter([make_call()])
    service = make_service(tmp_path, adapter)
    row, created = service.create(REQ)
    assert (row.status, created) == ("classified", True)
    row, created = service.create(REQ)
    assert (row.status, created) == ("classified", False)
    assert len(adapter.calls) == 1


def test_unicode_message_round_trips_and_replays(tmp_path):
    adapter = FakeAdapter([make_call()])
    service = make_service(tmp_path, adapter)
    req = RequestInput(id="u1", message="Não consigo acessar 🚀 ação")
    service.create(req)
    row, created = service.create(req)
    assert created is False
    assert service.get("u1")[0].message == "Não consigo acessar 🚀 ação"


def test_same_id_with_different_text_is_a_conflict(tmp_path):
    service = make_service(tmp_path, FakeAdapter([make_call()]))
    service.create(REQ)
    with pytest.raises(IdConflict):
        service.create(RequestInput(id="MSG-01", message="otro texto"))


def test_whitespace_difference_counts_as_different_text(tmp_path):
    service = make_service(tmp_path, FakeAdapter([make_call()]))
    service.create(REQ)
    with pytest.raises(IdConflict):
        service.create(RequestInput(id="MSG-01", message=REQ.message + " "))


def test_two_threads_same_id_make_exactly_one_model_call(tmp_path):
    adapter = GateAdapter([make_call(), make_call()])
    service = make_service(tmp_path, adapter)
    first, outcome = start(service.create, REQ)
    assert adapter.entered.acquire(timeout=5)  # first call is in flight
    with pytest.raises(InProgress) as info:
        service.create(REQ)
    assert info.value.retry_after_s > 0
    adapter.release.set()
    first.join()
    assert len(adapter.calls) == 1
    assert outcome["result"][1] is True


def test_pending_row_survives_restart_as_in_progress(tmp_path):
    adapter = GateAdapter([make_call()])
    clock = FakeClock()
    service = make_service(tmp_path, adapter, clock=clock)
    worker, _ = start(service.create, REQ)
    assert adapter.entered.acquire(timeout=5)
    restarted = make_service(tmp_path, FakeAdapter([]), clock=clock)  # same DB file
    with pytest.raises(InProgress) as info:
        restarted.create(REQ)
    assert 0 < info.value.retry_after_s <= 530
    adapter.release.set()
    worker.join()


def test_stale_pending_row_is_reclaimed(tmp_path):
    clock = FakeClock()
    gate = GateAdapter([make_call()])
    service = make_service(tmp_path, gate, clock=clock)
    worker, _ = start(service.create, REQ)
    assert gate.entered.acquire(timeout=5)
    clock.advance(531)
    fresh = make_service(tmp_path, FakeAdapter([make_call()]), clock=clock)
    row, created = fresh.create(REQ)
    assert (row.status, created) == ("classified", True)
    gate.release.set()
    worker.join()


def test_failed_classification_is_recorded_then_retried(tmp_path):
    adapter = FakeAdapter([unavailable(), make_call()])
    service = make_service(tmp_path, adapter)
    with pytest.raises(ClassificationFailed) as info:
        service.create(REQ)
    assert (info.value.kind, info.value.attempts) == ("llm_unavailable", 1)
    row, _ = service.get(REQ.id)
    assert (row.status, row.error) == ("failed", "llm_unavailable")
    row, created = service.create(REQ)
    assert (row.status, row.error, created) == ("classified", None, True)


def test_failing_twice_stays_failed(tmp_path):
    service = make_service(tmp_path, FakeAdapter([unavailable(), unavailable()]))
    for _ in range(2):
        with pytest.raises(ClassificationFailed):
            service.create(REQ)
    assert service.get(REQ.id)[0].status == "failed"


def test_failed_retry_refreshes_updated_at_so_a_concurrent_post_waits(tmp_path):
    clock = FakeClock()
    gate = GateAdapter([make_call()])
    service = make_service(tmp_path, FakeAdapter([unavailable()]), clock=clock)
    with pytest.raises(ClassificationFailed):
        service.create(REQ)
    clock.advance(10_000)  # the failed row is old
    retrying = make_service(tmp_path, gate, clock=clock)
    worker, _ = start(retrying.create, REQ)
    assert gate.entered.acquire(timeout=5)
    with pytest.raises(InProgress):
        make_service(tmp_path, FakeAdapter([]), clock=clock).create(REQ)
    gate.release.set()
    worker.join()


def test_retry_classifies_the_stored_source_area(tmp_path):
    adapter = FakeAdapter([unavailable(), make_call()])
    service = make_service(tmp_path, adapter)
    with pytest.raises(ClassificationFailed):
        service.create(RequestInput(id="s1", message="hola", source_area="Ventas"))
    service.create(RequestInput(id="s1", message="hola", source_area="Soporte"))
    assert "<source_area>Ventas</source_area>" in adapter.calls[1]["user"]
    assert service.get("s1")[0].source_area == "Ventas"


def test_crash_marks_the_row_internal_error_and_allows_retry(tmp_path):
    service = make_service(tmp_path, FakeAdapter([RuntimeError("SENTINEL"), make_call()]))
    with pytest.raises(ClassificationCrash):
        service.create(REQ)
    assert service.get(REQ.id)[0].error == "internal_error"
    assert service.create(REQ)[1] is True


def test_each_create_logs_one_outcome_without_message_text(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.service")
    service = make_service(tmp_path, FakeAdapter([make_call()]))
    req = RequestInput(id="s1", message="SENTINEL-MESSAGE-TEXT")
    service.create(req)
    service.create(req)
    outcomes = [r for r in caplog.records if r.getMessage() == "request_outcome"]
    assert [r.fields["http_status"] for r in outcomes] == [201, 200]
    assert outcomes[0].fields["attempts"] == 1
    assert "SENTINEL-MESSAGE-TEXT" not in json.dumps([r.fields for r in caplog.records])


def test_needs_review_follows_the_threshold_without_rewriting_rows(tmp_path):
    service = make_service(tmp_path, FakeAdapter([make_call()]))
    row, _ = service.create(REQ)  # confianza 0.86
    assert service.needs_review(row) is False
    strict = make_service(tmp_path, FakeAdapter([]), threshold=0.9)
    assert strict.needs_review(strict.get(REQ.id)[0]) is True
```

`tests/test_lost_claims.py`:
```python
"""G16/G24: a worker whose row was re-claimed never overwrites it and never answers 201."""

import logging

from fakes import VALID_OUTPUT, FakeAdapter, GateAdapter, make_call
from service_support import FakeClock, make_service, start, unavailable

from pitz_pulse.errors import ClassificationFailed, InProgress
from pitz_pulse.schema import RequestInput

REQ = RequestInput(id="MSG-01", message="Error 500 al subir catálogo")
DATOS = {**VALID_OUTPUT, "categoria": "datos"}


def hold_first_worker(tmp_path, response):
    """A starts and blocks inside the model call; then its row becomes stale."""
    clock = FakeClock()
    gate = GateAdapter([response])
    worker, outcome = start(make_service(tmp_path, gate, clock=clock).create, REQ)
    assert gate.entered.acquire(timeout=5)
    clock.advance(531)
    return clock, gate, worker, outcome


def test_lost_claim_on_a_classified_row_answers_200_never_201(tmp_path, caplog):
    clock, gate, worker, outcome = hold_first_worker(tmp_path, make_call())
    make_service(tmp_path, FakeAdapter([make_call()]), clock=clock).create(REQ)  # B wins
    caplog.set_level(logging.INFO, logger="pitz_pulse.service")
    gate.release.set()
    worker.join()
    row, created = outcome["result"]
    assert (row.status, created) == ("classified", False)
    assert any(r.getMessage() == "request_claim_lost" for r in caplog.records)


def test_late_worker_never_completes_a_row_re_claimed_in_flight(tmp_path):
    clock, gate_a, worker_a, outcome_a = hold_first_worker(tmp_path, make_call())
    gate_b = GateAdapter([make_call(DATOS)])
    service_b = make_service(tmp_path, gate_b, clock=clock)
    worker_b, outcome_b = start(service_b.create, REQ)
    assert gate_b.entered.acquire(timeout=5)  # B re-claimed and is in flight

    gate_a.release.set()
    worker_a.join()
    assert isinstance(outcome_a["error"], InProgress)
    row = service_b.get(REQ.id)[0]
    assert row.status == "pending" and row.claim_token is not None

    gate_b.release.set()
    worker_b.join()
    row, created = outcome_b["result"]
    assert (row.status, row.classification.categoria, created) == ("classified", "datos", True)


def test_lost_claim_on_a_failed_row_is_502_with_that_kind(tmp_path):
    clock, gate, worker, outcome = hold_first_worker(tmp_path, make_call())
    service_b = make_service(tmp_path, FakeAdapter([unavailable()]), clock=clock)
    worker_b, outcome_b = start(service_b.create, REQ)
    worker_b.join()
    assert isinstance(outcome_b["error"], ClassificationFailed)

    gate.release.set()
    worker.join()
    error = outcome["error"]
    assert isinstance(error, ClassificationFailed)
    assert (error.kind, error.attempts) == ("llm_unavailable", 1)
    assert service_b.get(REQ.id)[0].status == "failed"


def test_late_failure_after_a_re_claim_does_not_fail_the_row(tmp_path):
    clock, gate_a, worker_a, outcome_a = hold_first_worker(tmp_path, unavailable())
    gate_b = GateAdapter([make_call()])
    service_b = make_service(tmp_path, gate_b, clock=clock)
    worker_b, outcome_b = start(service_b.create, REQ)
    assert gate_b.entered.acquire(timeout=5)

    gate_a.release.set()
    worker_a.join()
    assert isinstance(outcome_a["error"], InProgress)
    row = service_b.get(REQ.id)[0]
    assert (row.status, row.error) == ("pending", None)

    gate_b.release.set()
    worker_b.join()
    assert outcome_b["result"][1] is True


def test_crash_after_a_lost_claim_answers_from_the_current_row(tmp_path):
    clock, gate, worker, outcome = hold_first_worker(tmp_path, RuntimeError("SENTINEL"))
    make_service(tmp_path, FakeAdapter([make_call()]), clock=clock).create(REQ)  # B wins
    gate.release.set()
    worker.join()
    row, created = outcome["result"]
    assert (row.status, row.error, created) == ("classified", None, False)
```

`tests/test_service_failures.py`:
```python
"""Model slots, locked writes and the outcome log on every error path."""

import json
import logging
import sqlite3
import threading
import time

import pytest
from fakes import FakeAdapter, GateAdapter, make_call
from service_support import FakeClock, make_service, start, unavailable

from pitz_pulse import repository
from pitz_pulse.classifier import ClassificationCrash
from pitz_pulse.errors import Busy, DbBusy, IdConflict, InProgress
from pitz_pulse.schema import RequestInput

REQ = RequestInput(id="MSG-01", message="Error 500 al subir catálogo")


def locked(self, *args, **kwargs):
    raise sqlite3.OperationalError("database is locked")


def outcomes(caplog):
    return [r.fields for r in caplog.records if r.getMessage() == "request_outcome"]


def test_model_slots_bound_concurrent_classifications(tmp_path):
    adapter = GateAdapter([make_call() for _ in range(6)])
    service = make_service(tmp_path, adapter, concurrency=2, queue_wait_s=5)
    threads = [
        threading.Thread(target=service.create, args=(RequestInput(id=f"c{i}", message="hola"),))
        for i in range(6)
    ]
    for thread in threads:
        thread.start()
    assert adapter.entered.acquire(timeout=5) and adapter.entered.acquire(timeout=5)
    assert adapter.entered.acquire(timeout=0.3) is False  # a third call never starts
    adapter.release.set()
    for thread in threads:
        thread.join()
    assert adapter.peak == 2
    assert len(adapter.calls) == 6


def test_no_slot_in_time_marks_busy_then_retry_succeeds(tmp_path):
    gate = GateAdapter([make_call(), make_call()])
    service = make_service(tmp_path, gate, concurrency=1, queue_wait_s=0.1)
    worker, _ = start(service.create, RequestInput(id="a", message="x"))
    assert gate.entered.acquire(timeout=5)
    with pytest.raises(Busy):
        service.create(REQ)
    assert service.get(REQ.id)[0].error == "busy"
    gate.release.set()
    worker.join()
    assert service.create(REQ)[1] is True


def test_waiters_beyond_twice_the_concurrency_are_busy_at_once(tmp_path):
    gate = GateAdapter([make_call() for _ in range(3)])
    service = make_service(tmp_path, gate, concurrency=1, queue_wait_s=5)
    workers = [start(service.create, RequestInput(id=f"w{i}", message="x"))[0] for i in range(3)]
    assert gate.entered.acquire(timeout=5)
    deadline = time.monotonic() + 5
    while service._waiters < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert service._waiters == 2
    began = time.monotonic()
    with pytest.raises(Busy):
        service.create(REQ)
    assert time.monotonic() - began < 1
    gate.release.set()
    for worker in workers:
        worker.join()


def test_new_arrivals_never_take_the_fast_path_while_others_wait(tmp_path):
    service = make_service(tmp_path, FakeAdapter([]), concurrency=1)
    service._waiters = service._max_waiters  # a full queue; the only slot is free
    assert service._acquire_slot() is False


def test_already_classified_requests_never_wait_for_a_slot(tmp_path):
    gate = GateAdapter([make_call(), make_call()])
    service = make_service(tmp_path, gate, concurrency=1, queue_wait_s=0.1)
    gate.release.set()
    service.create(REQ)
    assert gate.entered.acquire(timeout=1)  # drain the first call's signal
    gate.release.clear()
    worker, _ = start(service.create, RequestInput(id="a", message="x"))
    assert gate.entered.acquire(timeout=5)
    assert service.create(REQ)[1] is False  # no Busy although the only slot is taken
    gate.release.set()
    worker.join()


def test_complete_is_retried_when_the_database_is_locked(tmp_path, monkeypatch):
    service = make_service(tmp_path, FakeAdapter([make_call()]))
    original = repository.Repository.complete
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(repository.Repository, "complete", flaky)
    row, created = service.create(REQ)
    assert (row.status, created, calls["n"]) == ("classified", True, 2)


def test_complete_locked_every_time_is_db_busy(tmp_path, monkeypatch):
    service = make_service(tmp_path, FakeAdapter([make_call()]))
    monkeypatch.setattr(repository.Repository, "complete", locked)
    with pytest.raises(DbBusy) as info:
        service.create(REQ)
    assert info.value.attempts == 1
    assert service.get(REQ.id)[0].status == "pending"


def test_unexpected_error_in_complete_fails_the_row(tmp_path, monkeypatch):
    service = make_service(tmp_path, FakeAdapter([make_call()]))

    def broken(self, *args, **kwargs):
        raise sqlite3.IntegrityError("SENTINEL")

    monkeypatch.setattr(repository.Repository, "complete", broken)
    with pytest.raises(sqlite3.IntegrityError):
        service.create(REQ)
    assert service.get(REQ.id)[0].error == "internal_error"


def test_locked_fail_write_is_logged_and_surfaces_db_busy(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.service")
    service = make_service(tmp_path, FakeAdapter([unavailable()]))
    monkeypatch.setattr(repository.Repository, "fail", locked)
    with pytest.raises(DbBusy) as info:
        service.create(REQ)
    assert info.value.attempts == 1
    events = [(r.getMessage(), r.fields) for r in caplog.records]
    assert ("request_fail_write_failed", {"id": REQ.id, "exc_type": "DbBusy"}) in events
    assert service.get(REQ.id)[0].status == "pending"


def test_locked_fail_write_after_a_crash_is_logged(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.service")
    service = make_service(tmp_path, FakeAdapter([RuntimeError("x")]))
    monkeypatch.setattr(repository.Repository, "fail", locked)
    with pytest.raises(ClassificationCrash):
        service.create(REQ)
    events = [r.getMessage() for r in caplog.records]
    assert "request_fail_write_failed" in events


def test_outcome_log_carries_409_and_503_statuses(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.service")
    gate = GateAdapter([make_call()])
    service = make_service(tmp_path, gate, concurrency=1, queue_wait_s=0.05)
    worker, _ = start(service.create, REQ)
    assert gate.entered.acquire(timeout=5)
    with pytest.raises(InProgress):
        service.create(REQ)
    with pytest.raises(Busy):
        service.create(RequestInput(id="b1", message="x"))
    gate.release.set()
    worker.join()
    with pytest.raises(IdConflict):
        service.create(RequestInput(id=REQ.id, message="otro"))
    monkeypatch.setattr(repository.Repository, "get", locked)
    with pytest.raises(DbBusy):
        service.create(RequestInput(id="c1", message="x"))
    statuses = [(f["id"], f["http_status"], f["error"]) for f in outcomes(caplog)]
    assert statuses == [
        (REQ.id, 409, "in_progress"),
        ("b1", 503, "busy"),
        (REQ.id, 201, None),
        (REQ.id, 409, "id_conflict"),
        ("c1", 503, "db_busy"),
    ]


def test_no_error_path_logs_the_message_or_exception_text(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    secret = "SENTINEL-7F3A"
    req = RequestInput(id="s1", message=f"hola {secret}")
    make_service(tmp_path / "a", FakeAdapter([make_call()])).create(req)
    with pytest.raises(IdConflict):
        make_service(tmp_path / "a", FakeAdapter([])).create(RequestInput(id="s1", message=secret))
    with pytest.raises(ClassificationCrash):
        make_service(tmp_path / "b", FakeAdapter([RuntimeError(secret)])).create(req)
    gate = GateAdapter([make_call()])
    busy = make_service(tmp_path / "c", gate, concurrency=1, queue_wait_s=0.05)
    worker, _ = start(busy.create, RequestInput(id="g1", message="x"))
    assert gate.entered.acquire(timeout=5)
    with pytest.raises(Busy):
        busy.create(req)
    gate.release.set()
    worker.join()
    clock = FakeClock()
    late_gate = GateAdapter([make_call()])
    late, outcome = start(make_service(tmp_path / "d", late_gate, clock=clock).create, req)
    assert late_gate.entered.acquire(timeout=5)
    clock.advance(531)
    make_service(tmp_path / "d", FakeAdapter([make_call()]), clock=clock).create(req)
    late_gate.release.set()
    late.join()
    assert outcome["result"][1] is False
    monkeypatch.setattr(repository.Repository, "complete", locked)
    with pytest.raises(DbBusy):
        make_service(tmp_path / "e", FakeAdapter([make_call()])).create(req)
    text = json.dumps(
        [[r.getMessage(), getattr(r, "fields", None), r.exc_info] for r in caplog.records],
        default=str,
    )
    assert "request_claim_lost" in text and "request_complete_failed" in text
    assert secret not in text
```

- [ ] **Step 4: Run to verify they fail**

Run: `uv run pytest tests/test_idempotency.py tests/test_lost_claims.py tests/test_service_failures.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pitz_pulse.service'` (collection errors).

- [ ] **Step 5: Write `intake.py`**

`src/pitz_pulse/intake.py`:
```python
"""Idempotent intake (Spec 02 §6): reserve, classify outside any transaction, complete or fail.

A row is left `pending` only when the database stays locked (DbBusy); it is then re-claimed
after the stale window. Every other failure path writes `failed` or answers from the row.
"""

import hashlib
import logging
import math
import time
import uuid
from datetime import datetime, timedelta

from pitz_pulse import db
from pitz_pulse.classifier import ClassificationCrash, ClassificationError, ClassifyOutcome
from pitz_pulse.errors import (
    Busy,
    ClassificationFailed,
    DbBusy,
    DomainError,
    IdConflict,
    InProgress,
)
from pitz_pulse.logs import log_event
from pitz_pulse.repository import StoredRequest
from pitz_pulse.schema import RequestInput

logger = logging.getLogger("pitz_pulse.service")
Answer = tuple[StoredRequest, bool, int]  # row, this call classified it, model attempts


class IntakeMixin:
    """Mixed into TriageService, which provides the connection, clock, slots and settings."""

    def create(self, req: RequestInput) -> tuple[StoredRequest, bool]:
        start = time.monotonic()
        status, row, attempts, error = 500, None, 0, None
        try:
            row, created, attempts = self._create(req)
            status = 201 if created else 200
            return row, created
        except DomainError as exc:
            status, attempts, error = exc.http_status, exc.attempts, exc.code
            raise
        except ClassificationCrash as exc:
            attempts, error = len(exc.attempts), "internal_error"
            raise
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            log_event(
                logger,
                "request_outcome",
                id=req.id,
                http_status=status,
                status=row.status if row else None,
                error=row.error if row else error,
                attempts=attempts,
                latency_ms=round((time.monotonic() - start) * 1000, 1),
            )

    def _create(self, req: RequestInput) -> Answer:
        message_hash = hashlib.sha256(req.message.encode("utf-8")).hexdigest()
        token = uuid.uuid4().hex
        with self.connection() as repo:
            with repo.transaction():
                row = repo.get(req.id)
                now = self.clock()
                if row is None:
                    repo.insert_pending(req, message_hash, token, db.format_ts(now))
                elif row.message_hash != message_hash:
                    raise IdConflict()
                elif row.status == "classified":
                    return row, False, 0
                elif row.status == "pending" and not self._is_stale(row, now):
                    raise InProgress(self._retry_after(row, now))
                else:
                    repo.reclaim(req.id, token, db.format_ts(now))
                stored = repo.get(req.id)
        # Retries classify what was stored first (same id + message; stored source_area).
        request = RequestInput(id=stored.id, message=stored.message, source_area=stored.source_area)
        return self._classify(request, token)

    def _is_stale(self, row: StoredRequest, now: datetime) -> bool:
        cutoff = db.format_ts(now - timedelta(seconds=self.pending_stale_s))
        return row.updated_at < cutoff

    def _retry_after(self, row: StoredRequest, now: datetime) -> int:
        age = (now - db.parse_ts(row.updated_at)).total_seconds()
        return max(1, math.ceil(self.pending_stale_s - age))

    def _classify(self, req: RequestInput, token: str) -> Answer:
        if not self._acquire_slot():
            answer = self._fail(req.id, token, "busy", attempts=0)
            if answer is not None:
                return answer
            raise Busy()
        attempts = 0
        try:
            try:
                outcome = self.classifier.classify(req)
            finally:
                self._release_slot()
            attempts = len(outcome.attempts)
            return self._complete(req.id, token, outcome)
        except ClassificationError as exc:
            attempts = len(exc.attempts)
            answer = self._fail(req.id, token, exc.kind, attempts)
            if answer is not None:
                return answer
            failed = ClassificationFailed(exc.kind)
            failed.attempts = attempts
            raise failed from None
        except DomainError:
            raise  # DbBusy from complete, or the current row's answer after a lost claim
        except Exception as exc:  # a crash in classify or an unexpected error in complete
            if isinstance(exc, ClassificationCrash):
                attempts = len(exc.attempts)
            answer = self._best_effort_fail(req.id, token, attempts)
            if answer is not None:
                return answer
            raise

    def _complete(self, request_id: str, token: str, outcome: ClassifyOutcome) -> Answer:
        attempts = len(outcome.attempts)
        adapter = self.classifier.adapter
        for delay in (*self.complete_backoff_s, None):
            try:
                with self.connection() as repo:
                    with repo.transaction():
                        done = repo.complete(
                            request_id,
                            token,
                            outcome.classification,
                            adapter.provider,
                            adapter.model,
                            self._now(),
                        )
                        row = repo.get(request_id)
                break
            except DbBusy as exc:
                if delay is None:
                    log_event(logger, "request_complete_failed", id=request_id, exc_type="DbBusy")
                    exc.attempts = attempts
                    raise
                time.sleep(delay)
        if done:
            return row, True, attempts
        return self._answer_lost_claim(row, attempts)

    def _record_failure(
        self, request_id: str, token: str, kind: str, attempts: int
    ) -> Answer | None:
        """None if the failure was recorded; else the current row answers (lost claim)."""
        with self.connection() as repo:
            with repo.transaction():
                done = repo.fail(request_id, token, kind, self._now())
                row = repo.get(request_id)
        if done:
            return None
        return self._answer_lost_claim(row, attempts)

    def _fail(self, request_id: str, token: str, kind: str, attempts: int) -> Answer | None:
        """Classification failure or busy: a locked database surfaces as DbBusy (row pending)."""
        try:
            return self._record_failure(request_id, token, kind, attempts)
        except DbBusy as exc:
            log_event(logger, "request_fail_write_failed", id=request_id, exc_type="DbBusy")
            exc.attempts = attempts
            raise

    def _best_effort_fail(self, request_id: str, token: str, attempts: int) -> Answer | None:
        """After an unexpected error: mark the row internal_error; the error itself is reported.

        A lost claim still answers from the current row (InProgress/ClassificationFailed raise).
        """
        try:
            return self._record_failure(request_id, token, "internal_error", attempts)
        except (InProgress, ClassificationFailed):
            raise
        except Exception as exc:  # DbBusy or any other write error: log it, report the original
            log_event(
                logger, "request_fail_write_failed", id=request_id, exc_type=type(exc).__name__
            )
            return None

    def _answer_lost_claim(self, row: StoredRequest, attempts: int) -> Answer:
        log_event(logger, "request_claim_lost", id=row.id, status=row.status)
        if row.status == "classified":
            return row, False, attempts
        if row.status == "pending":
            error: DomainError = InProgress(self._retry_after(row, self.clock()))
        else:
            error = ClassificationFailed(row.error or "internal_error")
        error.attempts = attempts
        raise error
```

Error-path rules (Spec 02 §6, plan-gate ruling):
- `ClassificationError` or no slot → `_fail` writes `failed` with the kind (or `busy`); a lost claim answers from the current row; a locked write logs `request_fail_write_failed` and raises `DbBusy` with the attempts (row stays `pending` until stale — the same accepted residual as a locked `complete`).
- Any other exception from `classify` (a `ClassificationCrash`) or from `_complete` (anything but `DbBusy` and lost-claim answers) → `_best_effort_fail` writes `internal_error`; if the claim was lost, the current row answers (200/409/502) instead of a 500; if the write itself fails it logs `request_fail_write_failed` with the class name and the original error is re-raised.
- There is no bare `except … pass` in the module.

- [ ] **Step 6: Write `service.py`**

`src/pitz_pulse/service.py`:
```python
"""TriageService: connections, model slots and reads (Spec 02 §2, §4); intake lives in intake.py."""

import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pitz_pulse import db
from pitz_pulse.classifier import Classifier
from pitz_pulse.errors import DbBusy, NotFound
from pitz_pulse.intake import IntakeMixin
from pitz_pulse.repository import ListFilters, Repository, StoredRequest
from pitz_pulse.review import needs_review
from pitz_pulse.settings_api import QUEUE_WAIT_S


def utc_now() -> datetime:
    return datetime.now(UTC)


class TriageService(IntakeMixin):
    def __init__(
        self,
        db_path: Path,
        classifier: Classifier,
        *,
        threshold: float,
        pending_stale_s: int,
        queue_wait_s: float = QUEUE_WAIT_S,
        clock: Callable[[], datetime] = utc_now,
        complete_backoff_s: tuple[float, ...] = (0.2, 0.4, 0.8),
    ):
        self.db_path = db_path
        self.classifier = classifier
        self.threshold = threshold
        self.pending_stale_s = pending_stale_s
        self.queue_wait_s = queue_wait_s
        self.clock = clock
        self.complete_backoff_s = complete_backoff_s
        concurrency = classifier.settings.concurrency
        self._slots = threading.BoundedSemaphore(concurrency)
        self._max_waiters = 2 * concurrency
        self._waiters = 0
        self._waiters_lock = threading.Lock()

    # -- connections -------------------------------------------------------------------

    @contextmanager
    def connection(self) -> Iterator[Repository]:
        conn = db.connect(self.db_path)
        try:
            yield Repository(conn)
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc):
                raise DbBusy() from None
            raise
        finally:
            conn.close()

    def _now(self) -> str:
        return db.format_ts(self.clock())

    # -- model slots -------------------------------------------------------------------

    def _acquire_slot(self) -> bool:
        """Take a model slot; False when the queue is full or the wait times out (busy)."""
        with self._waiters_lock:
            # The fast path only when nobody queues: new arrivals never overtake waiters.
            if self._waiters == 0 and self._slots.acquire(blocking=False):
                return True
            if self._waiters >= self._max_waiters:
                return False
            self._waiters += 1
        try:
            return self._slots.acquire(timeout=self.queue_wait_s)
        finally:
            with self._waiters_lock:
                self._waiters -= 1

    def _release_slot(self) -> None:
        self._slots.release()

    # -- reads -------------------------------------------------------------------------

    def get(self, request_id: str) -> tuple[StoredRequest, list[dict[str, Any]]]:
        with self.connection() as repo:
            row = repo.get(request_id)
            if row is None:
                raise NotFound()
            return row, repo.list_corrections(request_id)

    def list_requests(
        self, filters: ListFilters, limit: int, offset: int
    ) -> tuple[list[StoredRequest], int]:
        with self.connection() as repo:
            return repo.list_requests(filters, self.threshold, limit, offset)

    def needs_review(self, row: StoredRequest) -> bool:
        return (
            row.status == "classified"
            and not row.reviewed
            and needs_review(row.classification.confianza, self.threshold)
        )
```

The fast path in `_acquire_slot` is taken only when nobody waits, so a new arrival cannot overtake a queued request that way. (CPython's `Semaphore` does not hand a released slot to a specific waiter, so ordering among threads that are already waiting is not strictly FIFO; the bound on concurrent calls is exact.)

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_idempotency.py tests/test_lost_claims.py tests/test_service_failures.py -q`
Expected: PASS (14 + 5 + 12 tests). Then run them 10 times to check for flakiness:
```bash
for i in $(seq 1 10); do uv run pytest tests/test_idempotency.py tests/test_lost_claims.py tests/test_service_failures.py -q || break; done
```
Expected: ten green runs.

- [ ] **Step 8: Full suite, lint, line counts, commit**

Run: `uv run pytest && uv run ruff format . && uv run ruff check --fix . && wc -l src/pitz_pulse/service.py src/pitz_pulse/intake.py`
Expected: `486 passed`; ruff reports nothing to change; both files well under 300 lines.
```bash
git add src/pitz_pulse/service.py src/pitz_pulse/intake.py tests/fakes.py tests/service_support.py tests/test_idempotency.py tests/test_lost_claims.py tests/test_service_failures.py
git commit -F - <<'EOF'
feat: add TriageService with idempotent intake and bounded model calls

BEGIN IMMEDIATE reservation with message hash and claim token; one app-wide
semaphore bounds paid calls (busy after the queue wait, no overtaking of
waiters); a lost claim answers with the current row, never 201; complete is
retried on a locked database; only a locked database leaves a row pending.
One request_outcome event per create, never with message text.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 6: Corrections (PATCH semantics)

**Files:**
- Create: `src/pitz_pulse/corrections.py`
- Modify: `src/pitz_pulse/service.py` (add `correct`)
- Test: `tests/test_corrections.py`

**Interfaces:**
- Consumes: `Repository.transaction/get/insert_correction/update_fields`, `errors.NotFound/NotClassified/ContractViolation/field_errors`, `schema.Classification`, `TriageService.connection()`.
- Produces:
  - `corrections.CORRECTABLE_FIELDS` (tuple of the 7 names)
  - `corrections.apply_correction(repo, request_id, changes: dict, author: str, reason: str | None, now: str) -> StoredRequest`
  - `TriageService.correct(request_id, changes: dict, author: str, reason: str | None) -> StoredRequest`
  - `changes` holds JSON-mode values of correctable fields only (the HTTP layer builds it from `PatchBody.model_dump(exclude_unset=True, mode="json")`).

- [ ] **Step 1: Write the failing tests**

`tests/test_corrections.py`:
```python
import threading

import pytest
from fakes import FakeAdapter, make_call
from service_support import make_service, unavailable

from pitz_pulse import corrections
from pitz_pulse.errors import ClassificationFailed, ContractViolation, NotClassified, NotFound
from pitz_pulse.repository import Repository
from pitz_pulse.schema import RequestInput

REQ = RequestInput(id="MSG-01", message="Error 500 al subir catálogo")


@pytest.fixture
def service(tmp_path):
    svc = make_service(tmp_path, FakeAdapter([make_call()]))  # requiere_info True, confianza .86
    svc.create(REQ)
    return svc


def test_patching_one_field_on_a_bool_row_works_and_history_is_typed(service):
    row = service.correct(REQ.id, {"categoria": "datos"}, "ana", None)
    assert row.classification.categoria == "datos"
    detail, history = service.get(REQ.id)
    assert history[0]["previous_values"] == {"categoria": "bug"}
    assert history[0]["new_values"] == {"categoria": "datos"}


def test_original_stays_immutable_after_two_patches(service):
    service.correct(REQ.id, {"categoria": "datos"}, "ana", None)
    service.correct(REQ.id, {"prioridad": "baja"}, "luis", "menor impacto")
    row, history = service.get(REQ.id)
    assert row.original_classification["categoria"] == "bug"
    assert row.original_classification["prioridad"] == "alta"
    assert [h["author"] for h in history] == ["ana", "luis"]
    assert row.corrected is True


def test_empty_diff_is_a_confirmation(service):
    row = service.correct(REQ.id, {"categoria": "bug"}, "ana", None)
    assert (row.reviewed, row.corrected) == (True, False)
    assert service.get(REQ.id)[1][0]["new_values"] == {}


def test_confirmation_keeps_an_earlier_correction_flag(service):
    service.correct(REQ.id, {"categoria": "datos"}, "ana", None)
    row = service.correct(REQ.id, {}, "luis", None)
    assert row.corrected is True


def test_turning_requiere_info_off_needs_an_explicit_null_question(service):
    with pytest.raises(ContractViolation) as info:
        service.correct(REQ.id, {"requiere_info": False}, "ana", None)
    assert info.value.fields[0]["loc"] == ["body"]
    row = service.correct(
        REQ.id, {"requiere_info": False, "pregunta_seguimiento": None}, "ana", None
    )
    assert row.classification.requiere_info is False
    assert service.get(REQ.id)[1][0]["new_values"] == {
        "requiere_info": False,
        "pregunta_seguimiento": None,
    }


def test_history_json_stores_booleans_not_integers(service):
    service.correct(REQ.id, {"requiere_info": False, "pregunta_seguimiento": None}, "ana", None)
    with service.connection() as repo:
        previous, new = repo.conn.execute(
            "SELECT previous_values, new_values FROM corrections"
        ).fetchone()
    assert '"requiere_info": true' in previous and '"requiere_info": false' in new


def test_contract_rule_violation_is_rolled_back(service):
    too_long = " ".join(["palabra"] * 25)
    with pytest.raises(ContractViolation):
        service.correct(REQ.id, {"resumen": too_long}, "ana", None)
    row, history = service.get(REQ.id)
    assert history == [] and row.reviewed is False


def test_unknown_id_is_not_found(service):
    with pytest.raises(NotFound):
        service.correct("nope", {"categoria": "datos"}, "ana", None)


def test_failed_rows_cannot_be_corrected(tmp_path):
    svc = make_service(tmp_path, FakeAdapter([unavailable()]))
    with pytest.raises(ClassificationFailed):
        svc.create(REQ)
    with pytest.raises(NotClassified):
        svc.correct(REQ.id, {"categoria": "datos"}, "ana", None)


def test_pending_rows_cannot_be_corrected(service):
    with service.connection() as repo, repo.transaction():
        repo.insert_pending(RequestInput(id="p1", message="x"), "h", "tok", service._now())
    with pytest.raises(NotClassified):
        service.correct("p1", {"categoria": "datos"}, "ana", None)


def test_non_correctable_fields_are_rejected(service):
    with pytest.raises(ValueError, match="not correctable"):
        service.correct(REQ.id, {"confianza": 0.1}, "ana", None)


def test_concurrent_patches_serialize(service, monkeypatch):
    first_inside, release, second_at_begin = (threading.Event() for _ in range(3))
    validate = corrections.Classification.model_validate
    begin = Repository.transaction

    def gated_validate(data, *args, **kwargs):
        if not first_inside.is_set():
            first_inside.set()
            assert release.wait(timeout=5)
        return validate(data, *args, **kwargs)

    def watched_begin(self):
        if first_inside.is_set():
            second_at_begin.set()
        return begin(self)

    monkeypatch.setattr(corrections.Classification, "model_validate", gated_validate)
    monkeypatch.setattr(Repository, "transaction", watched_begin)

    def patch(value):
        service.correct(REQ.id, {"categoria": value}, "ana", None)

    first = threading.Thread(target=patch, args=("datos",))
    first.start()
    assert first_inside.wait(timeout=5)  # the first PATCH is inside its transaction
    second = threading.Thread(target=patch, args=("acceso",))
    second.start()
    assert second_at_begin.wait(timeout=5)  # the second one is about to BEGIN
    release.set()
    first.join()
    second.join()
    older, newer = service.get(REQ.id)[1]
    assert older["new_values"] == {"categoria": "datos"}
    assert newer["previous_values"] == {"categoria": "datos"}
```

`test_concurrent_patches_serialize` is deterministic: the first PATCH is held inside its transaction (in `Classification.model_validate`), the second is released only once it has called `transaction()`; a read before `BEGIN IMMEDIATE` would see the first PATCH's old values.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_corrections.py -q`
Expected: FAIL — `ImportError: cannot import name 'corrections' from 'pitz_pulse'`.

- [ ] **Step 3: Write `corrections.py`**

`src/pitz_pulse/corrections.py`:
```python
"""PATCH semantics (Spec 02 §7): merge, revalidate against the contract, record the diff."""

from typing import Any

from pydantic import ValidationError

from pitz_pulse.errors import ContractViolation, NotClassified, NotFound, field_errors
from pitz_pulse.repository import Repository, StoredRequest
from pitz_pulse.schema import Classification

CORRECTABLE_FIELDS = (
    "categoria",
    "prioridad",
    "area_sugerida",
    "idioma",
    "resumen",
    "requiere_info",
    "pregunta_seguimiento",
)


def apply_correction(
    repo: Repository,
    request_id: str,
    changes: dict[str, Any],
    author: str,
    reason: str | None,
    now: str,
) -> StoredRequest:
    unknown = set(changes) - set(CORRECTABLE_FIELDS)
    if unknown:
        raise ValueError(f"not correctable: {sorted(unknown)}")
    with repo.transaction():
        row = repo.get(request_id)
        if row is None:
            raise NotFound()
        if row.status != "classified":
            raise NotClassified()
        current = row.classification.model_dump(mode="json")
        try:
            merged = Classification.model_validate({**current, **changes})
        except ValidationError as exc:
            raise ContractViolation(field_errors(exc)) from None
        new = merged.model_dump(mode="json")
        diff = [name for name in CORRECTABLE_FIELDS if new[name] != current[name]]
        repo.insert_correction(
            request_id,
            {name: current[name] for name in diff},
            {name: new[name] for name in diff},
            author,
            reason,
            now,
        )
        repo.update_fields(
            request_id,
            {name: new[name] for name in diff},
            corrected=row.corrected or bool(diff),
            now=now,
        )
        return repo.get(request_id)
```

Note: a cross-field rule error (from the model validator) has an empty pydantic `loc`, so `field_errors` yields `["body"]` — that is the contract in Spec 02 §4.

- [ ] **Step 4: Add `correct` to `TriageService`**

In `src/pitz_pulse/service.py`, add this import on the line right after `from pitz_pulse.classifier import Classifier`:
```python
from pitz_pulse.corrections import apply_correction
```
and append to the end of the file (after one blank line, inside the class):
```python
    # -- corrections -------------------------------------------------------------------

    def correct(
        self, request_id: str, changes: dict[str, Any], author: str, reason: str | None
    ) -> StoredRequest:
        with self.connection() as repo:
            return apply_correction(repo, request_id, changes, author, reason, self._now())
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_corrections.py -q`
Expected: PASS (12 tests). `wc -l src/pitz_pulse/service.py` → 115.

- [ ] **Step 6: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff format . && uv run ruff check --fix .`
Expected: `498 passed`; ruff reports nothing to change.
```bash
git add src/pitz_pulse/corrections.py src/pitz_pulse/service.py tests/test_corrections.py
git commit -F - <<'EOF'
feat: add corrections that keep the original classification

PATCH reads the row inside BEGIN IMMEDIATE, merges into typed current values,
revalidates against the contract and records only the changed fields (typed
JSON). An empty diff is a reviewer's confirmation; the original
classification never changes; pending and failed rows are not correctable.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 7: HTTP models and error handling

**Files:**
- Create: `src/pitz_pulse/api_models.py`, `src/pitz_pulse/http_errors.py`
- Test: `tests/test_api_models.py`

**Interfaces:**
- Consumes: `schema` enums, `CONTRACT_FIELDS`, `ensure_utf8`; `StoredRequest`, `ListFilters`; `errors.DomainError`.
- Produces:
  - `api_models.PatchBody` (`extra="forbid"`, strict scalars, non-nullable fields reject explicit `null`, `author` stripped 1–100, `reason` stripped ≤ 500 or `None`); `.changes() -> dict` (JSON-mode correctable fields present in the body)
  - `api_models.ListQuery` (`extra="forbid"`): `categoria, prioridad, area_sugerida, status, needs_review, limit (1–100, 20), offset (0–2³¹−1, 0)`; `.filters() -> ListFilters`
  - `api_models.Item`, `ItemDetail`, `Page`, `CorrectionOut`, `FieldError`, `ErrorBody` (`error`, `detail`, `fields?`, `kind?`); `Item.from_stored(row, needs_review: bool)`, `ItemDetail.from_stored(row, needs_review, corrections)`
  - `http_errors.install(app: FastAPI) -> None`; `http_errors.error_response(status, code, detail, headers=None, **extra) -> JSONResponse`

- [ ] **Step 1: Write the failing tests**

`tests/test_api_models.py`:
```python
import pytest
from pydantic import ValidationError

from pitz_pulse.api_models import ErrorBody, Item, ListQuery, PatchBody
from pitz_pulse.repository import ListFilters


def test_patch_changes_only_include_fields_present_in_the_body():
    body = PatchBody.model_validate({"categoria": "datos", "author": " ana "})
    assert body.changes() == {"categoria": "datos"}
    assert body.author == "ana"


def test_patch_explicit_null_is_allowed_only_for_the_question():
    body = PatchBody.model_validate({"pregunta_seguimiento": None, "author": "ana"})
    assert body.changes() == {"pregunta_seguimiento": None}
    with pytest.raises(ValidationError) as info:
        PatchBody.model_validate({"categoria": None, "author": "ana"})
    assert info.value.errors()[0]["loc"] == ("categoria",)


@pytest.mark.parametrize(
    "payload",
    [
        {"requiere_info": "yes", "author": "ana"},
        {"confianza": 0.1, "author": "ana"},
        {"id": "x", "author": "ana"},
        {"categoria": "datos", "author": "   "},
        {"categoria": "datos"},
        {"categoria": "datos", "author": "a" * 101},
        {"categoria": "datos", "author": "ana", "reason": "r" * 501},
        {"resumen": "hola \ud800", "author": "ana"},
    ],
)
def test_patch_rejects_invalid_bodies(payload):
    with pytest.raises(ValidationError):
        PatchBody.model_validate(payload)


def test_patch_blank_reason_becomes_null():
    assert PatchBody.model_validate({"author": "ana", "reason": "  "}).reason is None


def test_list_query_forbids_unknown_params_and_bounds_paging():
    assert ListQuery.model_validate({}).filters() == ListFilters()
    with pytest.raises(ValidationError):
        ListQuery.model_validate({"area": "backend"})
    with pytest.raises(ValidationError):
        ListQuery.model_validate({"offset": 2**31})
    with pytest.raises(ValidationError):
        ListQuery.model_validate({"limit": 0})


def test_item_keys_are_the_documented_ones():
    assert set(Item.model_fields) == {
        "id",
        "message",
        "source_area",
        "status",
        "categoria",
        "prioridad",
        "area_sugerida",
        "idioma",
        "resumen",
        "requiere_info",
        "pregunta_seguimiento",
        "confianza",
        "version_prompt",
        "provider",
        "model",
        "needs_review",
        "corrected",
        "error",
        "created_at",
        "updated_at",
    }


def test_error_body_documents_the_error_shape():
    assert set(ErrorBody.model_fields) == {"error", "detail", "fields", "kind"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_api_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pitz_pulse.api_models'`.

- [ ] **Step 3: Write `api_models.py`**

`src/pitz_pulse/api_models.py`:
```python
"""HTTP models. Enums are typed so OpenAPI carries their values; bodies forbid extra fields."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator

from pitz_pulse.repository import ListFilters, StoredRequest
from pitz_pulse.schema import CONTRACT_FIELDS, Area, Categoria, Idioma, Prioridad, ensure_utf8

Status = Literal["pending", "classified", "failed"]
_NOT_NULLABLE = ("categoria", "prioridad", "area_sugerida", "idioma", "resumen", "requiere_info")
_CORRECTABLE = (*_NOT_NULLABLE, "pregunta_seguimiento")


class PatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categoria: Categoria | None = None
    prioridad: Prioridad | None = None
    area_sugerida: Area | None = None
    idioma: Idioma | None = None
    resumen: StrictStr | None = None
    requiere_info: StrictBool | None = None
    pregunta_seguimiento: StrictStr | None = None
    author: StrictStr
    reason: StrictStr | None = None

    @field_validator(*_NOT_NULLABLE)
    @classmethod
    def _not_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("cannot be null; omit the field to keep its value")
        return value

    @field_validator("resumen", "pregunta_seguimiento")
    @classmethod
    def _utf8(cls, value: str | None) -> str | None:
        return ensure_utf8(value) if value is not None else None

    @field_validator("author")
    @classmethod
    def _author(cls, value: str) -> str:
        value = ensure_utf8(value).strip()
        if not 1 <= len(value) <= 100:
            raise ValueError("must have 1-100 characters after strip")
        return value

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = ensure_utf8(value).strip()
        if len(value) > 500:
            raise ValueError("must have at most 500 characters")
        return value or None

    def changes(self) -> dict[str, Any]:
        present = self.model_dump(mode="json", exclude_unset=True)
        return {name: present[name] for name in _CORRECTABLE if name in present}


class ListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categoria: Categoria | None = None
    prioridad: Prioridad | None = None
    area_sugerida: Area | None = None
    status: Status | None = None
    needs_review: bool | None = None
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0, le=2**31 - 1)

    def filters(self) -> ListFilters:
        return ListFilters(
            categoria=self.categoria,
            prioridad=self.prioridad,
            area_sugerida=self.area_sugerida,
            status=self.status,
            needs_review=self.needs_review,
        )


class Item(BaseModel):
    id: str
    message: str
    source_area: str | None
    status: Status
    categoria: Categoria | None
    prioridad: Prioridad | None
    area_sugerida: Area | None
    idioma: Idioma | None
    resumen: str | None
    requiere_info: bool | None
    pregunta_seguimiento: str | None
    confianza: float | None
    version_prompt: str | None
    provider: str | None
    model: str | None
    needs_review: bool
    corrected: bool
    error: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_stored(cls, row: StoredRequest, needs_review: bool) -> "Item":
        values = row.classification.model_dump() if row.classification else {}
        return cls(
            id=row.id,
            message=row.message,
            source_area=row.source_area,
            status=row.status,
            **{name: values.get(name) for name in CONTRACT_FIELDS[1:]},
            provider=row.provider,
            model=row.model,
            needs_review=needs_review,
            corrected=row.corrected,
            error=row.error,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class CorrectionOut(BaseModel):
    previous_values: dict[str, Any]
    new_values: dict[str, Any]
    author: str
    reason: str | None
    created_at: str


class ItemDetail(Item):
    original_classification: dict[str, Any] | None
    corrections: list[CorrectionOut]

    @classmethod
    def from_stored(  # type: ignore[override]
        cls, row: StoredRequest, needs_review: bool, corrections: list[dict[str, Any]]
    ) -> "ItemDetail":
        item = Item.from_stored(row, needs_review)
        return cls(
            **item.model_dump(),
            original_classification=row.original_classification,
            corrections=[CorrectionOut(**c) for c in corrections],
        )


class FieldError(BaseModel):
    loc: list[str]
    msg: str


class ErrorBody(BaseModel):
    error: str
    detail: str
    fields: list[FieldError] | None = None
    kind: str | None = None


class Page(BaseModel):
    items: list[Item]
    total: int
    limit: int
    offset: int
```

- [ ] **Step 4: Write `http_errors.py`**

`src/pitz_pulse/http_errors.py`:
```python
"""One error body shape for every failure; unhandled exceptions never reach uvicorn (D9)."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from pitz_pulse.errors import DomainError

logger = logging.getLogger("pitz_pulse.http")
_HTTP_CODES = {401: "unauthorized", 404: "not_found", 405: "method_not_allowed"}


def error_response(
    status: int, code: str, detail: str, headers: dict[str, str] | None = None, **extra: Any
) -> JSONResponse:
    body = {"error": code, "detail": detail}
    body.update({key: value for key, value in extra.items() if value is not None})
    return JSONResponse(body, status_code=status, headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after_s)} if exc.retry_after_s else None
        return error_response(
            exc.http_status,
            exc.code,
            exc.detail,
            headers,
            fields=getattr(exc, "fields", None),
            kind=getattr(exc, "kind", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"loc": [str(p) for p in e["loc"]], "msg": e["msg"]} for e in exc.errors()]
        return error_response(422, "validation_error", "request validation failed", fields=fields)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return error_response(exc.status_code, code, str(exc.detail), exc.headers)

    @app.middleware("http")
    async def _catch_all(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            # Class name only: messages and chained causes may carry request or model text.
            logger.error(
                "unhandled_error",
                extra={"fields": {"exc_type": type(exc).__name__, "path": request.url.path}},
            )
            return error_response(500, "internal_error", "internal error")
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_api_models.py -q`
Expected: PASS (14 tests). (`http_errors` is exercised in Task 8.)

- [ ] **Step 6: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff format . && uv run ruff check --fix .`
Expected: `512 passed`; ruff reports nothing to change.
```bash
git add src/pitz_pulse/api_models.py src/pitz_pulse/http_errors.py tests/test_api_models.py
git commit -F - <<'EOF'
feat: add HTTP models and a single error body shape

Strict PATCH and list-query models reject unknown fields and explicit nulls
on contract fields; every error returns {error, detail} (ErrorBody documents
it); a catch-all middleware turns unhandled exceptions into 500s and logs
only the class name.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 8: `create_app` factory, routes, auth and mock marking

**Files:**
- Create: `src/pitz_pulse/api.py`, `tests/api_support.py`, `tests/test_api.py`, `tests/test_api_errors.py`, `tests/test_app_factory.py`

**Interfaces:**
- Consumes: everything above; `classifier.build_classifier`, `config.disable_tracing`, `logs.configure_logging`, `models_catalog.MOCK`, `schema.ID_PATTERN`, `schema.RequestInput`, `api_models.ErrorBody`.
- Produces: `api.create_app(settings: ApiSettings | None = None, adapter: ProviderAdapter | None = None) -> FastAPI`; `app.state.service` (the `TriageService`). OpenAPI: router-wide `401, 422, 500, 503` → `ErrorBody`; POST adds `409, 502`; GET `/{id}` adds `404`; PATCH adds `404, 409`.

- [ ] **Step 1: Write `tests/api_support.py`**

`tests/api_support.py`:
```python
from fastapi.testclient import TestClient

from pitz_pulse.api import create_app
from pitz_pulse.settings_api import parse_api_settings

KEY = "test-key"
HEADERS = {"X-API-Key": KEY}


def make_client(tmp_path, adapter, **env):
    settings = parse_api_settings(
        {"LLM_PROVIDER": "mock", "API_KEY": KEY, "DB_PATH": str(tmp_path / "api.db"), **env}
    )
    app = create_app(settings, adapter=adapter)
    return TestClient(app, raise_server_exceptions=False), app
```

- [ ] **Step 2: Write the failing tests**

`tests/test_api.py`:
```python
import threading

import pytest
from api_support import HEADERS, make_client
from fakes import VALID_OUTPUT, FakeAdapter, GateAdapter, make_call

from pitz_pulse.providers.base import LLMError

BODY = {"id": "MSG-01", "message": "Error 500 al subir catálogo"}
ITEM_KEYS = {
    "id", "message", "source_area", "status", "categoria", "prioridad", "area_sugerida",
    "idioma", "resumen", "requiere_info", "pregunta_seguimiento", "confianza",
    "version_prompt", "provider", "model", "needs_review", "corrected", "error",
    "created_at", "updated_at",
}  # fmt: skip


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path, FakeAdapter([make_call() for _ in range(5)]))[0]


def test_lowercase_api_key_header_is_accepted(client):
    assert client.get("/solicitudes", headers={"x-api-key": "test-key"}).status_code == 200


def test_open_endpoints_need_no_key(client):
    for path in ("/health", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 200


def test_health_reports_the_effective_adapter(client):
    assert client.get("/health").json() == {
        "status": "ok",
        "provider": "fake",
        "model": "fake-model",
        "prompt_version": "v1",
    }


def test_post_201_then_200_with_item_keys(client):
    first = client.post("/solicitudes", json=BODY, headers=HEADERS)
    second = client.post("/solicitudes", json=BODY, headers=HEADERS)
    assert (first.status_code, second.status_code) == (201, 200)
    item = first.json()
    assert set(item) == ITEM_KEYS
    assert item["categoria"] == "bug" and item["requiere_info"] is True
    assert item["provider"] == "fake" and item["needs_review"] is False


def test_in_progress_is_409_with_retry_after(tmp_path):
    gate = GateAdapter([make_call()])
    client, _ = make_client(tmp_path, gate)
    worker = threading.Thread(
        target=client.post, args=("/solicitudes",), kwargs={"json": BODY, "headers": HEADERS}
    )
    worker.start()
    assert gate.entered.acquire(timeout=5)
    response = client.post("/solicitudes", json=BODY, headers=HEADERS)
    assert (response.status_code, response.json()["error"]) == (409, "in_progress")
    assert int(response.headers["Retry-After"]) > 0
    gate.release.set()
    worker.join()


def test_list_filters_paging_and_unknown_params(client):
    for i in range(3):
        client.post("/solicitudes", json={"id": f"m{i}", "message": f"hola {i}"}, headers=HEADERS)
    page = client.get("/solicitudes?categoria=bug&limit=2", headers=HEADERS).json()
    assert (page["total"], len(page["items"]), page["limit"], page["offset"]) == (3, 2, 2, 0)
    assert client.get("/solicitudes?status=pending", headers=HEADERS).json()["total"] == 0
    assert client.get("/solicitudes?needs_review=false", headers=HEADERS).json()["total"] == 3
    for query in ("area=backend", "categoria=Bug", "limit=0", "limit=101", f"offset={2**31}"):
        response = client.get(f"/solicitudes?{query}", headers=HEADERS)
        assert (response.status_code, response.json()["error"]) == (422, "validation_error"), query
    unknown = client.get("/solicitudes?area=backend", headers=HEADERS).json()
    assert unknown["fields"][0]["loc"] == ["query", "area"]


def test_combined_filters_over_http(tmp_path):
    low_datos = make_call({**VALID_OUTPUT, "categoria": "datos", "confianza": 0.5})
    adapter = FakeAdapter([make_call(), low_datos, LLMError("unavailable", "APIConnectionError")])
    client, _ = make_client(tmp_path, adapter)
    for i in range(3):
        client.post("/solicitudes", json={"id": f"m{i}", "message": f"hola {i}"}, headers=HEADERS)

    def ids(query):
        page = client.get(f"/solicitudes?{query}", headers=HEADERS).json()
        return sorted(item["id"] for item in page["items"])

    assert ids("categoria=bug&status=classified&needs_review=false") == ["m0"]
    assert ids("categoria=datos&status=classified&needs_review=true") == ["m1"]
    assert ids("categoria=bug&needs_review=true") == []
    assert ids("status=failed&needs_review=false") == ["m2"]


def test_threshold_change_flips_needs_review_over_http(tmp_path):
    client, _ = make_client(tmp_path, FakeAdapter([make_call()]))  # confianza 0.86
    assert client.post("/solicitudes", json=BODY, headers=HEADERS).json()["needs_review"] is False
    strict, _ = make_client(tmp_path, FakeAdapter([]), CONFIDENCE_THRESHOLD="0.9")
    assert strict.get("/solicitudes/MSG-01", headers=HEADERS).json()["needs_review"] is True
    page = strict.get("/solicitudes?needs_review=true", headers=HEADERS).json()
    assert [item["id"] for item in page["items"]] == ["MSG-01"]


def test_get_detail_keys_404_and_bad_id(client):
    client.post("/solicitudes", json=BODY, headers=HEADERS)
    detail = client.get("/solicitudes/MSG-01", headers=HEADERS).json()
    assert set(detail) == ITEM_KEYS | {"original_classification", "corrections"}
    assert detail["original_classification"]["categoria"] == "bug"
    assert detail["corrections"] == []
    missing = client.get("/solicitudes/NOPE", headers=HEADERS)
    assert (missing.status_code, missing.json()["error"]) == (404, "not_found")
    assert client.get("/solicitudes/" + "a" * 65, headers=HEADERS).status_code == 422


def test_patch_flow_and_errors(client):
    client.post("/solicitudes", json=BODY, headers=HEADERS)
    ok = client.patch(
        "/solicitudes/MSG-01", json={"categoria": "datos", "author": "ana"}, headers=HEADERS
    )
    assert (ok.status_code, ok.json()["categoria"], ok.json()["corrected"]) == (200, "datos", True)
    detail = client.get("/solicitudes/MSG-01", headers=HEADERS).json()
    assert detail["original_classification"]["categoria"] == "bug"
    assert detail["corrections"][0]["new_values"] == {"categoria": "datos"}
    rule = client.patch(
        "/solicitudes/MSG-01", json={"requiere_info": False, "author": "ana"}, headers=HEADERS
    )
    assert rule.status_code == 422 and rule.json()["fields"][0]["loc"] == ["body"]
    missing = client.patch(
        "/solicitudes/NOPE", json={"categoria": "datos", "author": "a"}, headers=HEADERS
    )
    assert missing.status_code == 404
    frozen = client.patch(
        "/solicitudes/MSG-01", json={"confianza": 0.1, "author": "a"}, headers=HEADERS
    )
    assert frozen.status_code == 422


def test_stored_mock_rows_are_marked_even_when_running_a_real_provider(tmp_path):
    from pitz_pulse.providers.mock import MockAdapter

    mock_client, _ = make_client(tmp_path, MockAdapter())
    first = mock_client.post("/solicitudes", json=BODY, headers=HEADERS)
    assert first.headers["X-Pitz-Provider"] == "mock"
    real_client, _ = make_client(tmp_path, FakeAdapter([]))  # same DB, non-mock provider
    detail = real_client.get("/solicitudes/MSG-01", headers=HEADERS)
    assert detail.json()["provider"] == "mock"
    assert detail.headers["X-Pitz-Provider"] == "mock"
    assert "X-Pitz-Provider" not in real_client.get("/health").headers


def test_openapi_carries_enum_values_and_error_bodies(client):
    schema = client.get("/openapi.json").json()
    text = str(schema)
    for value in ("automatizacion", "digital_transformation", "pt", "baja"):
        assert value in text
    post = schema["paths"]["/solicitudes"]["post"]["responses"]
    for status in ("401", "409", "422", "500", "502", "503"):
        assert post[status]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/ErrorBody"
        ), status
    assert "404" in schema["paths"]["/solicitudes/{request_id}"]["patch"]["responses"]
```

`tests/test_api_errors.py`:
```python
import sqlite3
import threading

import pytest
from api_support import HEADERS, make_client
from fakes import FakeAdapter, GateAdapter, make_call

from pitz_pulse import repository
from pitz_pulse.providers.base import LLMError

BODY = {"id": "MSG-01", "message": "Error 500 al subir catálogo"}


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path, FakeAdapter([make_call() for _ in range(5)]))[0]


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-API-Key": "wrong"}, {"X-API-Key": "clé".encode()}, {"X-API-Key": b"\xff"}],
)
def test_missing_wrong_or_non_ascii_key_is_401(client, headers):
    response = client.get("/solicitudes", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_without_a_key_an_invalid_body_is_401_and_bad_json_is_422(client):
    invalid = client.post("/solicitudes", json={"id": "bad id"})
    assert (invalid.status_code, invalid.json()["error"]) == (401, "unauthorized")
    broken = client.post(
        "/solicitudes", content="{not json", headers={"Content-Type": "application/json"}
    )
    assert (broken.status_code, broken.json()["error"]) == (422, "validation_error")


def test_post_conflict_and_validation_codes(client):
    client.post("/solicitudes", json=BODY, headers=HEADERS)
    conflict = client.post("/solicitudes", json={**BODY, "message": "otro"}, headers=HEADERS)
    assert (conflict.status_code, conflict.json()["error"]) == (409, "id_conflict")
    invalid = client.post("/solicitudes", json={"id": "x", "message": ""}, headers=HEADERS)
    assert (invalid.status_code, invalid.json()["error"]) == (422, "validation_error")
    assert invalid.json()["fields"]
    extra = client.post("/solicitudes", json={**BODY, "id": "y", "urgent": True}, headers=HEADERS)
    assert extra.status_code == 422


def test_lone_surrogate_is_422_not_500(client):
    raw = '{"id": "s1", "message": "hola \\ud800"}'
    response = client.post(
        "/solicitudes", content=raw, headers={**HEADERS, "Content-Type": "application/json"}
    )
    assert response.status_code == 422


def test_classification_failure_is_502_with_kind(tmp_path):
    client, _ = make_client(tmp_path, FakeAdapter([LLMError("rejected", "APIStatusError:401")]))
    response = client.post("/solicitudes", json=BODY, headers=HEADERS)
    assert response.status_code == 502
    assert response.json() == {
        "error": "classification_failed",
        "detail": response.json()["detail"],
        "kind": "llm_rejected",
    }


def test_unexpected_error_is_500_with_a_constant_body(tmp_path):
    client, _ = make_client(tmp_path, FakeAdapter([RuntimeError("SENTINEL")]))
    response = client.post("/solicitudes", json=BODY, headers=HEADERS)
    assert response.status_code == 500
    assert response.json() == {"error": "internal_error", "detail": "internal error"}


def test_busy_is_503_with_retry_after(tmp_path):
    gate = GateAdapter([make_call(), make_call()])
    client, app = make_client(tmp_path, gate, LLM_CONCURRENCY="1")
    app.state.service.queue_wait_s = 0.1
    worker = threading.Thread(
        target=client.post,
        args=("/solicitudes",),
        kwargs={"json": {"id": "a", "message": "x"}, "headers": HEADERS},
    )
    worker.start()
    assert gate.entered.acquire(timeout=5)
    response = client.post("/solicitudes", json=BODY, headers=HEADERS)
    assert (response.status_code, response.json()["error"]) == (503, "busy")
    assert response.headers["Retry-After"] == "30"
    health = client.get("/health")  # async route: answers while the only slot is taken
    assert health.status_code == 200
    gate.release.set()
    worker.join()


def test_locked_database_is_503_db_busy_with_retry_after(client, monkeypatch):
    def locked(self, *args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repository.Repository, "get", locked)
    response = client.get("/solicitudes/MSG-01", headers=HEADERS)
    assert (response.status_code, response.json()["error"]) == (503, "db_busy")
    assert response.headers["Retry-After"] == "1"


def test_patch_on_failed_row_is_409(tmp_path):
    client, _ = make_client(tmp_path, FakeAdapter([LLMError("unavailable", "APIConnectionError")]))
    client.post("/solicitudes", json=BODY, headers=HEADERS)
    response = client.patch(
        "/solicitudes/MSG-01", json={"categoria": "datos", "author": "a"}, headers=HEADERS
    )
    assert (response.status_code, response.json()["error"]) == (409, "not_classified")


def test_unknown_route_and_method_use_the_error_body(client):
    missing = client.get("/nope")
    assert (missing.status_code, missing.json()["error"]) == (404, "not_found")
    method = client.delete("/solicitudes/MSG-01", headers=HEADERS)
    assert (method.status_code, method.json()["error"]) == (405, "method_not_allowed")
```

`tests/test_app_factory.py`:
```python
import json
import logging

import pytest
import uvicorn
from fakes import FakeAdapter
from fastapi.testclient import TestClient

from pitz_pulse.api import create_app
from pitz_pulse.config import ConfigError
from pitz_pulse.settings_api import parse_api_settings


def _env(monkeypatch, tmp_path, **extra):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "factory.db"))
    for name, value in extra.items():
        monkeypatch.setenv(name, value)


def test_uvicorn_can_load_the_zero_argument_factory(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    config = uvicorn.Config("pitz_pulse.api:create_app", factory=True)
    config.load()
    assert config.loaded_app is not None
    assert (tmp_path / "factory.db").exists()  # migrations ran


def test_missing_api_key_fails_startup_naming_it(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    monkeypatch.delenv("API_KEY")
    with pytest.raises(ConfigError, match="API_KEY"):
        create_app()


def test_default_adapter_in_mock_env_is_the_mock_and_marks_responses(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    client = TestClient(create_app())
    assert client.get("/health").json()["provider"] == "mock"
    assert client.get("/health").headers["X-Pitz-Provider"] == "mock"


def test_one_post_logs_one_llm_call_and_one_outcome_as_json(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    client = TestClient(create_app())
    capsys.readouterr()
    client.post(
        "/solicitudes",
        json={"id": "L1", "message": "SENTINEL-LOG-TEXT no puedo acceder"},
        headers={"X-API-Key": "test-key"},
    )
    lines = [
        json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")
    ]
    events = [line["event"] for line in lines]
    assert events.count("llm_call") == 1
    assert events.count("request_outcome") == 1
    assert "SENTINEL-LOG-TEXT" not in json.dumps(lines)


class MockNamedAdapter(FakeAdapter):
    provider = "mock"


def test_unhandled_errors_are_a_marked_500_and_never_log_exception_text(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    settings = parse_api_settings(
        {"LLM_PROVIDER": "mock", "API_KEY": "test-key", "DB_PATH": str(tmp_path / "e.db")}
    )
    adapter = MockNamedAdapter([ValueError("SENTINEL-EXC-TEXT")])
    client = TestClient(create_app(settings, adapter=adapter))  # re-raises unhandled errors
    response = client.post(
        "/solicitudes", json={"id": "E1", "message": "hola"}, headers={"X-API-Key": "test-key"}
    )
    assert response.status_code == 500
    assert response.json() == {"error": "internal_error", "detail": "internal error"}
    assert response.headers["X-Pitz-Provider"] == "mock"
    captured = " ".join(
        [r.getMessage() for r in caplog.records]
        + [str(getattr(r, "fields", "")) for r in caplog.records]
        + [str(r.exc_info) for r in caplog.records if r.exc_info]
    )
    assert "unhandled_error" in captured
    assert "SENTINEL-EXC-TEXT" not in captured
```

Notes on the tests:
- `test_one_post_logs…`: `configure_logging` attaches a `StreamHandler` to `sys.stderr` at call time; `capsys` replaces `sys.stderr` before the test body, so `create_app()` (which calls `configure_logging`) writes into the captured stream. If the handler was bound earlier, fix the factory (it must call `configure_logging` on every `create_app()` without settings), not the test.
- `test_unhandled_errors_are_a_marked_500…` uses the default `TestClient` (`raise_server_exceptions=True`): without the catch-all the error would reach the test; with the mock middleware installed inside the catch-all the 500 would lack `X-Pitz-Provider`.
- `test_without_a_key_an_invalid_body_is_401_and_bad_json_is_422` pins FastAPI 0.141's order: dependencies (auth) run before body *validation*, but a body that is not JSON fails while it is read, before auth (Spec 02 §4, rev 5.1).
- Replacing `hmac.compare_digest` with `==` is not caught by any test (constant-time comparison is not observable in a unit test) — accepted.

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/test_api.py tests/test_api_errors.py tests/test_app_factory.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pitz_pulse.api'`.

- [ ] **Step 4: Write `api.py`**

`src/pitz_pulse/api.py`:
```python
"""HTTP API (Spec 02 §4). `uvicorn --factory pitz_pulse.api:create_app` calls create_app()."""

import hmac
import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Response, Security
from fastapi.security import APIKeyHeader

from pitz_pulse import db, http_errors
from pitz_pulse.api_models import ErrorBody, Item, ItemDetail, ListQuery, Page, PatchBody
from pitz_pulse.classifier import build_classifier
from pitz_pulse.config import disable_tracing
from pitz_pulse.logs import configure_logging
from pitz_pulse.models_catalog import MOCK
from pitz_pulse.providers.base import ProviderAdapter
from pitz_pulse.repository import StoredRequest
from pitz_pulse.schema import ID_PATTERN, RequestInput
from pitz_pulse.service import TriageService
from pitz_pulse.settings_api import ApiSettings, parse_api_settings

MOCK_HEADER = "X-Pitz-Provider"
RequestId = Annotated[str, Path(pattern=ID_PATTERN)]


def _errors(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI entries for the error responses a route can return (all use ErrorBody)."""
    return {status: {"model": ErrorBody} for status in statuses}


def create_app(
    settings: ApiSettings | None = None, adapter: ProviderAdapter | None = None
) -> FastAPI:
    if settings is None:
        configure_logging("INFO")  # before parsing: the auto-selection log line must not be lost
        disable_tracing(os.environ)
        settings = parse_api_settings(os.environ)  # ConfigError aborts startup
        configure_logging(settings.llm.log_level)
    classifier = build_classifier(settings.llm, adapter)
    conn = db.connect(settings.db_path)
    try:
        db.migrate(conn)
    finally:
        conn.close()
    service = TriageService(
        settings.db_path,
        classifier,
        threshold=settings.llm.confidence_threshold,
        pending_stale_s=settings.pending_stale_s,
    )
    running_mock = classifier.adapter.provider == MOCK

    app = FastAPI(title="Pitz Pulse", version="0.1.0")
    app.state.service = service
    http_errors.install(app)

    @app.middleware("http")
    async def _mark_mock_mode(request, call_next):
        response = await call_next(request)
        if running_mock:
            response.headers[MOCK_HEADER] = "mock"
        return response

    @app.get("/health")
    async def health() -> dict[str, str]:  # async: no DB, answers even when the pool is busy
        return {
            "status": "ok",
            "provider": classifier.adapter.provider,
            "model": classifier.adapter.model,
            "prompt_version": classifier.prompt.version,
        }

    app.include_router(_router(service, settings.api_key))
    return app


def _mark_stored_mock_rows(response: Response, rows: list[StoredRequest]) -> None:
    if any(row.provider == MOCK for row in rows):
        response.headers[MOCK_HEADER] = "mock"


def _router(service: TriageService, api_key: str) -> APIRouter:
    header = APIKeyHeader(name="X-API-Key", auto_error=False)
    expected = api_key.encode()

    def require_api_key(provided: Annotated[str | None, Security(header)]) -> None:
        if provided is None or not hmac.compare_digest(provided.encode(), expected):
            raise HTTPException(status_code=401, detail="missing or invalid API key")

    router = APIRouter(
        prefix="/solicitudes",
        dependencies=[Depends(require_api_key)],
        responses=_errors(401, 422, 500, 503),
    )

    @router.post(
        "",
        status_code=201,
        response_model=Item,
        responses={200: {"model": Item}, **_errors(409, 502)},
    )
    def create(body: RequestInput, response: Response) -> Item:
        row, created = service.create(body)
        response.status_code = 201 if created else 200
        _mark_stored_mock_rows(response, [row])
        return Item.from_stored(row, service.needs_review(row))

    @router.get("", response_model=Page)
    def list_requests(query: Annotated[ListQuery, Query()], response: Response) -> Page:
        rows, total = service.list_requests(query.filters(), query.limit, query.offset)
        _mark_stored_mock_rows(response, rows)
        items = [Item.from_stored(row, service.needs_review(row)) for row in rows]
        return Page(items=items, total=total, limit=query.limit, offset=query.offset)

    @router.get("/{request_id}", response_model=ItemDetail, responses=_errors(404))
    def get_request(request_id: RequestId, response: Response) -> ItemDetail:
        row, corrections = service.get(request_id)
        _mark_stored_mock_rows(response, [row])
        return ItemDetail.from_stored(row, service.needs_review(row), corrections)

    @router.patch("/{request_id}", response_model=Item, responses=_errors(404, 409))
    def correct(request_id: RequestId, body: PatchBody, response: Response) -> Item:
        row = service.correct(request_id, body.changes(), body.author, body.reason)
        _mark_stored_mock_rows(response, [row])
        return Item.from_stored(row, service.needs_review(row))

    return router
```

Notes for the implementer:
- `APIKeyHeader` reads the header case-insensitively (Starlette headers), which is what `test_lowercase_api_key_header_is_accepted` pins.
- `provided.encode()` never fails: Starlette decodes header bytes as latin-1, so every character encodes; `hmac.compare_digest` on bytes accepts non-ASCII.
- A `ValueError` raised by `apply_correction` for non-correctable fields cannot happen through HTTP (`PatchBody` forbids them); if it ever does it becomes a 500 through the catch-all, which is correct for a programming error.
- The mock-mode middleware is added after `http_errors.install`, so it wraps the catch-all and even 500 responses carry the header in mock mode.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_api.py tests/test_api_errors.py tests/test_app_factory.py -q`
Expected: PASS (12 + 13 + 5 tests). Then check the threaded tests are stable:
```bash
for i in $(seq 1 10); do uv run pytest tests/test_api.py tests/test_api_errors.py tests/test_corrections.py -q || break; done
```
Expected: ten green runs.

- [ ] **Step 6: Full suite, lint, line counts, commit**

Run:
```bash
uv run pytest && uv run ruff format . && uv run ruff check --fix .
wc -l src/pitz_pulse/*.py tests/*.py | awk '$1>=300 && $2!="total"'
```
Expected: `542 passed`; ruff reports nothing to change; the `awk` line prints nothing.
```bash
git add src/pitz_pulse/api.py tests/api_support.py tests/test_api.py tests/test_api_errors.py tests/test_app_factory.py
git commit -F - <<'EOF'
feat: add the /solicitudes HTTP API with a zero-argument factory

create_app() loads settings from the environment so `uvicorn --factory`
works; the app owns one TriageService; /health is async and always answers;
X-API-Key is compared as bytes; error responses are documented with
ErrorBody; mock mode and stored mock rows are marked with X-Pitz-Provider.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 9: End-to-end verification in mock mode and docs status

**Files:**
- Modify: `docs/superpowers/specs/2026-09-25-02-service-persistence-design.md` (status line), `docs/MASTER.md` (§5 row 02)

**Interfaces:** none new.

- [ ] **Step 1: Run the server locally in mock mode and exercise the API (no network, no real model)**

Run from `apps/api/` in one shell:
```bash
E2E=$(mktemp -d)
PORT=$(uv run python -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1]); s.close()")
U=http://127.0.0.1:$PORT; H='X-API-Key: dev-local-key'; J='Content-Type: application/json'
LLM_PROVIDER=mock API_KEY=dev-local-key DB_PATH=$E2E/pitz.db \
  uv run uvicorn --factory pitz_pulse.api:create_app --host 127.0.0.1 --port "$PORT" \
  > "$E2E/access.log" 2> "$E2E/server.log" &
echo $! > "$E2E/server.pid"
for i in $(seq 1 60); do curl -sf "$U/health" > /dev/null && break; sleep 0.5; done
curl -sf "$U/health" > /dev/null && echo READY || echo NOT-READY
curl -s "$U/health" | uv run python -m json.tool
BODY='{"id":"E2E-1","message":"No puedo acceder al panel de pedidos"}'
curl -s -o "$E2E/post1.json" -w 'POST %{http_code}\n' -X POST "$U/solicitudes" -H "$H" -H "$J" -d "$BODY"
curl -s -o "$E2E/post2.json" -w 'POST %{http_code}\n' -X POST "$U/solicitudes" -H "$H" -H "$J" -d "$BODY"
uv run python -m json.tool "$E2E/post1.json"
curl -s -D "$E2E/patch.headers" -X PATCH "$U/solicitudes/E2E-1" -H "$H" -H "$J" \
  -d '{"prioridad":"alta","author":"reviewer"}' | uv run python -m json.tool
grep -i '^x-pitz-provider' "$E2E/patch.headers"
curl -s "$U/solicitudes/E2E-1" -H "$H" | uv run python -m json.tool
curl -s -o /dev/null -w 'GET without key %{http_code}\n' "$U/solicitudes"
echo "llm_call $(grep -c '"event": "llm_call"' "$E2E/server.log")"
echo "request_outcome $(grep -c '"event": "request_outcome"' "$E2E/server.log")"
echo "message text $(grep -c 'No puedo acceder' "$E2E/server.log")"
PID=$(cat "$E2E/server.pid"); kill "$PID"
for i in $(seq 1 20); do kill -0 "$PID" 2> /dev/null || break; sleep 0.5; done
kill -0 "$PID" 2> /dev/null && echo STILL-RUNNING || echo STOPPED
rm -rf "$E2E"
```
Expected, in order: `READY`; health JSON with `"provider": "mock"`; `POST 201`, `POST 200`; the POST body and the PATCH body as full JSON (PATCH shows `"prioridad": "alta"` and `"corrected": true`); `X-Pitz-Provider: mock`; the GET detail with `original_classification` and one entry in `corrections`; `GET without key 401`; `llm_call 1`, `request_outcome 2`, `message text 0`; `STOPPED`. Paste the real output into the task report.

- [ ] **Step 2: Update docs status**

- Spec 02 header: `- **Status:** implemented on feat/spec-02-service-persistence (rev 5.1)`.
- `docs/MASTER.md` §5 row 02 status: `implemented on feat/spec-02-service-persistence; implementation gate next`.

- [ ] **Step 3: Final checks and commit**

Run: `uv run pytest && uv run ruff format --check . && uv run ruff check .` (paste the tail lines).
Expected: `542 passed`; `… files already formatted`; `All checks passed!`.
```bash
git add docs/superpowers/specs/2026-09-25-02-service-persistence-design.md docs/MASTER.md
git commit -F - <<'EOF'
docs: mark Spec 02 implemented after the mock-mode end-to-end run

POST twice logs one llm_call and two request_outcome lines, PATCH keeps the
original, unauthenticated calls get 401 and no message text reaches logs.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

## Self-review (done while writing; v2 re-checked by verbatim execution)

- **Spec coverage:** §2 components → Tasks 2–8 (`db`, `repository`, `service` + `intake`, `errors`, `api`, `http_errors`, `api_models` incl. `ErrorBody`, `settings_api`, `review`, migration); factory flow and fail-fast → Task 8 + Task 4; concurrency bound, waiter cap and no-overtaking fast path → Task 5; `/health` async → Task 8; §3 schema and migration runner (incomplete tail, write lock at `BEGIN`) → Task 2; §4 auth order (401 before body validation, 422 for unparseable JSON), error table incl. the amended `kind` list, OpenAPI error bodies, filters, PATCH body, mock marking, path id, surrogates → Tasks 4, 7, 8; §6 POST flow incl. stale rule, lost claim (200/409/502, never 201), complete retry, crash/complete-error best-effort fail, locked fail → `DbBusy`, outcome log, stale default → Tasks 4, 5; §7 PATCH → Task 6; §9 edge list → Tasks 5–8; §10 test files → all present (`test_db`, `test_repository`, `test_idempotency` + `test_lost_claims` + `test_service_failures`, `test_corrections`, `test_api` + `test_api_errors`, `test_app_factory`, `test_review_flag`, `test_settings_api`); §11 acceptance → Task 9. The `deadline_s` docstring fix → Task 4. `make web-types` env belongs to Spec 04a/05 (Makefile does not exist yet).
- **Deviation noted:** the factory calls `configure_logging("INFO")` before parsing instead of reading `LOG_LEVEL` from the raw env; an invalid raw `LOG_LEVEL` would otherwise crash `logging` before `parse_llm_settings` can report it as a `ConfigError`. The level from settings is applied right after.
- **Type consistency:** `TriageService.create -> (StoredRequest, bool)`, `get -> (StoredRequest, list[dict])`, `list_requests -> (list, int)`, `correct(request_id, changes, author, reason)`; `Repository.list_requests(filters, threshold, limit, offset)`; `StoredRequest.classification: ClassificationShape | None` (read), `Repository.complete(..., result: Classification, ...)` (write); `DomainError.attempts/retry_after_s/code/http_status` used identically in `intake.py` and `http_errors.py`; no name shadows a builtin.
- **Mutants killed (verbatim v2 run):** list filters AND→OR · `prioridad` filter ignored · `<=` in the SQL and in the Python review rule · `complete` keeping `error` · `update_fields` not setting `updated_at` / forcing `corrected` · corrections newest first · `created_at ASC` · deferred `BEGIN` in `transaction()` and in `migrate()` · `fail` without the token guard · `;` appended to the tail · `executescript` · re-claim reusing the old token · retry using the new `source_area` · crash ignoring the lost-claim answer · no best-effort fail after a `complete` error · `_best_effort_fail` silently passing · locked `fail` not logged / attempts dropped · unfair slot fast path · no waiter cap · lost claim on failed → wrong kind · lost claim on pending → 200 · lost claim → 201 · outcome log 500 for domain errors · `ClassificationFailed` without attempts · outcome log leaking the crash cause · PATCH read before `BEGIN` · PATCH allowed on pending · history JSON 1/0 · catch-all removed · mock middleware inside the catch-all · `unhandled_error` leaking the cause · locked DB not mapped to `DbBusy` · `Retry-After` dropped · threshold fixed at 0.7 · API key not compared · key encoded as ASCII · reads validating full contract rules (41/41).
- **Accepted, not tested:** replacing `hmac.compare_digest` (timing is not observable in a unit test); repeated query params take the last value (FastAPI default); `/health` saturation is not reproducible with `TestClient` (async route by construction); ordering among threads already waiting for a slot is CPython's, not strict FIFO.
- **Placeholders:** none.
