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
