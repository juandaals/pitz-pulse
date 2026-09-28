"""Read pairs (row + corrections, total + items) come from one deferred read transaction."""

import pytest
from fakes import FakeAdapter, make_call
from service_support import make_service

from pitz_pulse import db
from pitz_pulse.errors import NotFound
from pitz_pulse.repository import ListFilters, Repository
from pitz_pulse.schema import RequestInput

REQ = RequestInput(id="MSG-01", message="Error 500 al subir catálogo")


def traced_connect(statements, opened):
    original = db.connect

    def connect(path):
        conn = original(path)
        conn.set_trace_callback(lambda sql: statements.append(sql.split()[0].upper()))
        opened.append(conn)
        return conn

    return connect


def reads_between_begin_and_commit(statements, reads):
    begin = statements.index("BEGIN")
    end = statements.index("COMMIT", begin)
    return statements[begin + 1 : end].count("SELECT") == reads


def test_get_reads_row_and_corrections_in_one_transaction(tmp_path, monkeypatch):
    service = make_service(tmp_path, FakeAdapter([make_call()]))
    service.create(REQ)
    statements, opened = [], []
    monkeypatch.setattr(db, "connect", traced_connect(statements, opened))
    row, history = service.get(REQ.id)
    assert (row.status, history) == ("classified", [])
    assert reads_between_begin_and_commit(statements, 2)


def test_get_of_an_unknown_id_rolls_back(tmp_path, monkeypatch):
    service = make_service(tmp_path, FakeAdapter([]))
    statements, opened = [], []
    monkeypatch.setattr(db, "connect", traced_connect(statements, opened))
    with pytest.raises(NotFound):
        service.get("nope")
    assert statements[-1] == "ROLLBACK"


def test_list_reads_total_and_rows_in_one_transaction(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.migrate(conn)
    statements: list[str] = []
    conn.set_trace_callback(lambda sql: statements.append(sql.split()[0].upper()))
    rows, total = Repository(conn).list_requests(ListFilters(), 0.7, 20, 0)
    assert (rows, total) == ([], 0)
    assert reads_between_begin_and_commit(statements, 2)
    assert conn.in_transaction is False
    conn.close()


def test_list_inside_an_open_transaction_reuses_it(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.migrate(conn)
    repo = Repository(conn)
    with repo.transaction():
        assert repo.list_requests(ListFilters(), 0.7, 20, 0) == ([], 0)
        assert conn.in_transaction is True
    assert conn.in_transaction is False
    conn.close()
