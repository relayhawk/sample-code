"""
Call storage: one row per call plus an append-only event timeline.

Every interesting thing that happens on a call — transcript lines, tool calls,
engine snapshots, Twilio callbacks — is written as an event. Replaying a call is
just reading its events back in order, which is exactly what the dashboard does.

SQLite keeps the template dependency-free; swap this module for Postgres or
DynamoDB without touching the rest of the app.
"""

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    call_sid      TEXT PRIMARY KEY,
    direction     TEXT,
    from_number   TEXT,
    to_number     TEXT,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    status        TEXT NOT NULL DEFAULT 'live',
    duration_sec  INTEGER,
    recording_sid TEXT,
    recording_offset_ms INTEGER,
    final_state   TEXT
);
CREATE TABLE IF NOT EXISTS events (
    call_sid TEXT NOT NULL,
    seq      INTEGER NOT NULL,
    ts       TEXT NOT NULL,
    t_ms     INTEGER NOT NULL,
    kind     TEXT NOT NULL,
    data     TEXT NOT NULL,
    PRIMARY KEY (call_sid, seq)
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class CallStore:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    # -- calls ---------------------------------------------------------------

    def start_call(self, call_sid: str, *, direction: str = "", from_number: str = "", to_number: str = "") -> None:
        """Create the call row if this is the first time we've seen the call."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO calls (call_sid, direction, from_number, to_number, started_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (call_sid, direction, from_number, to_number, now_iso()),
            )
            # Fill in numbers if an earlier writer created the row without them.
            self._db.execute(
                "UPDATE calls SET direction = COALESCE(NULLIF(direction, ''), ?), "
                "from_number = COALESCE(NULLIF(from_number, ''), ?), "
                "to_number = COALESCE(NULLIF(to_number, ''), ?) WHERE call_sid = ?",
                (direction, from_number, to_number, call_sid),
            )

    def update_call(self, call_sid: str, **fields: Any) -> None:
        if not fields:
            return
        if "final_state" in fields and not isinstance(fields["final_state"], str):
            fields["final_state"] = json.dumps(fields["final_state"])
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._lock, self._db:
            self._db.execute(f"UPDATE calls SET {assignments} WHERE call_sid = ?", (*fields.values(), call_sid))

    def get_call(self, call_sid: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM calls WHERE call_sid = ?", (call_sid,)).fetchone()
        return self._call_dict(row) if row else None

    def list_calls(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM calls ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._call_dict(row) for row in rows]

    @staticmethod
    def _call_dict(row: sqlite3.Row) -> dict[str, Any]:
        call = dict(row)
        call["final_state"] = json.loads(call["final_state"]) if call["final_state"] else None
        return call

    # -- events --------------------------------------------------------------

    def append_event(self, call_sid: str, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        """Append an event and return it as stored (with seq, ts and t_ms filled in)."""
        ts = now_iso()
        with self._lock, self._db:
            started = self._db.execute("SELECT started_at FROM calls WHERE call_sid = ?", (call_sid,)).fetchone()
            if started is None:
                self._db.execute("INSERT INTO calls (call_sid, started_at) VALUES (?, ?)", (call_sid, ts))
                started_at = ts
            else:
                started_at = started["started_at"]
            seq = self._db.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE call_sid = ?", (call_sid,)
            ).fetchone()[0]
            t_ms = max(0, int((_parse(ts) - _parse(started_at)).total_seconds() * 1000))
            self._db.execute(
                "INSERT INTO events (call_sid, seq, ts, t_ms, kind, data) VALUES (?, ?, ?, ?, ?, ?)",
                (call_sid, seq, ts, t_ms, kind, json.dumps(data, default=str)),
            )
        return {"call_sid": call_sid, "seq": seq, "ts": ts, "t_ms": t_ms, "kind": kind, "data": data}

    def list_events(self, call_sid: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM events WHERE call_sid = ? ORDER BY seq", (call_sid,)
        ).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows]

    def close(self) -> None:
        self._db.close()
