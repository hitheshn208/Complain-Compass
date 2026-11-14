"""Data-access layer. All SQL lives here and is parameterized."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import pandas as pd

from app.database.models import BatchStatus, ComplaintStatus, utcnow_iso
from app.database.sqlite import Database
from app.schemas.complaint import CustomerProfile, RowError, StandardComplaint

DEMOGRAPHIC_FIELDS = ("country", "state", "age_group", "student_status", "income_bracket")


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


def _rows(rs: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rs]


@dataclass
class ComplaintFilters:
    """Dashboard filters. All are optional and combine with AND."""

    date_from: str | None = None
    date_to: str | None = None
    country: list[str] | None = None
    state: list[str] | None = None
    age_group: list[str] | None = None
    student_status: list[str] | None = None
    income_bracket: list[str] | None = None
    source: list[str] | None = None
    severity: list[str] | None = None
    issue_id: str | None = None
    batch_id: str | None = None
    search: str | None = None

    def is_empty(self) -> bool:
        return all(v in (None, [], "") for v in self.__dict__.values())

    def to_sql(self) -> tuple[str, list[Any]]:
        """WHERE clause over aliases c (complaints), cu (customers),
        cl (complaint_classifications), ca (cluster_assignments), i (issues)."""
        clauses = ["c.processing_status != ?"]
        params: list[Any] = [ComplaintStatus.REJECTED_EMPTY.value]
        if self.date_from:
            clauses.append("c.timestamp >= ?")
            params.append(self.date_from)
        if self.date_to:
            # inclusive end date when only a date is given
            end = self.date_to + "T23:59:59+00:00" if len(self.date_to) == 10 else self.date_to
            clauses.append("c.timestamp <= ?")
            params.append(end)
        for field in DEMOGRAPHIC_FIELDS:
            values = getattr(self, field)
            if values:
                clauses.append(f"cu.{field} IN ({','.join('?' * len(values))})")
                params.extend(values)
        if self.source:
            clauses.append(f"c.source IN ({','.join('?' * len(self.source))})")
            params.extend(self.source)
        if self.severity:
            clauses.append(f"cl.severity IN ({','.join('?' * len(self.severity))})")
            params.extend(self.severity)
        if self.issue_id:
            clauses.append("i.issue_id = ?")
            params.append(self.issue_id)
        if self.batch_id:
            clauses.append("c.batch_id = ?")
            params.append(self.batch_id)
        if self.search:
            clauses.append("c.original_text LIKE ?")
            params.append(f"%{self.search}%")
        return " AND ".join(clauses), params


_COMPLAINT_JOINS = """
FROM complaints c
LEFT JOIN customers cu ON cu.customer_id = c.customer_id
LEFT JOIN complaint_classifications cl ON cl.complaint_id = c.complaint_id
LEFT JOIN cluster_assignments ca ON ca.complaint_id = c.complaint_id
LEFT JOIN issues i ON i.cluster_id = ca.cluster_id
LEFT JOIN complaint_incident_links l ON l.complaint_id = c.complaint_id
"""


class Repository:
    def __init__(self, db: Database):
        self.db = db

    @property
    def conn(self) -> sqlite3.Connection:
        return self.db.connect()

    # ------------------------------------------------------------------ batches
    def find_batch_by_hash(self, file_hash: str) -> dict[str, Any] | None:
        return _row(self.conn.execute("SELECT * FROM ingestion_batches WHERE file_hash = ?", (file_hash,)).fetchone())

    def delete_empty_batch(self, batch_id: str) -> None:
        """Remove a batch that has no complaints (every row was rejected), so the same file can be re-uploaded."""
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM complaints WHERE batch_id = ? LIMIT 1", (batch_id,)).fetchone():
                raise ValueError(f"Batch {batch_id} has complaints; refusing to delete")
            conn.execute("DELETE FROM batch_row_errors WHERE batch_id = ?", (batch_id,))
            conn.execute("DELETE FROM ingestion_batches WHERE batch_id = ?", (batch_id,))

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        return _row(self.conn.execute("SELECT * FROM ingestion_batches WHERE batch_id = ?", (batch_id,)).fetchone())

    def list_batches(self, limit: int = 50) -> list[dict[str, Any]]:
        return _rows(
            self.conn.execute("SELECT * FROM ingestion_batches ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        )

    def list_row_errors(self, batch_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        return _rows(
            self.conn.execute(
                "SELECT row_number, field, message FROM batch_row_errors WHERE batch_id = ? ORDER BY row_number, id LIMIT ?",
                (batch_id, limit),
            ).fetchall()
        )

    def create_batch_with_rows(
        self,
        batch_id: str,
        filename: str,
        file_hash: str,
        total_rows: int,
        complaints: Sequence[StandardComplaint],
        customers: Sequence[CustomerProfile],
        errors: Sequence[RowError],
        channel: str = "CSV",
    ) -> None:
        """Atomically persist a batch, its customers, valid complaints and row errors."""
        now = utcnow_iso()
        accepted = len(complaints)
        rejected = len({e.row_number for e in errors})
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO ingestion_batches
                   (batch_id, filename, file_hash, channel, total_rows, accepted_rows, rejected_rows, status, stage,
                    progress, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (batch_id, filename, file_hash, channel, total_rows, accepted, rejected, BatchStatus.UPLOADED.value,
                 "ingested", now, now),
            )
            self._upsert_customers(conn, customers, now)
            conn.executemany(
                """INSERT INTO complaints
                   (complaint_id, customer_id, source, original_text, timestamp, timestamp_inferred, batch_id,
                    source_row, created_at, processing_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        c.complaint_id,
                        c.customer_id,
                        c.source.value,
                        c.original_text,
                        c.timestamp.replace(microsecond=0).isoformat(),
                        int(c.timestamp_inferred),
                        batch_id,
                        c.source_row,
                        now,
                        ComplaintStatus.INGESTED.value,
                    )
                    for c in complaints
                ],
            )
            conn.executemany(
                "INSERT INTO batch_row_errors (batch_id, row_number, field, message) VALUES (?, ?, ?, ?)",
                [(batch_id, e.row_number, e.field, e.message) for e in errors],
            )

    @staticmethod
    def _upsert_customers(conn: sqlite3.Connection, customers: Sequence[CustomerProfile], now: str) -> None:
        # COALESCE(excluded.x, customers.x): incoming nulls never overwrite stored values.
        conn.executemany(
            """INSERT INTO customers (customer_id, country, state, age_group, student_status, income_bracket, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(customer_id) DO UPDATE SET
                 country = COALESCE(excluded.country, customers.country),
                 state = COALESCE(excluded.state, customers.state),
                 age_group = COALESCE(excluded.age_group, customers.age_group),
                 student_status = COALESCE(excluded.student_status, customers.student_status),
                 income_bracket = COALESCE(excluded.income_bracket, customers.income_bracket)""",
            [
                (p.customer_id, p.country, p.state, p.age_group, p.student_status, p.income_bracket, now)
                for p in customers
            ],
        )

    def upsert_customers(self, customers: Sequence[CustomerProfile]) -> None:
        with self.db.transaction() as conn:
            self._upsert_customers(conn, customers, utcnow_iso())

    # ------------------------------------------------------------------ identity directory
    def lookup_contacts(self, contact_type: str, hashes: Sequence[str]) -> dict[str, str]:
        """{contact_hash: customer_id} for hashes present in the directory."""
        if not hashes:
            return {}
        marks = ",".join("?" * len(hashes))
        rows = self.conn.execute(
            f"SELECT contact_hash, customer_id FROM customer_contacts WHERE contact_type = ? AND contact_hash IN ({marks})",
            (contact_type, *hashes),
        ).fetchall()
        return {r["contact_hash"]: r["customer_id"] for r in rows}

    def save_directory(
        self, customers: Sequence[CustomerProfile], contacts: Sequence[tuple[str, str, str]]
    ) -> None:
        """Upsert customer profiles and (contact_type, contact_hash, customer_id) rows."""
        now = utcnow_iso()
        with self.db.transaction() as conn:
            self._upsert_customers(conn, customers, now)
            conn.executemany(
                """INSERT INTO customer_contacts (contact_type, contact_hash, customer_id, created_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(contact_type, contact_hash) DO UPDATE SET customer_id = excluded.customer_id""",
                [(t, h, cid, now) for t, h, cid in contacts],
            )

    def relink_customers(self, mapping: dict[str, str]) -> int:
        """Move complaints/incidents from pseudonymous IDs to directory customer IDs.
        Returns the number of complaints relinked. Emptied pseudonymous customers are deleted."""
        moved = 0
        with self.db.transaction() as conn:
            for old, new in mapping.items():
                if old == new:
                    continue
                moved += conn.execute("UPDATE complaints SET customer_id = ? WHERE customer_id = ?", (new, old)).rowcount
                conn.execute("UPDATE incidents SET customer_id = ? WHERE customer_id = ?", (new, old))
                conn.execute(
                    "DELETE FROM customers WHERE customer_id = ? AND NOT EXISTS "
                    "(SELECT 1 FROM complaints WHERE customer_id = ?)", (old, old)
                )
        return moved

    def directory_stats(self) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT contact_type, COUNT(*) AS n, COUNT(DISTINCT customer_id) AS customers FROM customer_contacts "
            "GROUP BY contact_type"
        ).fetchall()
        total = self.conn.execute("SELECT COUNT(DISTINCT customer_id) FROM customer_contacts").fetchone()[0]
        return {"customers_with_contacts": total,
                "by_type": {r["contact_type"]: {"contacts": r["n"], "customers": r["customers"]} for r in rows}}

    def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        return _row(self.conn.execute("SELECT * FROM customers WHERE customer_id = ?", (customer_id,)).fetchone())

    def update_batch(
        self,
        batch_id: str,
        *,
        status: BatchStatus | None = None,
        stage: str | None = None,
        progress: float | None = None,
        error_message: str | None = None,
        clear_error: bool = False,
    ) -> None:
        sets, params = ["updated_at = ?"], [utcnow_iso()]
        if status is not None:
            sets.append("status = ?")
            params.append(status.value)
        if stage is not None:
            sets.append("stage = ?")
            params.append(stage)
        if progress is not None:
            sets.append("progress = ?")
            params.append(round(float(progress), 3))
        if error_message is not None or clear_error:
            sets.append("error_message = ?")
            params.append(error_message)
        params.append(batch_id)
        with self.db.transaction() as conn:
            conn.execute(f"UPDATE ingestion_batches SET {', '.join(sets)} WHERE batch_id = ?", params)

    def fail_interrupted_batches(self) -> int:
        """Called at startup: no pipeline is running yet, so any batch still UPLOADED/PROCESSING was cut off
        by a server stop. Mark it FAILED with a hint instead of leaving it 'processing' forever."""
        rows = self.conn.execute(
            "SELECT batch_id, stage, total_rows, accepted_rows FROM ingestion_batches WHERE status IN (?, ?)",
            (BatchStatus.UPLOADED.value, BatchStatus.PROCESSING.value),
        ).fetchall()
        for r in rows:
            if not r["accepted_rows"]:
                self.update_batch(r["batch_id"], status=BatchStatus.FAILED, error_message=(
                    f"No valid complaints: all {r['total_rows']} item(s) were rejected. "
                    "Check that the file was uploaded on the right tab."))
                continue
            where = f" during '{r['stage']}'" if r["stage"] else ""
            self.update_batch(
                r["batch_id"], status=BatchStatus.FAILED,
                error_message=f"Interrupted: the server stopped{where}. Re-run it with "
                              f"POST /api/v1/pipeline/process/{r['batch_id']} (cached LLM results are reused).",
            )
        return len(rows)

    # --------------------------------------------------------------- complaints
    def complaints_by_status(self, batch_id: str, status: ComplaintStatus) -> list[dict[str, Any]]:
        return _rows(
            self.conn.execute(
                "SELECT * FROM complaints WHERE batch_id = ? AND processing_status = ?", (batch_id, status.value)
            ).fetchall()
        )

    def save_preprocessed(self, rows: Sequence[tuple[str, str, bool, list[str], ComplaintStatus]]) -> None:
        """rows: (complaint_id, normalized_text, pii_masked, reference_ids, status)."""
        with self.db.transaction() as conn:
            conn.executemany(
                "UPDATE complaints SET normalized_text = ?, pii_masked = ?, reference_ids = ?, processing_status = ? "
                "WHERE complaint_id = ?",
                [(text, int(pii), json.dumps(refs), status.value, cid) for cid, text, pii, refs, status in rows],
            )

    def complaints_pending_embedding(self) -> list[dict[str, Any]]:
        return _rows(
            self.conn.execute(
                "SELECT complaint_id, customer_id, source, timestamp, batch_id, normalized_text FROM complaints "
                "WHERE embedded = 0 AND normalized_text IS NOT NULL AND processing_status != ?",
                (ComplaintStatus.REJECTED_EMPTY.value,),
            ).fetchall()
        )

    def mark_embedded(self, complaint_ids: Sequence[str]) -> None:
        with self.db.transaction() as conn:
            conn.executemany(
                "UPDATE complaints SET embedded = 1, processing_status = ? WHERE complaint_id = ?",
                [(ComplaintStatus.EMBEDDED.value, cid) for cid in complaint_ids],
            )

    def mark_batch_complaints_processed(self, batch_id: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE complaints SET processing_status = ? WHERE batch_id = ? AND embedded = 1",
                (ComplaintStatus.PROCESSED.value, batch_id),
            )

    def embedded_complaint_ids(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT complaint_id FROM complaints WHERE embedded = 1").fetchall()]

    def embedded_complaint_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM complaints WHERE embedded = 1").fetchone()[0])

    def complaint_categories(self) -> dict[str, str]:
        """complaint_id -> classified category (LLM or fallback)."""
        return {r[0]: r[1] for r in self.conn.execute(
            "SELECT complaint_id, category FROM complaint_classifications WHERE category IS NOT NULL").fetchall()}

    def get_complaints(self, complaint_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        ids = list(complaint_ids)
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            q = f"SELECT * FROM complaints WHERE complaint_id IN ({','.join('?' * len(chunk))})"
            for r in self.conn.execute(q, chunk).fetchall():
                out[r["complaint_id"]] = dict(r)
        return out

    def get_complaint_detail(self, complaint_id: str) -> dict[str, Any] | None:
        q = f"""SELECT c.*, cl.category, cl.severity, cl.summary, cl.entities, cl.confidence AS classification_confidence,
                       cl.evidence AS classification_evidence, cl.source AS classification_source,
                       i.issue_id, i.title AS issue_title, ca.assignment_confidence, ca.is_noise,
                       l.incident_id, l.link_type, l.match_confidence, l.evidence AS dedup_evidence
                {_COMPLAINT_JOINS} WHERE c.complaint_id = ?"""
        return _row(self.conn.execute(q, (complaint_id,)).fetchone())

    def list_complaints(self, filters: ComplaintFilters, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
        where, params = filters.to_sql()
        total = self.conn.execute(f"SELECT COUNT(*) {_COMPLAINT_JOINS} WHERE {where}", params).fetchone()[0]
        q = f"""SELECT c.complaint_id, c.customer_id, c.source, c.original_text, c.normalized_text, c.timestamp,
                       c.batch_id, c.processing_status, c.created_at, cl.category, cl.severity, cl.summary,
                       i.issue_id, i.title AS issue_title, l.incident_id, l.link_type,
                       (SELECT COUNT(*) FROM complaint_incident_links l2 WHERE l2.incident_id = l.incident_id)
                           AS incident_report_count
                {_COMPLAINT_JOINS} WHERE {where}
                ORDER BY c.timestamp DESC, c.complaint_id LIMIT ? OFFSET ?"""
        rows = _rows(self.conn.execute(q, [*params, page_size, (page - 1) * page_size]).fetchall())
        return rows, total

    def complaint_frame(self, filters: ComplaintFilters | None = None) -> pd.DataFrame:
        """Flat analytics frame: one row per (non-empty) complaint with all joined attributes."""
        filters = filters or ComplaintFilters()
        where, params = filters.to_sql()
        q = f"""SELECT c.complaint_id, c.customer_id, c.source, c.timestamp, c.timestamp_inferred, c.batch_id,
                       c.reference_ids,
                       c.normalized_text,
                       cu.country, cu.state, cu.age_group, cu.student_status, cu.income_bracket,
                       cl.category, cl.severity, cl.entities,
                       i.issue_id, i.title AS issue_title, i.category AS issue_category, i.status AS issue_status,
                       ca.is_noise, l.incident_id, l.link_type
                {_COMPLAINT_JOINS} WHERE {where}"""
        df = pd.read_sql_query(q, self.conn, params=params)
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601")
        return df

    def filter_options(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for field in DEMOGRAPHIC_FIELDS:
            out[field] = [
                r[0]
                for r in self.conn.execute(
                    f"SELECT DISTINCT {field} FROM customers WHERE {field} IS NOT NULL ORDER BY {field}"
                ).fetchall()
            ]
        out["source"] = [r[0] for r in self.conn.execute("SELECT DISTINCT source FROM complaints ORDER BY source").fetchall()]
        rng = self.conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM complaints").fetchone()
        out["date_range"] = [rng[0], rng[1]] if rng[0] else []
        return out

    # -------------------------------------------------------------- clustering
    def replace_cluster_assignments(self, rows: Sequence[tuple[str, str | None, float, bool]]) -> None:
        """rows: (complaint_id, cluster_id or None for noise, confidence, is_noise)."""
        now = utcnow_iso()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM cluster_assignments")
            conn.executemany(
                "INSERT INTO cluster_assignments (complaint_id, cluster_id, assignment_confidence, is_noise, assigned_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [(cid, cl, float(min(max(conf, 0.0), 1.0)), int(noise), now) for cid, cl, conf, noise in rows],
            )

    def cluster_members(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for r in self.conn.execute(
            "SELECT cluster_id, complaint_id FROM cluster_assignments WHERE cluster_id IS NOT NULL"
        ).fetchall():
            out.setdefault(r[0], []).append(r[1])
        return out

    def complaint_issue_map(self) -> dict[str, str]:
        return {
            r[0]: r[1]
            for r in self.conn.execute(
                "SELECT ca.complaint_id, i.issue_id FROM cluster_assignments ca JOIN issues i ON i.cluster_id = ca.cluster_id"
            ).fetchall()
        }

    # ------------------------------------------------------------------ issues
    def list_all_issues(self, include_stale: bool = True) -> list[dict[str, Any]]:
        q = "SELECT * FROM issues" + ("" if include_stale else " WHERE status != 'STALE'")
        return _rows(self.conn.execute(q).fetchall())

    def get_issue(self, issue_id: str) -> dict[str, Any] | None:
        return _row(self.conn.execute("SELECT * FROM issues WHERE issue_id = ?", (issue_id,)).fetchone())

    def upsert_issue(self, issue: dict[str, Any]) -> None:
        now = utcnow_iso()
        cols = [
            "issue_id", "title", "description", "category", "severity", "cluster_id", "status", "label_source",
            "symptoms", "root_cause_hypotheses", "supporting_complaint_ids", "representative_ids", "keywords",
            "centroid", "label_signature",
        ]
        values = [issue.get(c) for c in cols]
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "issue_id")
        with self.db.transaction() as conn:
            conn.execute(
                f"""INSERT INTO issues ({', '.join(cols)}, created_at, updated_at)
                    VALUES ({', '.join('?' * len(cols))}, ?, ?)
                    ON CONFLICT(issue_id) DO UPDATE SET {updates}, updated_at = excluded.updated_at""",
                [*values, now, now],
            )

    def mark_issues_stale(self, keep_issue_ids: Sequence[str]) -> None:
        keep = list(keep_issue_ids)
        with self.db.transaction() as conn:
            if keep:
                conn.execute(
                    f"UPDATE issues SET status = 'STALE', priority_score = 0, updated_at = ? "
                    f"WHERE issue_id NOT IN ({','.join('?' * len(keep))}) AND status != 'STALE'",
                    [utcnow_iso(), *keep],
                )
            else:
                conn.execute("UPDATE issues SET status = 'STALE', priority_score = 0, updated_at = ?", (utcnow_iso(),))

    def patch_issue(self, issue_id: str, fields: dict[str, Any]) -> None:
        allowed = {"title", "description", "category", "severity", "status"}
        sets = [f"{k} = ?" for k in fields if k in allowed]
        if not sets:
            return
        params = [v for k, v in fields.items() if k in allowed]
        with self.db.transaction() as conn:
            conn.execute(
                f"UPDATE issues SET {', '.join(sets)}, label_source = CASE WHEN ? THEN 'manual' ELSE label_source END, "
                f"updated_at = ? WHERE issue_id = ?",
                [*params, int(any(k in fields for k in ("title", "description", "category"))), utcnow_iso(), issue_id],
            )

    def update_issue_scores(self, rows: Sequence[tuple[str, float, str | None]]) -> None:
        """rows: (issue_id, priority_score, severity)."""
        with self.db.transaction() as conn:
            conn.executemany(
                "UPDATE issues SET priority_score = ?, severity = COALESCE(?, severity), updated_at = ? WHERE issue_id = ?",
                [(p, sev, utcnow_iso(), iid) for iid, p, sev in rows],
            )

    def replace_issue_metrics(self, rows: Sequence[dict[str, Any]]) -> None:
        now = utcnow_iso()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM issue_metrics")
            conn.executemany(
                """INSERT INTO issue_metrics (issue_id, unique_affected_customers, total_reports, total_incidents,
                     severity_score, recurrence_score, growth_score, business_impact_score, impact_score, priority_score,
                     explanation, breakdown, calculated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        r["issue_id"], r["unique_affected_customers"], r["total_reports"], r["total_incidents"],
                        r["severity_score"], r["recurrence_score"], r["growth_score"], r["business_impact_score"],
                        r["impact_score"], r["priority_score"], r["explanation"], json.dumps(r["breakdown"]), now,
                    )
                    for r in rows
                ],
            )

    def issues_with_metrics(self, include_stale: bool = False) -> list[dict[str, Any]]:
        q = """SELECT i.*, m.unique_affected_customers, m.total_reports, m.total_incidents, m.severity_score,
                      m.recurrence_score, m.growth_score, m.business_impact_score, m.impact_score, m.explanation,
                      m.breakdown, m.calculated_at
               FROM issues i LEFT JOIN issue_metrics m ON m.issue_id = i.issue_id"""
        if not include_stale:
            q += " WHERE i.status != 'STALE'"
        q += " ORDER BY i.priority_score DESC, i.title"
        return _rows(self.conn.execute(q).fetchall())

    def issue_with_metrics(self, issue_id: str) -> dict[str, Any] | None:
        q = """SELECT i.*, m.unique_affected_customers, m.total_reports, m.total_incidents, m.severity_score,
                      m.recurrence_score, m.growth_score, m.business_impact_score, m.impact_score, m.explanation,
                      m.breakdown, m.calculated_at
               FROM issues i LEFT JOIN issue_metrics m ON m.issue_id = i.issue_id WHERE i.issue_id = ?"""
        return _row(self.conn.execute(q, (issue_id,)).fetchone())

    # --------------------------------------------------------- classifications
    def unclassified_complaints(self) -> list[dict[str, Any]]:
        return _rows(
            self.conn.execute(
                """SELECT c.complaint_id, c.normalized_text FROM complaints c
                   LEFT JOIN complaint_classifications cl ON cl.complaint_id = c.complaint_id
                   WHERE c.embedded = 1 AND (cl.complaint_id IS NULL OR cl.source = 'fallback')"""
            ).fetchall()
        )

    def classified_by_source(self, source: str) -> set[str]:
        return {
            r[0]
            for r in self.conn.execute(
                "SELECT complaint_id FROM complaint_classifications WHERE source = ?", (source,)
            ).fetchall()
        }

    def save_classifications(self, rows: Sequence[dict[str, Any]]) -> None:
        now = utcnow_iso()
        with self.db.transaction() as conn:
            conn.executemany(
                """INSERT INTO complaint_classifications
                     (complaint_id, category, severity, summary, entities, confidence, evidence, source, classified_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(complaint_id) DO UPDATE SET category = excluded.category, severity = excluded.severity,
                     summary = excluded.summary, entities = excluded.entities, confidence = excluded.confidence,
                     evidence = excluded.evidence, source = excluded.source, classified_at = excluded.classified_at""",
                [
                    (
                        r["complaint_id"], r["category"], r["severity"], r.get("summary"), json.dumps(r.get("entities", [])),
                        float(min(max(r["confidence"], 0.0), 1.0)), r.get("evidence"), r["source"], now,
                    )
                    for r in rows
                ],
            )

    # -------------------------------------------------------------- incidents
    def dedup_input(self) -> list[dict[str, Any]]:
        q = """SELECT c.complaint_id, c.customer_id, c.source, c.timestamp, c.reference_ids, c.normalized_text,
                      i.issue_id, cl.category
               FROM complaints c
               LEFT JOIN cluster_assignments ca ON ca.complaint_id = c.complaint_id
               LEFT JOIN issues i ON i.cluster_id = ca.cluster_id
               LEFT JOIN complaint_classifications cl ON cl.complaint_id = c.complaint_id
               WHERE c.embedded = 1
               ORDER BY c.timestamp, c.complaint_id"""
        return _rows(self.conn.execute(q).fetchall())

    def replace_incidents(
        self,
        incidents: Sequence[dict[str, Any]],
        links: Sequence[dict[str, Any]],
        review: Sequence[dict[str, Any]],
    ) -> None:
        now = utcnow_iso()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM duplicate_review_candidates")
            conn.execute("DELETE FROM complaint_incident_links")
            conn.execute("DELETE FROM incidents")
            conn.executemany(
                """INSERT INTO incidents (incident_id, customer_id, issue_id, first_reported_at, last_reported_at,
                     status, created_at) VALUES (?, ?, ?, ?, ?, 'OPEN', ?)""",
                [
                    (x["incident_id"], x["customer_id"], x["issue_id"], x["first_reported_at"], x["last_reported_at"], now)
                    for x in incidents
                ],
            )
            conn.executemany(
                """INSERT INTO complaint_incident_links (complaint_id, incident_id, link_type, match_confidence, evidence,
                     linked_at) VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (x["complaint_id"], x["incident_id"], x["link_type"], x["confidence"], json.dumps(x["evidence"]), now)
                    for x in links
                ],
            )
            conn.executemany(
                """INSERT INTO duplicate_review_candidates (complaint_id, candidate_incident, confidence, evidence,
                     created_at) VALUES (?, ?, ?, ?, ?)""",
                [
                    (x["complaint_id"], x["incident_id"], x["confidence"], json.dumps(x["evidence"]), now)
                    for x in review
                ],
            )

    def review_candidates(self, limit: int = 200) -> list[dict[str, Any]]:
        return _rows(
            self.conn.execute(
                "SELECT * FROM duplicate_review_candidates ORDER BY confidence DESC LIMIT ?", (limit,)
            ).fetchall()
        )

    def incident_complaints(self, incident_id: str) -> list[dict[str, Any]]:
        return _rows(
            self.conn.execute(
                """SELECT c.complaint_id, c.source, c.timestamp, c.customer_id, c.normalized_text, c.original_text,
                          b.channel AS upload_channel, b.filename AS upload_filename,
                          l.link_type, l.match_confidence, l.evidence
                   FROM complaint_incident_links l JOIN complaints c ON c.complaint_id = l.complaint_id
                   LEFT JOIN ingestion_batches b ON b.batch_id = c.batch_id
                   WHERE l.incident_id = ? ORDER BY c.timestamp""",
                (incident_id,),
            ).fetchall()
        )

    # ------------------------------------------------------------- LLM cache
    def cache_get(self, key: str) -> str | None:
        r = self.conn.execute("SELECT response FROM llm_cache WHERE cache_key = ?", (key,)).fetchone()
        return r[0] if r else None

    def cache_put(self, key: str, kind: str, model: str, response: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache (cache_key, kind, model, response, created_at) VALUES (?, ?, ?, ?, ?)",
                (key, kind, model, response, utcnow_iso()),
            )

    # -------------------------------------------------------------- app settings
    def get_setting(self, key: str) -> Any | None:
        row = self.conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_setting(self, key: str, value: Any) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, json.dumps(value), utcnow_iso()),
            )

    def delete_setting(self, key: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))

    # ----------------------------------------------------------- populations
    def replace_populations(self, rows: Sequence[tuple[str, str, int]]) -> None:
        now = utcnow_iso()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM segment_populations")
            conn.executemany(
                "INSERT INTO segment_populations (dimension, value, population, updated_at) VALUES (?, ?, ?, ?)",
                [(d, v, int(p), now) for d, v, p in rows],
            )

    def populations(self, dimension: str) -> dict[str, int]:
        return {
            r[0]: r[1]
            for r in self.conn.execute(
                "SELECT value, population FROM segment_populations WHERE dimension = ?", (dimension,)
            ).fetchall()
        }

    # ------------------------------------------------------------- counters
    def counts(self) -> dict[str, int]:
        c = self.conn
        return {
            "complaints": c.execute("SELECT COUNT(*) FROM complaints").fetchone()[0],
            "customers": c.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
            "issues": c.execute("SELECT COUNT(*) FROM issues WHERE status != 'STALE'").fetchone()[0],
            "incidents": c.execute("SELECT COUNT(*) FROM incidents").fetchone()[0],
        }
