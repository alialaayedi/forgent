"""SQLite + FTS5 backed memory store.

Design goals:
  * Zero external dependencies (sqlite ships with Python)
  * Full-text search via FTS5 for keyword recall — works without an embedding API
  * Optional embedding column so you can layer semantic recall on top later
  * Type-tagged entries so the orchestrator can scope recall (e.g. "only past
    routing decisions for similar tasks", "only artifacts from this session")
  * Append-only: nothing is overwritten; old context is always recoverable

Schema:
    sessions(id, task, created_at, status, metadata_json)
    memories(id, session_id, type, content, tags, source, created_at)
    memories_fts(content, tags) — virtual FTS5 table mirrored from memories
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Iterator


class MemoryType(str, Enum):
    """What kind of memory entry this is.

    These are recall scopes — the router asks for "past routing decisions" and
    "past task outputs" separately because they serve different purposes.
    """

    TASK = "task"                  # the original user request
    ROUTING = "routing"            # the router's decision + reasoning
    AGENT_OUTPUT = "agent_output"  # what an agent produced
    DECISION = "decision"          # a checkpoint / branch decision
    AGENT_DOC = "agent_doc"        # curated agent definition (system prompt)
    NOTE = "note"                  # free-form note from the user or system
    ARTIFACT = "artifact"          # file path or blob produced by an agent


@dataclass
class MemoryEntry:
    id: str
    session_id: str | None
    type: MemoryType
    content: str
    tags: list[str] = field(default_factory=list)
    source: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_context_block(self) -> str:
        """Render this memory as a block ready to drop into an agent prompt."""
        tag_str = f" [{', '.join(self.tags)}]" if self.tags else ""
        src = f" (source: {self.source})" if self.source else ""
        return f"[{self.type.value}{tag_str}{src}]\n{self.content}".strip()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    task        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    metadata    TEXT
);

CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    session_id  TEXT,
    type        TEXT NOT NULL,
    content     TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '',
    source      TEXT,
    created_at  REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id);
CREATE INDEX IF NOT EXISTS idx_memories_type    ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC);

-- FTS5 virtual table for keyword recall. Mirrored via triggers.
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    tags,
    content='memories',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, tags)
    VALUES (new.rowid, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags)
    VALUES('delete', old.rowid, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags)
    VALUES('delete', old.rowid, old.content, old.tags);
    INSERT INTO memories_fts(rowid, content, tags)
    VALUES (new.rowid, new.content, new.tags);
END;
"""


