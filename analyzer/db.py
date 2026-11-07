import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from .config import DATA


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DATA / "tracewatch.db", timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def initialize():
    with connect() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript('''
        CREATE TABLE IF NOT EXISTS jobs(
          id TEXT PRIMARY KEY, filename TEXT NOT NULL, status TEXT NOT NULL,
          phase TEXT NOT NULL DEFAULT 'queued', created TEXT NOT NULL, updated TEXT NOT NULL,
          bytes INTEGER NOT NULL, lines INTEGER NOT NULL DEFAULT 0,
          completed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
          error TEXT, warnings TEXT NOT NULL DEFAULT '[]', report TEXT,
          peak_rss INTEGER NOT NULL DEFAULT 0, elapsed REAL NOT NULL DEFAULT 0,
          model_fingerprint TEXT);
        CREATE TABLE IF NOT EXISTS entries(
          job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          line INTEGER NOT NULL, raw TEXT NOT NULL, facts TEXT NOT NULL,
          canonical INTEGER NOT NULL, PRIMARY KEY(job_id,line));
        CREATE TABLE IF NOT EXISTS chunks(
          job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          idx INTEGER NOT NULL, content TEXT NOT NULL, lines TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', result TEXT, error TEXT,
          attempts INTEGER NOT NULL DEFAULT 0, prompt_tokens INTEGER, output_tokens INTEGER,
          duration REAL, memory TEXT, PRIMARY KEY(job_id,idx));
        CREATE TABLE IF NOT EXISTS reviews(
          job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', result TEXT,
          prompt_tokens INTEGER, output_tokens INTEGER, duration REAL, attempts INTEGER DEFAULT 0,
          PRIMARY KEY(job_id,key));
        CREATE TABLE IF NOT EXISTS calls(
          id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          stage TEXT NOT NULL, unit TEXT NOT NULL, attempt INTEGER NOT NULL,
          prompt_tokens INTEGER NOT NULL, output_budget INTEGER NOT NULL,
          output_tokens INTEGER, duration REAL NOT NULL, status TEXT NOT NULL,
          error TEXT, response TEXT, created TEXT NOT NULL);
        ''')
        c.execute("UPDATE jobs SET status='interrupted',error='Application stopped. Resume to continue from the last checkpoint.' WHERE status IN ('running','queued')")


def update_job(job_id, **fields):
    fields["updated"] = now()
    with connect() as c:
        c.execute("UPDATE jobs SET " + ",".join(f"{key}=?" for key in fields) + " WHERE id=?", (*fields.values(), job_id))


def get_job(job_id):
    with connect() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    for key in ("warnings", "report"):
        out[key] = json.loads(out[key]) if out[key] else ([] if key == "warnings" else None)
    return out


def warn(job_id, message):
    job = get_job(job_id)
    warnings = job["warnings"]
    if message not in warnings:
        warnings.append(message)
        update_job(job_id, warnings=json.dumps(warnings))
