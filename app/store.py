"""SQLite tables and helpers (sqlite3, no ORM)."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL, address TEXT DEFAULT '',
  capacity INTEGER, ical_url TEXT NOT NULL, ops_email TEXT DEFAULT '', policy TEXT NOT NULL,
  is_demo INTEGER DEFAULT 0, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, venue_id TEXT NOT NULL, uid TEXT NOT NULL, summary TEXT, start TEXT,
  headliner TEXT, support TEXT, description TEXT, fingerprint TEXT, planned_fingerprint TEXT,
  last_run_id TEXT, updated REAL, UNIQUE(venue_id, uid)
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, kind TEXT, venue_id TEXT, event_id TEXT, status TEXT, ctx TEXT, policy TEXT,
  steps TEXT, plan TEXT, baseline TEXT, pools TEXT, trace TEXT, error TEXT, created REAL, updated REAL
);
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, venue_id TEXT, kind TEXT, recipient TEXT,
  subject TEXT, body TEXT, status TEXT, detail TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT, venue_id TEXT, text TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS lessons (
  id INTEGER PRIMARY KEY AUTOINCREMENT, venue_id TEXT, kind TEXT, value TEXT, run_id TEXT, created REAL
);
CREATE INDEX IF NOT EXISTS runs_created ON runs(created);
CREATE INDEX IF NOT EXISTS events_venue ON events(venue_id);
"""

JSON_COLS = {"policy", "support", "ctx", "steps", "plan", "baseline", "pools", "trace"}


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(settings().db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if d[k] is not None:
            try:
                d[k] = json.loads(d[k])
            except (TypeError, ValueError):
                pass
    return d


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


# ---- venues ------------------------------------------------------------------

def create_venue(name: str, city: str, ical_url: str, policy: dict, address: str = "", capacity: int | None = None,
                 ops_email: str = "", is_demo: bool = False, venue_id: str | None = None) -> dict:
    vid = venue_id or new_id("v_")
    with db() as c:
        c.execute("INSERT OR REPLACE INTO venues VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (vid, name, city, address, capacity, ical_url, ops_email, _j(policy), int(is_demo), time.time()))
    return get_venue(vid)  # type: ignore[return-value]


def get_venue(vid: str) -> dict | None:
    with db() as c:
        return _row(c.execute("SELECT * FROM venues WHERE id=?", (vid,)).fetchone())


def list_venues() -> list[dict]:
    with db() as c:
        return [_row(r) for r in c.execute("SELECT * FROM venues ORDER BY is_demo DESC, created")]


# ---- events ------------------------------------------------------------------

def upsert_event(venue_id: str, ev: dict) -> dict:
    with db() as c:
        cur = _row(c.execute("SELECT * FROM events WHERE venue_id=? AND uid=?", (venue_id, ev["uid"])).fetchone())
        if cur is None:
            eid = new_id("e_")
            c.execute("INSERT INTO events (id, venue_id, uid, summary, start, headliner, support, description, "
                      "fingerprint, updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (eid, venue_id, ev["uid"], ev["summary"], ev["start"], ev["headliner"], _j(ev["support"]),
                       ev.get("description", ""), ev["fingerprint"], time.time()))
        else:
            eid = cur["id"]
            c.execute("UPDATE events SET summary=?, start=?, headliner=?, support=?, description=?, fingerprint=?, "
                      "updated=? WHERE id=?",
                      (ev["summary"], ev["start"], ev["headliner"], _j(ev["support"]), ev.get("description", ""),
                       ev["fingerprint"], time.time(), eid))
    return get_event(eid)  # type: ignore[return-value]


def get_event(eid: str) -> dict | None:
    with db() as c:
        return _row(c.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone())


def list_events(venue_id: str) -> list[dict]:
    with db() as c:
        return [_row(r) for r in c.execute("SELECT * FROM events WHERE venue_id=? ORDER BY start", (venue_id,))]


def delete_events_except(venue_id: str, keep_uids: list[str]) -> None:
    with db() as c:
        marks = ",".join("?" * len(keep_uids)) or "''"
        c.execute(f"DELETE FROM events WHERE venue_id=? AND uid NOT IN ({marks})", (venue_id, *keep_uids))


def mark_event_planned(eid: str, fingerprint: str, run_id: str) -> None:
    with db() as c:
        c.execute("UPDATE events SET planned_fingerprint=?, last_run_id=? WHERE id=?", (fingerprint, run_id, eid))


# ---- runs ----------------------------------------------------------------------

def recover_interrupted_runs() -> int:
    """Runs live in memory, so a restart orphans any that were queued or running. Fail them honestly and clear
    their events' planned fingerprint so the watcher plans those shows again."""
    with db() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM runs WHERE status IN ('queued', 'running')").fetchall()]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        c.execute(f"UPDATE runs SET status='error', error=?, updated=? WHERE id IN ({marks})",
                  ("Interrupted by a server restart before it finished; the show is being planned again.",
                   time.time(), *ids))
        c.execute(f"UPDATE events SET planned_fingerprint=NULL WHERE last_run_id IN ({marks})", ids)
    return len(ids)


