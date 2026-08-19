"""SQLite persistence for a hiring workspace.

One file holds every posting, candidate, submission and finding, so results
accumulate across runs instead of being recomputed each time. Screening a
résumé is deterministic for a given file, so a submission is keyed by content
hash and re-screened only when the file or the posting's JD changes.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

DEFAULT_DB = "vetta.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id          INTEGER PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    jd_text     TEXT NOT NULL,
    jd_hash     TEXT NOT NULL,
    location    TEXT DEFAULT '',
    status      TEXT DEFAULT 'open',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    id          INTEGER PRIMARY KEY,
    name        TEXT DEFAULT '',
    email       TEXT DEFAULT '',
    phone       TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE (email, name)
);

CREATE TABLE IF NOT EXISTS submissions (
    id            INTEGER PRIMARY KEY,
    posting_id    INTEGER NOT NULL REFERENCES postings(id) ON DELETE CASCADE,
    candidate_id  INTEGER REFERENCES candidates(id),
    path          TEXT NOT NULL,
    filename      TEXT NOT NULL,
    file_hash     TEXT NOT NULL,
    jd_hash       TEXT NOT NULL,
    kind          TEXT DEFAULT '',
    pages         INTEGER DEFAULT 0,
    match_score   INTEGER DEFAULT 0,
    band          TEXT DEFAULT '',
    verdict       TEXT DEFAULT '',
    hidden_ratio  REAL DEFAULT 0,
    fingerprint   TEXT DEFAULT '',
    matched_terms TEXT DEFAULT '[]',
    missing_terms TEXT DEFAULT '[]',
    hidden_text   TEXT DEFAULT '',
    error         TEXT DEFAULT '',
    screened_at   TEXT NOT NULL,
    UNIQUE (posting_id, file_hash)
);

CREATE TABLE IF NOT EXISTS findings (
    id             INTEGER PRIMARY KEY,
    submission_id  INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    code           TEXT NOT NULL,
    severity       TEXT NOT NULL,
    title          TEXT NOT NULL,
    evidence       TEXT DEFAULT '',
    location       TEXT DEFAULT '',
    detail         TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_sub_posting  ON submissions(posting_id);
CREATE INDEX IF NOT EXISTS idx_sub_fp       ON submissions(fingerprint);
CREATE INDEX IF NOT EXISTS idx_find_sub     ON findings(submission_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:32]


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:32]


class Store:
    """Thin wrapper over sqlite3. Rows come back as dicts."""

    def __init__(self, path: str = DEFAULT_DB):
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- postings ------------------------------------------------------------
    def add_posting(self, code: str, title: str, jd_text: str,
                    location: str = "") -> int:
        cur = self.db.execute(
            "INSERT INTO postings (code, title, jd_text, jd_hash, location, created_at)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(code) DO UPDATE SET title=excluded.title,"
            " jd_text=excluded.jd_text, jd_hash=excluded.jd_hash,"
            " location=excluded.location",
            (code, title, jd_text, text_hash(jd_text), location, now()))
        self.db.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = self.db.execute("SELECT id FROM postings WHERE code=?", (code,)).fetchone()
        return row["id"]

    def get_posting(self, code: str) -> dict | None:
        r = self.db.execute("SELECT * FROM postings WHERE code=?", (code,)).fetchone()
        return dict(r) if r else None

    def postings(self, status: str | None = None) -> list[dict]:
        q = "SELECT * FROM postings"
        args: tuple = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        q += " ORDER BY created_at, code"
        return [dict(r) for r in self.db.execute(q, args)]

    def set_posting_status(self, code: str, status: str) -> None:
        self.db.execute("UPDATE postings SET status=? WHERE code=?", (status, code))
        self.db.commit()

    # --- candidates ----------------------------------------------------------
    def upsert_candidate(self, name: str, email: str, phone: str) -> int | None:
        if not (name or email):
            return None
        self.db.execute(
            "INSERT INTO candidates (name, email, phone, created_at) VALUES (?,?,?,?)"
            " ON CONFLICT(email, name) DO UPDATE SET"
            " phone = CASE WHEN excluded.phone <> '' THEN excluded.phone"
            "              ELSE candidates.phone END",
            (name, email, phone, now()))
        self.db.commit()
        r = self.db.execute("SELECT id FROM candidates WHERE email=? AND name=?",
                            (email, name)).fetchone()
        return r["id"] if r else None

    # --- submissions ---------------------------------------------------------
    def needs_screening(self, posting_id: int, file_hash: str, jd_hash: str) -> bool:
        r = self.db.execute(
            "SELECT jd_hash FROM submissions WHERE posting_id=? AND file_hash=?",
            (posting_id, file_hash)).fetchone()
        return r is None or r["jd_hash"] != jd_hash

    def save_submission(self, posting_id: int, jd_hash: str, path: str,
                        file_hash: str, result, candidate_id: int | None) -> int:
        m = result.match
        cur = self.db.execute(
            "INSERT INTO submissions (posting_id, candidate_id, path, filename,"
            " file_hash, jd_hash, kind, pages, match_score, band, verdict,"
            " hidden_ratio, fingerprint, matched_terms, missing_terms, hidden_text,"
            " error, screened_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(posting_id, file_hash) DO UPDATE SET"
            " jd_hash=excluded.jd_hash, match_score=excluded.match_score,"
            " band=excluded.band, verdict=excluded.verdict,"
            " hidden_ratio=excluded.hidden_ratio,"
            " matched_terms=excluded.matched_terms,"
            " missing_terms=excluded.missing_terms,"
            " hidden_text=excluded.hidden_text, error=excluded.error,"
            " screened_at=excluded.screened_at",
            (posting_id, candidate_id, os.path.abspath(path),
             os.path.basename(path), file_hash, jd_hash, result.kind, result.pages,
             m.score, m.band, result.verdict, result.hidden_ratio, result.fingerprint,
             json.dumps(m.matched[:80]), json.dumps(m.missing[:80]),
             result.hidden_text[:4000], result.error, now()))
        self.db.commit()
        row = self.db.execute(
            "SELECT id FROM submissions WHERE posting_id=? AND file_hash=?",
            (posting_id, file_hash)).fetchone()
        sub_id = row["id"]
        self.db.execute("DELETE FROM findings WHERE submission_id=?", (sub_id,))
        self.db.executemany(
            "INSERT INTO findings (submission_id, code, severity, title, evidence,"
            " location, detail) VALUES (?,?,?,?,?,?,?)",
            [(sub_id, f.code, f.severity, f.title, f.evidence[:500], f.where,
              f.detail[:500]) for f in result.findings])
        self.db.commit()
        return sub_id

    def submissions(self, posting_code: str | None = None,
                    verdict: str | None = None) -> list[dict]:
        q = ("SELECT s.*, p.code AS posting_code, p.title AS posting_title,"
             " c.name AS candidate_name, c.email AS candidate_email"
             " FROM submissions s JOIN postings p ON p.id = s.posting_id"
             " LEFT JOIN candidates c ON c.id = s.candidate_id")
        where, args = [], []
        if posting_code:
            where.append("p.code = ?")
            args.append(posting_code)
        if verdict:
            where.append("s.verdict = ?")
            args.append(verdict)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY s.match_score DESC, s.filename"
        return [dict(r) for r in self.db.execute(q, args)]

    def findings_for(self, submission_id: int) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM findings WHERE submission_id=?"
            " ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1"
            " WHEN 'low' THEN 2 ELSE 3 END, code", (submission_id,))]

    def all_findings(self, severity: str | None = None) -> list[dict]:
        q = ("SELECT f.*, s.filename, p.code AS posting_code"
             " FROM findings f JOIN submissions s ON s.id = f.submission_id"
             " JOIN postings p ON p.id = s.posting_id")
        args: tuple = ()
        if severity:
            q += " WHERE f.severity = ?"
            args = (severity,)
        q += (" ORDER BY CASE f.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1"
              " WHEN 'low' THEN 2 ELSE 3 END, p.code, s.filename")
        return [dict(r) for r in self.db.execute(q, args)]

    def stats(self) -> dict:
        g = lambda q, *a: self.db.execute(q, a).fetchone()[0]  # noqa: E731
        return {
            "postings": g("SELECT COUNT(*) FROM postings"),
            "open_postings": g("SELECT COUNT(*) FROM postings WHERE status='open'"),
            "candidates": g("SELECT COUNT(*) FROM candidates"),
            "submissions": g("SELECT COUNT(*) FROM submissions"),
            "fail": g("SELECT COUNT(*) FROM submissions WHERE verdict='fail'"),
            "review": g("SELECT COUNT(*) FROM submissions WHERE verdict='review'"),
            "clean": g("SELECT COUNT(*) FROM submissions WHERE verdict='clean'"),
            "high_findings": g("SELECT COUNT(*) FROM findings WHERE severity='high'"),
        }
