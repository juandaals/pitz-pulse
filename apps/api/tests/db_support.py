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
