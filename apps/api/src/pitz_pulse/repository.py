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
    possible_duplicate_of: str | None


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
        possible_duplicate_of=row["possible_duplicate_of"],
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

    @contextmanager
    def read_transaction(self) -> Iterator["Repository"]:
        """Deferred BEGIN … COMMIT so several reads see one snapshot; reuses an open one."""
        if self.conn.in_transaction:
            yield self
            return
        self.conn.execute("BEGIN")
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
        possible_duplicate_of: str | None = None,
    ) -> bool:
        values = result.model_dump(mode="json")
        columns = ", ".join(f"{field} = ?" for field in _VALUE_FIELDS)
        cursor = self.conn.execute(
            f"UPDATE requests SET status = 'classified', {columns}, provider = ?, model = ?,"
            " original_classification = ?, error = NULL, claim_token = NULL,"
            " possible_duplicate_of = ?, updated_at = ?"
            " WHERE id = ? AND status = 'pending' AND claim_token = ?",
            (
                *(values[field] for field in _VALUE_FIELDS),
                provider,
                model,
                json.dumps(values, ensure_ascii=False),
                possible_duplicate_of,
                now,
                request_id,
                token,
            ),
        )
        return cursor.rowcount == 1

    def list_recent_classified(
        self, idioma: str, exclude_id: str, limit: int
    ) -> list[tuple[str, str]]:
        """(id, message) pairs for the last `limit` classified rows in `idioma`, most recent first.

        Read-only; callers use a `read_transaction()` (or an already-open one) so this never
        takes the write lock (Spec 06c: run before `complete()`, which does the actual write).
        """
        rows = self.conn.execute(
            "SELECT id, message FROM requests WHERE status = 'classified' AND idioma = ?"
            " AND id != ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (idioma, exclude_id, limit),
        ).fetchall()
        return [(row["id"], row["message"]) for row in rows]

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
        with self.read_transaction():  # total and page from one snapshot
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
