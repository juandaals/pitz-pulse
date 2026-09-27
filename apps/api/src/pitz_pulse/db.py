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
