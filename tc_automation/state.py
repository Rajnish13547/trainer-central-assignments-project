"""Local record of processed files (SQLite) -> guarantees each file/submission is handled once."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(str(path))
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS processed (
                   file_id TEXT PRIMARY KEY,
                   name TEXT,
                   md5 TEXT,
                   status TEXT NOT NULL,      -- pushed | not_mine | empty | duplicate | skipped
                   detail TEXT,
                   dedupe_key TEXT,
                   entry_id TEXT,
                   first_seen_at TEXT,
                   processed_at TEXT
               )"""
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_key ON processed(dedupe_key)")
        self.db.commit()

    def is_done(self, file_id: str, md5: Optional[str]) -> bool:
        """True if this exact file version was already handled. A changed checksum means re-check."""
        row = self.db.execute("SELECT md5 FROM processed WHERE file_id=?", (file_id,)).fetchone()
        if row is None:
            return False
        return (md5 is None) or (row[0] is None) or (row[0] == md5)

    def pushed_entry_for_key(self, dedupe_key: str) -> Optional[str]:
        row = self.db.execute(
            "SELECT entry_id FROM processed WHERE dedupe_key=? AND status='pushed'", (dedupe_key,)
        ).fetchone()
        return row[0] if row else None

    def record(self, file_id, name, md5, status, detail="", dedupe_key=None, entry_id=None):
        now = _now()
        self.db.execute(
            """INSERT INTO processed(file_id,name,md5,status,detail,dedupe_key,entry_id,first_seen_at,processed_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(file_id) DO UPDATE SET name=excluded.name, md5=excluded.md5,
                 status=excluded.status, detail=excluded.detail, dedupe_key=excluded.dedupe_key,
                 entry_id=excluded.entry_id, processed_at=excluded.processed_at""",
            (file_id, name, md5, status, detail, dedupe_key, entry_id, now, now),
        )
        self.db.commit()

    def close(self):
        self.db.close()