class MemoryStore:
    """Persistent knowledge base with full-text recall.

    The orchestrator calls into this on every task:
        1. start_session(task)               — create a new conversation thread
        2. context_for(task)                 — pull relevant past memories
        3. remember(...) after each agent run — append outputs and decisions
    """

    def __init__(self, db_path: str | Path = "./orchestrator.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ----- session lifecycle ------------------------------------------------

    def start_session(self, task: str, metadata: dict[str, Any] | None = None) -> str:
        sid = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO sessions(id, task, created_at, status, metadata) VALUES (?,?,?,?,?)",
            (sid, task, time.time(), "open", json.dumps(metadata or {})),
        )
        self._conn.commit()
        # Also store the task itself as a memory so it's recallable later.
        self.remember(task, MemoryType.TASK, session_id=sid, tags=["user_request"])
        return sid

    def close_session(self, session_id: str, status: str = "completed") -> None:
        self._conn.execute(
            "UPDATE sessions SET status=? WHERE id=?", (status, session_id)
        )
        self._conn.commit()

    # ----- writes -----------------------------------------------------------

    def remember(
        self,
        content: str,
        type: MemoryType,
        session_id: str | None = None,
        tags: Iterable[str] | None = None,
        source: str | None = None,
    ) -> MemoryEntry:
        """Persist a single piece of context. Always returns the stored entry."""
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            session_id=session_id,
            type=type,
            content=content,
            tags=list(tags or []),
            source=source,
        )
        self._conn.execute(
            "INSERT INTO memories(id, session_id, type, content, tags, source, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                entry.id,
                entry.session_id,
                entry.type.value,
                entry.content,
                " ".join(entry.tags),
                entry.source,
                entry.created_at,
            ),
        )
        self._conn.commit()
        return entry

    def remember_many(self, entries: Iterable[tuple[str, MemoryType]], **kwargs: Any) -> None:
        for content, type_ in entries:
            self.remember(content, type_, **kwargs)

    # ----- reads ------------------------------------------------------------

    def recall(
        self,
        query: str,
        limit: int = 5,
        type: MemoryType | None = None,
        session_id: str | None = None,
    ) -> list[MemoryEntry]:
        """Keyword recall via FTS5, ranked by BM25.

        FTS5 is forgiving about phrasing, but the query must be tokens — strip
        punctuation that would otherwise be parsed as operators.
        """
        clean = _sanitize_fts_query(query)
        if not clean:
            return self._recent(limit=limit, type=type, session_id=session_id)

        sql = (
            "SELECT m.* FROM memories_fts f "
            "JOIN memories m ON m.rowid = f.rowid "
            "WHERE memories_fts MATCH ? "
        )
        params: list[Any] = [clean]
        if type is not None:
            sql += "AND m.type = ? "
            params.append(type.value)
        if session_id is not None:
            sql += "AND m.session_id = ? "
            params.append(session_id)
        sql += "ORDER BY bm25(memories_fts) LIMIT ?"
        params.append(limit)

        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # Bad FTS expression — fall back to recency
            return self._recent(limit=limit, type=type, session_id=session_id)
        return [_row_to_entry(r) for r in rows]

    def _recent(
        self,
        limit: int,
        type: MemoryType | None = None,
        session_id: str | None = None,
    ) -> list[MemoryEntry]:
        sql = "SELECT * FROM memories WHERE 1=1 "
        params: list[Any] = []
        if type is not None:
            sql += "AND type = ? "
            params.append(type.value)
        if session_id is not None:
            sql += "AND session_id = ? "
            params.append(session_id)
        sql += "ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_entry(r) for r in rows]

    def session_history(self, session_id: str) -> list[MemoryEntry]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE session_id=? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

    # ----- the high-level API the orchestrator actually uses ----------------

    def context_for(self, task: str, k: int = 6) -> str:
        """Build a ready-to-paste context block for a new task.

        Recipe:
          * top-K relevant past task outputs (so the agent sees what worked)
          * top-K relevant past routing decisions (so we don't re-plan from scratch)
          * top-K relevant agent docs (so the agent has institutional knowledge)

        Returns a single string. Empty string if nothing relevant exists yet
        (e.g. on the very first run).
        """
        buckets = [
            ("Relevant past outputs",   self.recall(task, k, MemoryType.AGENT_OUTPUT)),
            ("Relevant past decisions", self.recall(task, k, MemoryType.ROUTING)),
            ("Relevant institutional knowledge", self.recall(task, k, MemoryType.NOTE)),
        ]
        sections: list[str] = []
        for label, entries in buckets:
            if not entries:
                continue
            block = "\n\n".join(e.to_context_block() for e in entries)
            sections.append(f"### {label}\n{block}")
        return "\n\n".join(sections)

    def stats(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT type, COUNT(*) AS n FROM memories GROUP BY type"
        ).fetchall()
        return {r["type"]: r["n"] for r in rows}

    @contextmanager
    def session(self, task: str, metadata: dict[str, Any] | None = None) -> Iterator[str]:
        sid = self.start_session(task, metadata)
        try:
            yield sid
        finally:
            self.close_session(sid)


def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
    return MemoryEntry(
        id=row["id"],
        session_id=row["session_id"],
        type=MemoryType(row["type"]),
        content=row["content"],
        tags=row["tags"].split() if row["tags"] else [],
        source=row["source"],
        created_at=row["created_at"],
    )


_FTS_RESERVED = set('"():*^')


def _sanitize_fts_query(q: str) -> str:
    """Strip FTS5 operators so user queries don't blow up the parser."""
    cleaned = "".join(" " if ch in _FTS_RESERVED else ch for ch in q)
    tokens = [t for t in cleaned.split() if t and not t.startswith("-")]
    return " OR ".join(tokens)
