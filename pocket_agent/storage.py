"""Persistent SQLite conversation memory and crash-recoverable job queue."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
import uuid
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: Path | str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._create()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _create(self):
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_msg_scope ON messages(scope,id);
            CREATE TABLE IF NOT EXISTS summaries (
                scope TEXT PRIMARY KEY, content TEXT NOT NULL, through_id INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS facts (
                scope TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY(scope,key)
            );
            CREATE TABLE IF NOT EXISTS preferences (
                scope TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY(scope,key)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
                detail TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, channel TEXT NOT NULL,
                user_id TEXT NOT NULL, chat_id TEXT NOT NULL, prompt TEXT NOT NULL,
                status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_job_status ON jobs(status,created_at);
            """)

    def add_message(self, scope: str, role: str, content: str) -> int:
        with self._conn() as c:
            cur = c.execute("INSERT INTO messages(scope,role,content,created_at) VALUES(?,?,?,?)",
                            (scope, role, content[:12000], utc_now()))
            return int(cur.lastrowid)

    def recent(self, scope: str, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT id,role,content FROM messages WHERE scope=? ORDER BY id DESC LIMIT ?",
                             (scope, limit)).fetchall()
            return [dict(r) for r in reversed(rows)]

    def messages_after(self, scope: str, after_id: int) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT id,role,content FROM messages WHERE scope=? AND id>? ORDER BY id",
                             (scope, after_id)).fetchall()
            return [dict(r) for r in rows]

    def get_summary(self, scope: str) -> tuple[str, int]:
        with self._conn() as c:
            r = c.execute("SELECT content,through_id FROM summaries WHERE scope=?", (scope,)).fetchone()
            return (r["content"], r["through_id"]) if r else ("", 0)

    def set_summary(self, scope: str, content: str, through_id: int):
        with self._conn() as c:
            c.execute("INSERT INTO summaries(scope,content,through_id) VALUES(?,?,?) "
                      "ON CONFLICT(scope) DO UPDATE SET content=excluded.content,through_id=excluded.through_id",
                      (scope, content[:8000], through_id))

    def set_fact(self, scope: str, key: str, value: str):
        with self._conn() as c:
            c.execute("INSERT INTO facts(scope,key,value,updated_at) VALUES(?,?,?,?) "
                      "ON CONFLICT(scope,key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                      (scope, key[:100], value[:3000], utc_now()))

    def facts(self, scope: str) -> dict[str, str]:
        with self._conn() as c:
            return {r["key"]: r["value"] for r in c.execute(
                "SELECT key,value FROM facts WHERE scope=? ORDER BY key LIMIT 100", (scope,))}

    def forget_fact(self, scope: str, key: str) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM facts WHERE scope=? AND key=?", (scope, key)).rowcount > 0

    def preference(self, scope: str, key: str) -> str | None:
        with self._conn() as c:
            r = c.execute("SELECT value FROM preferences WHERE scope=? AND key=?", (scope, key)).fetchone()
            return r["value"] if r else None

    def set_preference(self, scope: str, key: str, value: str):
        with self._conn() as c:
            c.execute("INSERT INTO preferences(scope,key,value) VALUES(?,?,?) "
                      "ON CONFLICT(scope,key) DO UPDATE SET value=excluded.value", (scope, key, value))

    def add_event(self, scope: str, detail: str):
        with self._conn() as c:
            c.execute("INSERT INTO events(scope,detail,created_at) VALUES(?,?,?)", (scope, detail[:500], utc_now()))

    def events(self, scope: str, limit: int = 8) -> list[str]:
        with self._conn() as c:
            rows = c.execute("SELECT detail,created_at FROM events WHERE scope=? ORDER BY id DESC LIMIT ?",
                             (scope, limit)).fetchall()
            return [f"{r['created_at']}: {r['detail']}" for r in reversed(rows)]

    def clear_chat(self, scope: str):
        with self._conn() as c:
            c.execute("DELETE FROM messages WHERE scope=?", (scope,))
            c.execute("DELETE FROM summaries WHERE scope=?", (scope,))
            c.execute("DELETE FROM events WHERE scope=?", (scope,))

    def queue_job(self, scope: str, channel: str, user_id: str, chat_id: str, prompt: str) -> str:
        job_id = uuid.uuid4().hex[:12]
        now = utc_now()
        with self._conn() as c:
            c.execute("INSERT INTO jobs(id,scope,channel,user_id,chat_id,prompt,status,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,'queued',?,?)",
                      (job_id, scope, channel, user_id, chat_id, prompt[:12000], now, now))
        return job_id

    def requeue_interrupted(self):
        with self._conn() as c:
            c.execute("UPDATE jobs SET status='queued',updated_at=? WHERE status='running'", (utc_now(),))

    def claim_job(self) -> dict | None:
        # Transaction prevents two worker processes from claiming the same job.
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if r:
                c.execute("UPDATE jobs SET status='running',updated_at=? WHERE id=?", (utc_now(), r["id"]))
                return dict(r)
        return None

    def complete_job(self, job_id: str, result: str = "", error: str = "") -> bool:
        status = "failed" if error else "done"
        with self._conn() as c:
            return c.execute("UPDATE jobs SET status=?,result=?,error=?,updated_at=? "
                             "WHERE id=? AND status='running'",
                             (status, result[:25000], error[:4000], utc_now(), job_id)).rowcount > 0

    def cancel_job(self, scope: str, job_id: str) -> bool:
        with self._conn() as c:
            return c.execute("UPDATE jobs SET status='cancelled',updated_at=? "
                             "WHERE id=? AND scope=? AND status IN ('queued','running')",
                             (utc_now(), job_id, scope)).rowcount > 0

    def get_job(self, job_id: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(r) if r else None

    def list_jobs(self, scope: str, limit: int = 10) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT id,status,prompt,created_at FROM jobs WHERE scope=? ORDER BY created_at DESC,id DESC LIMIT ?",
                (scope, limit))]
