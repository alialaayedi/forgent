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
    PLAN = "plan"                  # a PlanCard produced by the planner
    OUTCOME = "outcome"            # did the plan work? (success/failure + notes)


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
    metadata    TEXT,
    team_id     TEXT
);

CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    session_id  TEXT,
    type        TEXT NOT NULL,
    content     TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '',
    source      TEXT,
    created_at  REAL NOT NULL,
    embedding   BLOB,
    team_id     TEXT,
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

    def __init__(self, db_path: str | Path = "./forgent.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Backward-compat column additions for existing DBs.

        sqlite3 won't 'ADD COLUMN IF NOT EXISTS' pre-3.35, so we introspect
        PRAGMA table_info and add missing columns idempotently. Safe on both
        fresh DBs (no-op, CREATE TABLE already has them) and v0.3 DBs.
        """
        def _col_names(table: str) -> set[str]:
            rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            return {r["name"] for r in rows}

        mem_cols = _col_names("memories")
        if "embedding" not in mem_cols:
            self._conn.execute("ALTER TABLE memories ADD COLUMN embedding BLOB")
        if "team_id" not in mem_cols:
            self._conn.execute("ALTER TABLE memories ADD COLUMN team_id TEXT")
        sess_cols = _col_names("sessions")
        if "team_id" not in sess_cols:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN team_id TEXT")

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
        """Persist a single piece of context. Always returns the stored entry.

        If FORGENT_EMBED_MODEL is set and a provider is reachable, the
        content is also embedded and stored in the `embedding` column for
        later semantic recall. Failures are silent -- memory writes must
        never fail because of a transient embedding API issue.
        """
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            session_id=session_id,
            type=type,
            content=content,
            tags=list(tags or []),
            source=source,
        )
        embedding_blob: bytes | None = None
        try:
            from forgent.embeddings import embed, pack_vector
            vec = embed(content)
            if vec:
                embedding_blob = pack_vector(vec)
        except Exception:
            embedding_blob = None

        team_id_val: str | None = None
        try:
            from forgent.config import ForgentConfig
            team_id_val = ForgentConfig.load().team_id()
        except Exception:
            team_id_val = None

        self._conn.execute(
            "INSERT INTO memories(id, session_id, type, content, tags, source, "
            "created_at, embedding, team_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                entry.id,
                entry.session_id,
                entry.type.value,
                entry.content,
                " ".join(entry.tags),
                entry.source,
                entry.created_at,
                embedding_blob,
                team_id_val,
            ),
        )
        self._conn.commit()
        return entry

    def remember_many(self, entries: Iterable[tuple[str, MemoryType]], **kwargs: Any) -> None:
        for content, type_ in entries:
            self.remember(content, type_, **kwargs)

    def record_outcome(
        self,
        session_id: str,
        success: bool,
        notes: str = "",
        agent_name: str | None = None,
    ) -> MemoryEntry:
        """Persist whether a planned task worked.

        The content string is a human- and FTS-readable one-liner so the router
        can grep it directly; agent name lives in tags for fast filtering.
        """
        status = "success" if success else "failure"
        content = f"outcome={status}"
        if agent_name:
            content += f" agent={agent_name}"
        if notes:
            content += f" notes={notes}"
        tags = ["outcome", status]
        if agent_name:
            tags.append(agent_name)
        return self.remember(
            content,
            MemoryType.OUTCOME,
            session_id=session_id,
            tags=tags,
            source=agent_name,
        )

    def recent_outcomes(
        self,
        agent_name: str | None = None,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """Return the most recent outcome entries, optionally for one agent."""
        if agent_name:
            sql = (
                "SELECT * FROM memories WHERE type=? AND source=? "
                "ORDER BY created_at DESC LIMIT ?"
            )
            rows = self._conn.execute(
                sql, (MemoryType.OUTCOME.value, agent_name, limit)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE type=? ORDER BY created_at DESC LIMIT ?",
                (MemoryType.OUTCOME.value, limit),
            ).fetchall()
        return [_row_to_entry(r) for r in rows]

    # ----- reads ------------------------------------------------------------

    def recall(
        self,
        query: str,
        limit: int = 5,
        type: MemoryType | None = None,
        session_id: str | None = None,
        mode: str = "auto",
    ) -> list[MemoryEntry]:
        """Keyword / semantic / hybrid recall.

        Modes:
          - 'bm25'   FTS5 keyword ranking (the classic forgent behavior).
          - 'semantic' cosine similarity over stored embeddings. Requires
                     FORGENT_EMBED_MODEL set AND memories previously written
                     with embeddings. Falls back to BM25 on any miss.
          - 'hybrid' Reciprocal rank fusion of BM25 + semantic. Surfaces
                     items that are either a strong keyword match OR a
                     strong semantic neighbor -- catches both "grep"-style
                     and "what did I do like this before?" queries.
          - 'auto'   hybrid when embeddings are enabled, else bm25.
        """
        if mode == "auto":
            try:
                from forgent.embeddings import embeddings_enabled
                mode = "hybrid" if embeddings_enabled() else "bm25"
            except Exception:
                mode = "bm25"
        if mode == "semantic":
            return self._recall_semantic(query, limit, type, session_id) \
                or self._recall_bm25(query, limit, type, session_id)
        if mode == "hybrid":
            return self._recall_hybrid(query, limit, type, session_id)
        return self._recall_bm25(query, limit, type, session_id)

    def _recall_bm25(
        self,
        query: str,
        limit: int,
        type: MemoryType | None,
        session_id: str | None,
    ) -> list[MemoryEntry]:
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
            return self._recent(limit=limit, type=type, session_id=session_id)
        return [_row_to_entry(r) for r in rows]

    def _recall_semantic(
        self,
        query: str,
        limit: int,
        type: MemoryType | None,
        session_id: str | None,
    ) -> list[MemoryEntry]:
        """Cosine-similarity ranking over the embedding column."""
        try:
            from forgent.embeddings import embed, unpack_vector, cosine_similarity
        except Exception:
            return []
        q_vec = embed(query)
        if not q_vec:
            return []
        sql = "SELECT * FROM memories WHERE embedding IS NOT NULL "
        params: list[Any] = []
        if type is not None:
            sql += "AND type = ? "
            params.append(type.value)
        if session_id is not None:
            sql += "AND session_id = ? "
            params.append(session_id)
        sql += "ORDER BY created_at DESC LIMIT 500"
        rows = self._conn.execute(sql, params).fetchall()
        scored: list[tuple[float, sqlite3.Row]] = []
        for r in rows:
            blob = r["embedding"]
            if not blob:
                continue
            vec = unpack_vector(blob)
            if not vec:
                continue
            score = cosine_similarity(q_vec, vec)
            if score > 0.0:
                scored.append((score, r))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [_row_to_entry(r) for _, r in scored[:limit]]

    def _recall_hybrid(
        self,
        query: str,
        limit: int,
        type: MemoryType | None,
        session_id: str | None,
    ) -> list[MemoryEntry]:
        """Reciprocal rank fusion of BM25 + semantic.

        RRF score = sum over sources of 1 / (k + rank), with k=60 per the
        standard paper. Simple, parameter-light, and robust to scale
        differences between the two scores (BM25 isn't bounded; cosine is).
        """
        k = 60
        bm25_hits = self._recall_bm25(query, limit * 3, type, session_id)
        sem_hits = self._recall_semantic(query, limit * 3, type, session_id)

        ranks: dict[str, float] = {}
        for i, r in enumerate(bm25_hits):
            ranks[r.id] = ranks.get(r.id, 0.0) + 1.0 / (k + i + 1)
        for i, r in enumerate(sem_hits):
            ranks[r.id] = ranks.get(r.id, 0.0) + 1.0 / (k + i + 1)

        # Map id -> entry, preferring semantic (it has fresher content).
        by_id: dict[str, MemoryEntry] = {r.id: r for r in bm25_hits}
        for r in sem_hits:
            by_id.setdefault(r.id, r)

        fused = sorted(ranks.items(), key=lambda kv: kv[1], reverse=True)
        return [by_id[eid] for eid, _ in fused[:limit] if eid in by_id]

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
            ("Relevant past plans",     self.recall(task, k, MemoryType.PLAN)),
            ("Relevant past outcomes",  self.recall(task, k, MemoryType.OUTCOME)),
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

    # ----- virtual path layer -----------------------------------------------
    #
    # Exposes the memory store as a browsable filesystem so the host LLM can
    # pull context on demand instead of receiving a dumped recall string. The
    # paths are derived, not stored -- they map back to SQL filters on
    # (type, source, tags). Shape inspired by Anthropic's memory_20250818
    # tool protocol.
    #
    # Tree:
    #   /                       -> root, lists top-level directories
    #   /outcomes/              -> by agent (source field)
    #   /outcomes/<agent>/      -> entries for that agent
    #   /plans/<agent>/         -> plans produced for that agent
    #   /notes/                 -> host-written breadcrumbs (tag "host-note")
    #   /notes/<topic>/         -> scoped by first tag after "host-note"
    #   /sessions/<sid>/        -> full timeline of a session
    #   /agents/<name>          -> agent doc (rarely used at runtime)
    #
    # All reads are read-only and return shallow lists (no recursion).

    def list_paths(self, path: str = "/") -> list[dict[str, Any]]:
        """Return child entries at a virtual path.

        Each entry is {path, kind, label, count}. kind is "dir" (more paths
        below) or "leaf" (terminal entry). label is a short summary, count is
        populated for directories.
        """
        p = _normalize_path(path)
        if p == "/":
            roots = [
                ("outcomes", MemoryType.OUTCOME),
                ("plans", MemoryType.PLAN),
                ("notes", MemoryType.NOTE),
                ("sessions", None),
                ("agents", MemoryType.AGENT_DOC),
            ]
            out: list[dict[str, Any]] = []
            for name, mtype in roots:
                if name == "sessions":
                    row = self._conn.execute(
                        "SELECT COUNT(DISTINCT id) AS n FROM sessions"
                    ).fetchone()
                    count = row["n"] if row else 0
                else:
                    count = self._count_by_type(mtype)
                if count > 0:
                    out.append({
                        "path": f"/{name}/",
                        "kind": "dir",
                        "label": f"{count} {name} entries",
                        "count": count,
                    })
            return out

        parts = [seg for seg in p.strip("/").split("/") if seg]
        if not parts:
            return []

        head = parts[0]
        tail = parts[1:]

        if head == "outcomes":
            return self._list_by_source(MemoryType.OUTCOME, "/outcomes", tail)
        if head == "plans":
            return self._list_by_source(MemoryType.PLAN, "/plans", tail)
        if head == "notes":
            return self._list_notes(tail)
        if head == "sessions":
            return self._list_sessions(tail)
        if head == "agents":
            return self._list_agents(tail)
        return []

    def view_path(self, path: str, limit: int = 20) -> list[MemoryEntry]:
        """Read the memory entries at a path. Non-recursive.

        For directory-like paths ('/outcomes/backbone/'), returns the most
        recent `limit` entries under that scope. For leaf paths
        ('/outcomes/backbone/<id>'), returns the single matching entry.
        """
        p = _normalize_path(path)
        parts = [seg for seg in p.strip("/").split("/") if seg]
        if not parts:
            return []

        head = parts[0]
        tail = parts[1:]

        if head == "outcomes":
            return self._view_by_source(MemoryType.OUTCOME, tail, limit)
        if head == "plans":
            return self._view_by_source(MemoryType.PLAN, tail, limit)
        if head == "notes":
            return self._view_notes(tail, limit)
        if head == "sessions":
            return self._view_session(tail, limit)
        if head == "agents":
            return self._view_agents(tail, limit)
        return []

    def write_note(
        self,
        path: str,
        content: str,
        session_id: str | None = None,
    ) -> MemoryEntry:
        """Host-writable breadcrumb. Normalized under /notes/<topic>/<id>.

        Rules:
          - path must start with /notes/
          - second segment is the topic (becomes a searchable tag)
          - entry gets tagged ["host-note", topic]
          - source is set to the full path for exact retrieval
        """
        p = _normalize_path(path)
        if not p.startswith("/notes/"):
            raise ValueError("write_note path must start with /notes/")
        parts = [seg for seg in p.strip("/").split("/") if seg]
        if len(parts) < 2:
            raise ValueError(
                "write_note path must be /notes/<topic> or /notes/<topic>/<subpath>"
            )
        topic = parts[1]
        tags = ["host-note", topic]
        return self.remember(
            content=content,
            type=MemoryType.NOTE,
            session_id=session_id,
            tags=tags,
            source=p,
        )

    # ----- path layer internals ---------------------------------------------

    def _count_by_type(self, mtype: MemoryType | None) -> int:
        if mtype is None:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM memories"
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM memories WHERE type=?",
                (mtype.value,),
            ).fetchone()
        return row["n"] if row else 0

    def _list_by_source(
        self, mtype: MemoryType, prefix: str, tail: list[str]
    ) -> list[dict[str, Any]]:
        """List /<type>/<source>/ or drill into one source."""
        if not tail:
            rows = self._conn.execute(
                "SELECT source, COUNT(*) AS n FROM memories "
                "WHERE type=? AND source IS NOT NULL AND source != '' "
                "GROUP BY source ORDER BY MAX(created_at) DESC",
                (mtype.value,),
            ).fetchall()
            return [
                {
                    "path": f"{prefix}/{r['source']}/",
                    "kind": "dir",
                    "label": f"{r['n']} entries for {r['source']}",
                    "count": r["n"],
                }
                for r in rows
                if r["source"]
            ]
        source = tail[0]
        if len(tail) >= 2:
            return []  # we only go one level deeper for now
        rows = self._conn.execute(
            "SELECT id, content, created_at FROM memories "
            "WHERE type=? AND source=? ORDER BY created_at DESC LIMIT 20",
            (mtype.value, source),
        ).fetchall()
        return [
            {
                "path": f"{prefix}/{source}/{r['id']}",
                "kind": "leaf",
                "label": _shorten(r["content"], 100),
                "count": 1,
            }
            for r in rows
        ]

    def _view_by_source(
        self, mtype: MemoryType, tail: list[str], limit: int
    ) -> list[MemoryEntry]:
        if not tail:
            return self._recent(limit=limit, type=mtype)
        source = tail[0]
        if len(tail) == 1:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE type=? AND source=? "
                "ORDER BY created_at DESC LIMIT ?",
                (mtype.value, source, limit),
            ).fetchall()
            return [_row_to_entry(r) for r in rows]
        entry_id = tail[1]
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE id=? AND type=?",
            (entry_id, mtype.value),
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def _list_notes(self, tail: list[str]) -> list[dict[str, Any]]:
        if not tail:
            # Group host-notes by topic (second tag after "host-note").
            rows = self._conn.execute(
                "SELECT tags, COUNT(*) AS n FROM memories "
                "WHERE type=? AND tags LIKE '%host-note%' "
                "GROUP BY tags",
                (MemoryType.NOTE.value,),
            ).fetchall()
            topic_counts: dict[str, int] = {}
            for r in rows:
                tag_tokens = (r["tags"] or "").split()
                for t in tag_tokens:
                    if t and t != "host-note":
                        topic_counts[t] = topic_counts.get(t, 0) + r["n"]
                        break
            return [
                {
                    "path": f"/notes/{topic}/",
                    "kind": "dir",
                    "label": f"{count} notes tagged {topic}",
                    "count": count,
                }
                for topic, count in sorted(
                    topic_counts.items(), key=lambda kv: -kv[1]
                )
            ]
        topic = tail[0]
        if len(tail) >= 2:
            return []
        rows = self._conn.execute(
            "SELECT id, content, created_at FROM memories "
            "WHERE type=? AND tags LIKE ? ORDER BY created_at DESC LIMIT 20",
            (MemoryType.NOTE.value, f"%{topic}%"),
        ).fetchall()
        return [
            {
                "path": f"/notes/{topic}/{r['id']}",
                "kind": "leaf",
                "label": _shorten(r["content"], 100),
                "count": 1,
            }
            for r in rows
        ]

    def _view_notes(self, tail: list[str], limit: int) -> list[MemoryEntry]:
        if not tail:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE type=? AND tags LIKE '%host-note%' "
                "ORDER BY created_at DESC LIMIT ?",
                (MemoryType.NOTE.value, limit),
            ).fetchall()
            return [_row_to_entry(r) for r in rows]
        topic = tail[0]
        if len(tail) == 1:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE type=? AND tags LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (MemoryType.NOTE.value, f"%{topic}%", limit),
            ).fetchall()
            return [_row_to_entry(r) for r in rows]
        entry_id = tail[1]
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE id=?", (entry_id,)
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def _list_sessions(self, tail: list[str]) -> list[dict[str, Any]]:
        if not tail:
            rows = self._conn.execute(
                "SELECT id, task, status, created_at FROM sessions "
                "ORDER BY created_at DESC LIMIT 25"
            ).fetchall()
            return [
                {
                    "path": f"/sessions/{r['id']}/",
                    "kind": "dir",
                    "label": f"[{r['status']}] {_shorten(r['task'], 80)}",
                    "count": 1,
                }
                for r in rows
            ]
        return []  # session drill-down goes through view_path

    def _view_session(self, tail: list[str], limit: int) -> list[MemoryEntry]:
        if not tail:
            return []
        sid = tail[0]
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE session_id=? "
            "ORDER BY created_at ASC LIMIT ?",
            (sid, limit),
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def _list_agents(self, tail: list[str]) -> list[dict[str, Any]]:
        if not tail:
            rows = self._conn.execute(
                "SELECT source, COUNT(*) AS n FROM memories "
                "WHERE type=? AND source IS NOT NULL "
                "GROUP BY source ORDER BY source",
                (MemoryType.AGENT_DOC.value,),
            ).fetchall()
            return [
                {
                    "path": f"/agents/{r['source']}",
                    "kind": "leaf",
                    "label": f"{r['n']} doc entries",
                    "count": r["n"],
                }
                for r in rows
                if r["source"]
            ]
        return []

    def _view_agents(self, tail: list[str], limit: int) -> list[MemoryEntry]:
        if not tail:
            return self._recent(limit=limit, type=MemoryType.AGENT_DOC)
        name = tail[0]
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE type=? AND source=? "
            "ORDER BY created_at DESC LIMIT ?",
            (MemoryType.AGENT_DOC.value, name, limit),
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

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


def _normalize_path(p: str) -> str:
    """Collapse // and strip trailing spaces. Never ends with // (except root)."""
    if not p:
        return "/"
    p = p.strip()
    if not p.startswith("/"):
        p = "/" + p
    while "//" in p:
        p = p.replace("//", "/")
    return p


def _shorten(s: str, limit: int) -> str:
    s = (s or "").strip().replace("\n", " ")
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "..."
