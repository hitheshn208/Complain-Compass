"""SQLite connection management and schema initialization."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id     TEXT PRIMARY KEY,
    country         TEXT,
    state           TEXT,
    age_group       TEXT,
    student_status  TEXT,
    income_bracket  TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_batches (
    batch_id        TEXT PRIMARY KEY,
    filename        TEXT NOT NULL,
    file_hash       TEXT NOT NULL UNIQUE,
    channel         TEXT NOT NULL DEFAULT 'CSV',
    total_rows      INTEGER NOT NULL DEFAULT 0 CHECK (total_rows >= 0),
    accepted_rows   INTEGER NOT NULL DEFAULT 0 CHECK (accepted_rows >= 0),
    rejected_rows   INTEGER NOT NULL DEFAULT 0 CHECK (rejected_rows >= 0),
    status          TEXT NOT NULL,
    stage           TEXT,
    progress        REAL NOT NULL DEFAULT 0,
    error_message   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Cross-channel identity directory. Raw contact values are never stored, only salted hashes.
CREATE TABLE IF NOT EXISTS customer_contacts (
    contact_type    TEXT NOT NULL CHECK (contact_type IN ('email','phone','social_handle')),
    contact_hash    TEXT NOT NULL,
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (contact_type, contact_hash)
);
CREATE INDEX IF NOT EXISTS idx_contacts_customer ON customer_contacts(customer_id);

CREATE TABLE IF NOT EXISTS batch_row_errors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        TEXT NOT NULL REFERENCES ingestion_batches(batch_id) ON DELETE CASCADE,
    row_number      INTEGER NOT NULL,
    field           TEXT,
    message         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_row_errors_batch ON batch_row_errors(batch_id);

CREATE TABLE IF NOT EXISTS complaints (
    complaint_id      TEXT PRIMARY KEY,
    customer_id       TEXT REFERENCES customers(customer_id) ON DELETE SET NULL,
    source            TEXT NOT NULL CHECK (source IN ('EMAIL','CHAT','VOICE','SOCIAL_MEDIA','CSV','OTHER')),
    original_text     TEXT NOT NULL,
    normalized_text   TEXT,
    pii_masked        INTEGER NOT NULL DEFAULT 0,
    reference_ids     TEXT NOT NULL DEFAULT '[]',
    timestamp         TEXT NOT NULL,
    timestamp_inferred INTEGER NOT NULL DEFAULT 0,  -- 1 = no timestamp in the source; upload time used
    batch_id          TEXT NOT NULL REFERENCES ingestion_batches(batch_id) ON DELETE CASCADE,
    source_row        INTEGER,
    created_at        TEXT NOT NULL,
    processing_status TEXT NOT NULL DEFAULT 'INGESTED',
    embedded          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_complaints_customer ON complaints(customer_id);
CREATE INDEX IF NOT EXISTS idx_complaints_batch ON complaints(batch_id);
CREATE INDEX IF NOT EXISTS idx_complaints_timestamp ON complaints(timestamp);
CREATE INDEX IF NOT EXISTS idx_complaints_source ON complaints(source);

CREATE TABLE IF NOT EXISTS issues (
    issue_id        TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT,
    category        TEXT,
    severity        TEXT CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
    priority_score  REAL NOT NULL DEFAULT 0,
    cluster_id      TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'OPEN',
    label_source    TEXT NOT NULL DEFAULT 'fallback',
    symptoms        TEXT NOT NULL DEFAULT '[]',
    root_cause_hypotheses TEXT NOT NULL DEFAULT '[]',
    supporting_complaint_ids TEXT NOT NULL DEFAULT '[]',
    representative_ids TEXT NOT NULL DEFAULT '[]',
    keywords        TEXT NOT NULL DEFAULT '[]',
    centroid        TEXT,
    label_signature TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issues_priority ON issues(priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status);

CREATE TABLE IF NOT EXISTS cluster_assignments (
    complaint_id          TEXT PRIMARY KEY REFERENCES complaints(complaint_id) ON DELETE CASCADE,
    cluster_id            TEXT REFERENCES issues(cluster_id) ON DELETE SET NULL,
    assignment_confidence REAL NOT NULL CHECK (assignment_confidence BETWEEN 0 AND 1),
    is_noise              INTEGER NOT NULL DEFAULT 0,
    assigned_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cluster_assign_cluster ON cluster_assignments(cluster_id);

CREATE TABLE IF NOT EXISTS complaint_classifications (
    complaint_id    TEXT PRIMARY KEY REFERENCES complaints(complaint_id) ON DELETE CASCADE,
    category        TEXT NOT NULL,
    severity        TEXT NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
    summary         TEXT,
    entities        TEXT NOT NULL DEFAULT '[]',
    confidence      REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence        TEXT,
    source          TEXT NOT NULL,
    classified_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_classif_severity ON complaint_classifications(severity);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id       TEXT PRIMARY KEY,
    customer_id       TEXT REFERENCES customers(customer_id) ON DELETE SET NULL,
    issue_id          TEXT REFERENCES issues(issue_id) ON DELETE SET NULL,
    first_reported_at TEXT NOT NULL,
    last_reported_at  TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'OPEN',
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_issue ON incidents(issue_id);
CREATE INDEX IF NOT EXISTS idx_incidents_customer ON incidents(customer_id);

CREATE TABLE IF NOT EXISTS complaint_incident_links (
    complaint_id      TEXT PRIMARY KEY REFERENCES complaints(complaint_id) ON DELETE CASCADE,
    incident_id       TEXT NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    link_type         TEXT NOT NULL CHECK (link_type IN ('CANONICAL','DUPLICATE')),
    match_confidence  REAL NOT NULL CHECK (match_confidence BETWEEN 0 AND 1),
    evidence          TEXT NOT NULL DEFAULT '{}',
    linked_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_links_incident ON complaint_incident_links(incident_id);

CREATE TABLE IF NOT EXISTS duplicate_review_candidates (
    complaint_id        TEXT NOT NULL REFERENCES complaints(complaint_id) ON DELETE CASCADE,
    candidate_incident  TEXT NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    confidence          REAL NOT NULL,
    evidence            TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    PRIMARY KEY (complaint_id, candidate_incident)
);

CREATE TABLE IF NOT EXISTS issue_metrics (
    issue_id                  TEXT PRIMARY KEY REFERENCES issues(issue_id) ON DELETE CASCADE,
    unique_affected_customers INTEGER NOT NULL,
    total_reports             INTEGER NOT NULL,
    total_incidents           INTEGER NOT NULL,
    severity_score            REAL NOT NULL,
    recurrence_score          REAL NOT NULL,
    growth_score              REAL NOT NULL,
    business_impact_score     REAL NOT NULL,
    impact_score              REAL NOT NULL,
    priority_score            REAL NOT NULL,
    explanation               TEXT NOT NULL,
    breakdown                 TEXT NOT NULL,
    calculated_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS segment_populations (
    dimension   TEXT NOT NULL CHECK (dimension IN ('country','state','age_group','student_status','income_bracket')),
    value       TEXT NOT NULL,
    population  INTEGER NOT NULL CHECK (population > 0),
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (dimension, value)
);

CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key   TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    model       TEXT NOT NULL,
    response    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


class Database:
    """Thin wrapper around a SQLite file. One connection per thread; FKs always on."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.write_lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialized write transaction; commits on success, rolls back on error."""
        with self.write_lock:
            conn = self.connect()
            try:
                conn.execute("BEGIN")
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def init_schema(self) -> None:
        conn = self.connect()
        conn.executescript(SCHEMA)
        self._migrate(conn)
        conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Additive migrations for databases created by earlier versions."""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(ingestion_batches)")}
        if "channel" not in cols:
            conn.execute("ALTER TABLE ingestion_batches ADD COLUMN channel TEXT NOT NULL DEFAULT 'CSV'")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(complaints)")}
        if "timestamp_inferred" not in cols:
            conn.execute("ALTER TABLE complaints ADD COLUMN timestamp_inferred INTEGER NOT NULL DEFAULT 0")

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
