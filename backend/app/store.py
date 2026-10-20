"""Small local SQLite store. JSON records keep demo migrations straightforward."""
from __future__ import annotations
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def now() -> str:
    return datetime.now(timezone.utc).isoformat()

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"

class Store:
    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir or os.getenv("DATA_DIR", str(ROOT / ".data"))).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "platform.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(kind, id)
            );
            CREATE TABLE IF NOT EXISTS events (
                run_id TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL,
                ts TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(run_id, seq)
            );
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def put(self, kind: str, value: dict) -> dict:
        with self.lock, self.connect() as db:
            db.execute("INSERT INTO records(kind,id,data) VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data",
                       (kind, value["id"], json.dumps(value, ensure_ascii=False)))
        return value

    def get(self, kind: str, identity: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT data FROM records WHERE kind=? AND id=?", (kind, identity)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, kind: str, **filters) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT data FROM records WHERE kind=? ORDER BY rowid DESC", (kind,)).fetchall()
        values = [json.loads(row[0]) for row in rows]
        return [v for v in values if all(v.get(k) == val for k, val in filters.items())]

    def update(self, kind: str, identity: str, **changes) -> dict:
        with self.lock:
            value = self.get(kind, identity)
            if value is None:
                raise KeyError(identity)
            value.update(changes)
            return self.put(kind, value)

    def event(self, run_id: str, event_type: str, payload: dict) -> dict:
        # SQLite makes sequence allocation + insertion one atomic write transaction.
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
            event = {"seq": seq, "type": event_type, "ts": now(), "payload": payload}
            db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (run_id, seq, event_type, event["ts"], json.dumps(payload, ensure_ascii=False)))
        return event

    def events(self, run_id: str, after_seq: int = 0) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT seq,type,ts,payload FROM events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after_seq)).fetchall()
        return [{"seq": r["seq"], "type": r["type"], "ts": r["ts"], "payload": json.loads(r["payload"])} for r in rows]