def create_run(kind: str, ctx: dict, policy: dict, venue_id: str | None = None, event_id: str | None = None) -> str:
    rid = new_id("r_")
    now = time.time()
    with db() as c:
        c.execute("INSERT INTO runs (id, kind, venue_id, event_id, status, ctx, policy, steps, created, updated) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (rid, kind, venue_id, event_id, "queued", _j(ctx), _j(policy), "[]", now, now))
    return rid


def update_run(rid: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields) + ", updated=?"
    vals = [(_j(v) if k in JSON_COLS else v) for k, v in fields.items()]
    with db() as c:
        c.execute(f"UPDATE runs SET {cols} WHERE id=?", (*vals, time.time(), rid))


def append_step(rid: str, step: dict) -> None:
    with db() as c:
        r = c.execute("SELECT steps FROM runs WHERE id=?", (rid,)).fetchone()
        steps = json.loads(r["steps"]) if r and r["steps"] else []
        steps.append(step)
        c.execute("UPDATE runs SET steps=?, updated=? WHERE id=?", (_j(steps), time.time(), rid))


def get_run(rid: str) -> dict | None:
    with db() as c:
        return _row(c.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone())


def list_runs(limit: int = 30, venue_id: str | None = None) -> list[dict]:
    q = "SELECT id, kind, venue_id, event_id, status, ctx, error, created, updated FROM runs"
    args: tuple = ()
    if venue_id:
        q += " WHERE venue_id=?"
        args = (venue_id,)
    with db() as c:
        return [_row(r) for r in c.execute(q + " ORDER BY created DESC LIMIT ?", (*args, limit))]


# ---- outbox ------------------------------------------------------------------------

def add_outbox(run_id: str, venue_id: str | None, kind: str, recipient: str, subject: str, body: str,
               status: str, detail: str) -> dict:
    with db() as c:
        cur = c.execute("INSERT INTO outbox (run_id, venue_id, kind, recipient, subject, body, status, detail, "
                        "created) VALUES (?,?,?,?,?,?,?,?,?)",
                        (run_id, venue_id, kind, recipient, subject, body, status, detail, time.time()))
        return {"id": cur.lastrowid, "kind": kind, "recipient": recipient, "subject": subject, "body": body,
                "status": status, "detail": detail}


def list_outbox(run_id: str) -> list[dict]:
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM outbox WHERE run_id=? ORDER BY id", (run_id,))]


# ---- memory --------------------------------------------------------------------------

def add_memory(venue_id: str, text: str) -> None:
    with db() as c:
        c.execute("INSERT INTO memories (venue_id, text, created) VALUES (?,?,?)", (venue_id, text, time.time()))


def list_memories(venue_id: str, limit: int = 20) -> list[dict]:
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT text, created FROM memories WHERE venue_id=? ORDER BY id DESC LIMIT ?", (venue_id, limit))]


def add_lesson(venue_id: str, kind: str, value: str, run_id: str | None = None) -> None:
    with db() as c:
        c.execute("INSERT INTO lessons (venue_id, kind, value, run_id, created) VALUES (?,?,?,?,?)",
                  (venue_id, kind, value, run_id, time.time()))


def list_lessons(venue_id: str) -> list[dict]:
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT kind, value, run_id, created FROM lessons WHERE venue_id=? ORDER BY id", (venue_id,))]
