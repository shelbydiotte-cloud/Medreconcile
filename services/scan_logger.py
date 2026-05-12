"""
services/scan_logger.py

Persists every finalized reconciliation scan to a local SQLite database.
The database file lives at data/scan_history.db (auto-created on first run).

Schema
──────
scans
  id            INTEGER  PRIMARY KEY AUTOINCREMENT
  scan_id       TEXT     UNIQUE  — UUID assigned at scan time
  scanned_at    TEXT     — ISO-8601 UTC timestamp
  patient_name  TEXT
  patient_mrn   TEXT
  care_setting  TEXT
  bottles_count INTEGER
  threshold     INTEGER
  status        TEXT     — 'pending_review' | 'confirmed' | 'edited'
  confirmed_by  TEXT     — clinician name, set on confirm
  confirmed_at  TEXT     — ISO-8601 UTC timestamp, set on confirm

scan_medications
  id              INTEGER  PRIMARY KEY AUTOINCREMENT
  scan_id         TEXT     REFERENCES scans(scan_id)
  med_name        TEXT
  ehr_dose        TEXT
  scanned_dose    TEXT     — extracted by regex from OCR text
  match_status    TEXT     — 'confirmed' | 'needs_review' | 'discrepancy'
  confidence      INTEGER
  dose_alert      INTEGER  — 1 if ehr_dose != scanned_dose and both non-empty
  alert_priority  TEXT     — 'high' | 'medium' | null
  clinician_note  TEXT     — free-text note added during review
  finalized_name  TEXT     — clinician may override the med name
  finalized_dose  TEXT     — clinician may override the dose
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "scan_history.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS scans (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id       TEXT    UNIQUE NOT NULL,
                scanned_at    TEXT    NOT NULL,
                patient_name  TEXT,
                patient_mrn   TEXT,
                care_setting  TEXT,
                bottles_count INTEGER DEFAULT 1,
                threshold     INTEGER DEFAULT 80,
                status        TEXT    DEFAULT 'pending_review',
                confirmed_by  TEXT,
                confirmed_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS scan_medications (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id        TEXT    NOT NULL REFERENCES scans(scan_id),
                med_name       TEXT    NOT NULL,
                ehr_dose       TEXT,
                scanned_dose   TEXT,
                match_status   TEXT    NOT NULL,
                confidence     INTEGER,
                dose_alert     INTEGER DEFAULT 0,
                alert_priority TEXT,
                clinician_note TEXT,
                finalized_name TEXT,
                finalized_dose TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_scans_mrn ON scans(patient_mrn);
            CREATE INDEX IF NOT EXISTS idx_meds_scan ON scan_medications(scan_id);
        """)


# ── Write ─────────────────────────────────────────────────────────────────────

def log_scan(
    patient_name: str,
    patient_mrn: str,
    care_setting: str,
    bottles_count: int,
    threshold: int,
    medications: list[dict],   # enriched reconciliation items with dose_alert etc.
) -> str:
    """
    Insert a new scan record and its medication rows.
    Returns the new scan_id (UUID).
    """
    scan_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with _connect() as conn:
        conn.execute(
            """INSERT INTO scans
               (scan_id, scanned_at, patient_name, patient_mrn,
                care_setting, bottles_count, threshold, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_review')""",
            (scan_id, now, patient_name, patient_mrn,
             care_setting, bottles_count, threshold),
        )

        for m in medications:
            conn.execute(
                """INSERT INTO scan_medications
                   (scan_id, med_name, ehr_dose, scanned_dose,
                    match_status, confidence, dose_alert, alert_priority)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan_id,
                    m.get("med") or m.get("name", ""),
                    m.get("ehr_dose") or m.get("dose", ""),
                    m.get("scanned_dose", ""),
                    m.get("match_status", "discrepancy"),
                    m.get("confidence"),
                    1 if m.get("dose_alert") else 0,
                    m.get("alert_priority"),
                ),
            )

    return scan_id


def confirm_scan(
    scan_id: str,
    confirmed_by: str,
    edits: list[dict],          # [{med_id, finalized_name, finalized_dose, clinician_note}]
) -> bool:
    """
    Mark a scan as confirmed and apply any clinician edits to individual med rows.
    Returns True on success, False if scan_id not found.
    """
    now = datetime.now(timezone.utc).isoformat()
    status = "edited" if edits else "confirmed"

    with _connect() as conn:
        cur = conn.execute(
            "UPDATE scans SET status=?, confirmed_by=?, confirmed_at=? WHERE scan_id=?",
            (status, confirmed_by, now, scan_id),
        )
        if cur.rowcount == 0:
            return False

        for edit in edits:
            conn.execute(
                """UPDATE scan_medications
                   SET finalized_name=?, finalized_dose=?, clinician_note=?
                   WHERE id=? AND scan_id=?""",
                (
                    edit.get("finalized_name"),
                    edit.get("finalized_dose"),
                    edit.get("clinician_note"),
                    edit["med_id"],
                    scan_id,
                ),
            )

    return True


# ── Read ──────────────────────────────────────────────────────────────────────

def get_scan(scan_id: str) -> dict | None:
    """Fetch a single scan with its medication rows."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM scans WHERE scan_id=?", (scan_id,)
        ).fetchone()
        if not row:
            return None

        meds = conn.execute(
            "SELECT * FROM scan_medications WHERE scan_id=? ORDER BY id",
            (scan_id,),
        ).fetchall()

    return {**dict(row), "medications": [dict(m) for m in meds]}


def get_history(
    patient_mrn: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    Return scan summaries (no medication rows), newest first.
    Optionally filter by patient MRN.
    """
    with _connect() as conn:
        if patient_mrn:
            rows = conn.execute(
                """SELECT s.*, COUNT(sm.id) AS med_count,
                          SUM(sm.dose_alert) AS alert_count
                   FROM scans s
                   LEFT JOIN scan_medications sm ON s.scan_id = sm.scan_id
                   WHERE s.patient_mrn = ?
                   GROUP BY s.scan_id
                   ORDER BY s.scanned_at DESC
                   LIMIT ? OFFSET ?""",
                (patient_mrn, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT s.*, COUNT(sm.id) AS med_count,
                          SUM(sm.dose_alert) AS alert_count
                   FROM scans s
                   LEFT JOIN scan_medications sm ON s.scan_id = sm.scan_id
                   GROUP BY s.scan_id
                   ORDER BY s.scanned_at DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()

    return [dict(r) for r in rows]
