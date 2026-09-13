"""SQLite persistence: sessions, tags, and the search/filter query.

Connections are opened per operation (cheap in SQLite) so the recorder thread and
the web request threads never share one. WAL keeps readers from blocking the writer.
"""
from __future__ import annotations

import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

from app.midi.recorder import SessionRecord

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    started_at   TEXT    NOT NULL,
    ended_at     TEXT    NOT NULL,
    duration_ms  INTEGER NOT NULL DEFAULT 0,
    name         TEXT    NOT NULL DEFAULT '',
    notes        TEXT    NOT NULL DEFAULT '',
    event_count  INTEGER NOT NULL DEFAULT 0,
    note_count   INTEGER NOT NULL DEFAULT 0,
    lowest_note  INTEGER,
    highest_note INTEGER,
    avg_velocity REAL,
    device_name  TEXT    NOT NULL DEFAULT '',
    favorite     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_started  ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_duration ON sessions(duration_ms);
CREATE INDEX IF NOT EXISTS idx_sessions_favorite ON sessions(favorite);

CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS session_tags (
    session_id TEXT    NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    tag_id     INTEGER NOT NULL REFERENCES tags(id)     ON DELETE CASCADE,
    PRIMARY KEY (session_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_session_tags_tag ON session_tags(tag_id);
"""

SORT_COLUMNS = {
    "date": "started_at",
    "duration": "duration_ms",
    "name": "name COLLATE NOCASE",
    "notes": "note_count",
}


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # -- plumbing ------------------------------------------------------------
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # -- writes --------------------------------------------------------------
    def insert_session(self, record: SessionRecord, name: str = "") -> None:
        now = _utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sessions
                    (id, started_at, ended_at, duration_ms, name, notes, event_count,
                     note_count, lowest_note, highest_note, avg_velocity, device_name,
                     favorite, created_at, updated_at)
                VALUES (?,?,?,?,?,'',?,?,?,?,?,?,
                        COALESCE((SELECT favorite FROM sessions WHERE id = ?), 0), ?, ?)
                """,
                (
                    record.id,
                    _iso(record.started_at),
                    _iso(record.ended_at),
                    record.duration_ms,
                    name or default_session_name(record.started_at),
                    record.event_count,
                    record.note_count,
                    record.lowest_note,
                    record.highest_note,
                    record.avg_velocity,
                    record.device_name,
                    record.id,
                    now,
                    now,
                ),
            )

    def update_session(self, session_id: str, **fields: Any) -> bool:
        allowed = {"name", "notes", "favorite"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        assignments = ", ".join(f"{k} = ?" for k in updates)
        with self.connect() as conn:
            cur = conn.execute(
                f"UPDATE sessions SET {assignments}, updated_at = ? WHERE id = ?",
                (*updates.values(), _utc_now_iso(), session_id),
            )
            return cur.rowcount > 0

    def delete_session(self, session_id: str, sessions_dir: Path) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            deleted = cur.rowcount > 0
        directory = sessions_dir / session_id
        if deleted and directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
        return deleted

    # -- tags ----------------------------------------------------------------
    def set_session_tags(self, session_id: str, names: Iterable[str]) -> list[str]:
        cleaned = _clean_tag_names(names)
        with self.connect() as conn:
            conn.execute("DELETE FROM session_tags WHERE session_id = ?", (session_id,))
            for name in cleaned:
                conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
                row = conn.execute(
                    "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)
                ).fetchone()
                conn.execute(
                    "INSERT OR IGNORE INTO session_tags(session_id, tag_id) VALUES (?,?)",
                    (session_id, row["id"]),
                )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (_utc_now_iso(), session_id),
            )
        self.prune_unused_tags()
        return cleaned

    def add_session_tag(self, session_id: str, name: str) -> list[str]:
        current = self.get_session_tags(session_id)
        return self.set_session_tags(session_id, [*current, name])

    def remove_session_tag(self, session_id: str, name: str) -> list[str]:
        current = self.get_session_tags(session_id)
        remaining = [t for t in current if t.lower() != name.strip().lower()]
        return self.set_session_tags(session_id, remaining)

    def get_session_tags(self, session_id: str) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT t.name FROM tags t
                JOIN session_tags st ON st.tag_id = t.id
                WHERE st.session_id = ? ORDER BY t.name COLLATE NOCASE
                """,
                (session_id,),
            ).fetchall()
        return [r["name"] for r in rows]

    def list_tags(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT t.name AS name, COUNT(st.session_id) AS count
                FROM tags t LEFT JOIN session_tags st ON st.tag_id = t.id
                GROUP BY t.id HAVING count > 0
                ORDER BY count DESC, t.name COLLATE NOCASE
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def rename_tag(self, old: str, new: str) -> bool:
        new = new.strip()
        if not new:
            return False
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (new,)
            ).fetchone()
            target = conn.execute(
                "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (old,)
            ).fetchone()
            if target is None:
                return False
            if existing is not None and existing["id"] != target["id"]:
                # Merge into the tag that already owns the new name.
                conn.execute(
                    "UPDATE OR IGNORE session_tags SET tag_id = ? WHERE tag_id = ?",
                    (existing["id"], target["id"]),
                )
                conn.execute("DELETE FROM tags WHERE id = ?", (target["id"],))
            else:
                conn.execute("UPDATE tags SET name = ? WHERE id = ?", (new, target["id"]))
            return True

    def prune_unused_tags(self) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM session_tags)"
            )

    # -- reads ---------------------------------------------------------------
    def known_ids(self) -> set[str]:
        with self.connect() as conn:
            return {r["id"] for r in conn.execute("SELECT id FROM sessions")}

    def get_session(self, session_id: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        session = dict(row)
        session["favorite"] = bool(session["favorite"])
        session["tags"] = self.get_session_tags(session_id)
        return session

    def search(
        self,
        q: str = "",
        tags: Sequence[str] = (),
        date_from: str = "",
        date_to: str = "",
        min_duration_ms: Optional[int] = None,
        max_duration_ms: Optional[int] = None,
        favorite_only: bool = False,
        sort: str = "date",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        where: list[str] = []
        params: list[Any] = []

        if q.strip():
            # Match the session's own text, or any of its tags.
            where.append(
                """(
                    sessions.name  LIKE ? COLLATE NOCASE
                 OR sessions.notes LIKE ? COLLATE NOCASE
                 OR EXISTS (SELECT 1 FROM session_tags st JOIN tags t ON t.id = st.tag_id
                            WHERE st.session_id = sessions.id
                              AND t.name LIKE ? COLLATE NOCASE)
                )"""
            )
            like = f"%{q.strip()}%"
            params += [like, like, like]

        for tag in _clean_tag_names(tags):
            # Repeated EXISTS gives AND semantics: all selected tags must be present.
            where.append(
                """EXISTS (SELECT 1 FROM session_tags st JOIN tags t ON t.id = st.tag_id
                           WHERE st.session_id = sessions.id AND t.name = ? COLLATE NOCASE)"""
            )
            params.append(tag)

        if date_from:
            where.append("sessions.started_at >= ?")
            params.append(_day_bound(date_from, end=False))
        if date_to:
            where.append("sessions.started_at <= ?")
            params.append(_day_bound(date_to, end=True))
        if min_duration_ms is not None:
            where.append("sessions.duration_ms >= ?")
            params.append(min_duration_ms)
        if max_duration_ms is not None:
            where.append("sessions.duration_ms <= ?")
            params.append(max_duration_ms)
        if favorite_only:
            where.append("sessions.favorite = 1")

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        column = SORT_COLUMNS.get(sort, SORT_COLUMNS["date"])
        direction = "ASC" if order.lower() == "asc" else "DESC"
        limit = max(1, min(limit, 200))

        with self.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS n FROM sessions {clause}", params
            ).fetchone()["n"]
            rows = conn.execute(
                f"""
                SELECT sessions.*,
                       (SELECT GROUP_CONCAT(t.name, char(31))
                          FROM session_tags st JOIN tags t ON t.id = st.tag_id
                         WHERE st.session_id = sessions.id) AS tag_blob
                FROM sessions {clause}
                ORDER BY {column} {direction}, sessions.started_at DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        items = []
        for row in rows:
            item = dict(row)
            blob = item.pop("tag_blob", None)
            item["tags"] = sorted(blob.split("\x1f")) if blob else []
            item["favorite"] = bool(item["favorite"])
            items.append(item)

        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(items) < total,
        }

    def stats(self) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS sessions,
                       COALESCE(SUM(duration_ms), 0) AS total_ms,
                       COALESCE(SUM(note_count), 0)  AS total_notes,
                       MAX(started_at)               AS latest
                FROM sessions
                """
            ).fetchone()
        return dict(row)


# -- helpers -----------------------------------------------------------------
def _day_bound(value: str, end: bool) -> str:
    """Turn a bare YYYY-MM-DD from a date picker into a UTC timestamp.

    The picker gives the user's local calendar day, but timestamps are stored in
    UTC. Without this conversion a late-evening session west of Greenwich is
    filed under the next day and vanishes from the day you actually played it.
    """
    if "T" in value:
        return value
    try:
        day = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return value
    if end:
        day = day.replace(hour=23, minute=59, second=59, microsecond=999_999)
    # A naive datetime is assumed to be local time by astimezone().
    return day.astimezone(timezone.utc).isoformat()


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_tag_names(names: Iterable[str]) -> list[str]:
    seen: dict[str, str] = {}
    for raw in names:
        name = " ".join(str(raw).split())[:40].strip()
        if name and name.lower() not in seen:
            seen[name.lower()] = name
    return list(seen.values())


def default_session_name(started_at: datetime) -> str:
    """Human, sortable, and immediately meaningful: 'Sep 13 - 2:47 PM'."""
    local = started_at.astimezone()
    return local.strftime("%b %-d, %-I:%M %p")
