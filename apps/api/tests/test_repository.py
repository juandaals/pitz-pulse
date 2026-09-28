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


def classified(repo, req_id="r1", now=T1, message="hola", possible_duplicate_of=None, **overrides):
    pending(repo, req_id, token=f"tok-{req_id}", now=now, message=message)
    result = classification(req_id, **overrides)
    with repo.transaction():
        assert repo.complete(
            req_id,
            f"tok-{req_id}",
            result,
            "fake",
            "fake-model",
            now,
            possible_duplicate_of=possible_duplicate_of,
        )


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


def test_complete_stores_possible_duplicate_of(repo):
    classified(repo, "first", now=T0)
    classified(repo, "second", now=T1, possible_duplicate_of="first")
    assert repo.get("first").possible_duplicate_of is None
    assert repo.get("second").possible_duplicate_of == "first"


def test_complete_without_possible_duplicate_of_defaults_to_null(repo):
    classified(repo)
    assert repo.get("r1").possible_duplicate_of is None


def test_list_recent_classified_filters_idioma_and_excludes_current_id(repo):
    classified(repo, "a", now=T0, message="mensaje a", idioma="es")
    classified(repo, "b", now=T1, message="mensaje b", idioma="es")
    classified(repo, "c", now=T2, message="mensaje c", idioma="pt")
    pending(repo, "d", token="d", now=T0, message="mensaje pendiente")

    assert repo.list_recent_classified("es", "a", 200) == [("b", "mensaje b")]
    assert repo.list_recent_classified("pt", "z", 200) == [("c", "mensaje c")]


def test_list_recent_classified_orders_most_recent_first_and_respects_limit(repo):
    classified(repo, "a", now=T0, message="mensaje a")
    classified(repo, "b", now=T1, message="mensaje b")
    classified(repo, "c", now=T2, message="mensaje c")

    assert repo.list_recent_classified("es", "current", 200) == [
        ("c", "mensaje c"),
        ("b", "mensaje b"),
        ("a", "mensaje a"),
    ]
    assert repo.list_recent_classified("es", "current", 1) == [("c", "mensaje c")]


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
