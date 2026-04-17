"""Tests for v0.4 features that aren't covered elsewhere.

Existing test files stay focused:
  - test_smoke.py: orchestrator + planner + memory core flows
  - test_statusline.py: status line rendering + install

This file covers the v0.4 gap closers:
  - Config read/write for new keys (render_mode, theme, autocompact_pct, team_id)
  - Router transparency (alternates populated)
  - PlanCard subplans + budgets
  - Revise plan round-trip
  - Verifier detectors + aggregator
  - Embedding helpers (pack/unpack/cosine)
  - Hybrid recall fallback when embeddings disabled
  - IDE setup snippets
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from forgent.config import ForgentConfig
from forgent.memory import MemoryStore, MemoryType
from forgent.orchestrator import Orchestrator
from forgent.planner import PlanCard
from forgent.registry.loader import Registry


def _fresh_orchestrator(tmp_path: Path) -> Orchestrator:
    os.environ.pop("ANTHROPIC_API_KEY", None)
    db = tmp_path / "v04.db"
    return Orchestrator(registry=Registry.load(), memory=MemoryStore(db), db_path=str(db))


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_config_new_fields_roundtrip(tmp_path):
    cfg = ForgentConfig.load(tmp_path / "c.json")
    cfg.set_render_mode("powerline")
    cfg.set_theme("highcontrast")
    cfg.set_segment("cost", False)
    cfg.set_autocompact_pct(55)
    cfg.set_team_id("acme")
    cfg.set_default_budget_ms(5000)

    cfg2 = ForgentConfig.load(tmp_path / "c.json")
    assert cfg2.render_mode() == "powerline"
    assert cfg2.theme_name() == "highcontrast"
    assert cfg2.segment_toggles() == {"cost": False}
    assert cfg2.autocompact_pct() == 55
    assert cfg2.team_id() == "acme"
    assert cfg2.default_budget_ms() == 5000


def test_config_rejects_bad_values(tmp_path):
    cfg = ForgentConfig.load(tmp_path / "c.json")
    with pytest.raises(ValueError):
        cfg.set_render_mode("banana")
    with pytest.raises(ValueError):
        cfg.set_autocompact_pct(0)
    with pytest.raises(ValueError):
        cfg.set_autocompact_pct(100)


# ---------------------------------------------------------------------------
# router transparency
# ---------------------------------------------------------------------------


def test_router_emits_alternates(tmp_path):
    """Heuristic router fills alternates with the runners-up."""
    orch = _fresh_orchestrator(tmp_path)
    d = orch.router.route("review Python code for SQL injection")
    assert d.primary
    # With 60+ agents and a clear task, we expect at least one alternate.
    assert len(d.alternates) >= 1
    for a in d.alternates:
        assert a.name
        assert 0.0 <= a.score <= 1.0


def test_plan_card_renders_why_this_pack(tmp_path):
    orch = _fresh_orchestrator(tmp_path)
    plan = asyncio.run(orch.advise_async("audit Python security"))
    if plan.alternates:
        md = plan.to_markdown()
        assert "Why this pack?" in md
        assert "Other candidates" in md


# ---------------------------------------------------------------------------
# multi-agent plan graphs + budgets
# ---------------------------------------------------------------------------


def test_plan_card_supports_subplans_field(tmp_path):
    """New PlanCard fields exist and default cleanly."""
    orch = _fresh_orchestrator(tmp_path)
    plan = asyncio.run(orch.advise_async("simple task"))
    assert isinstance(plan.subplans, list)
    assert plan.version == 1
    assert plan.budget_ms is None


def test_advise_threads_budget(tmp_path):
    orch = _fresh_orchestrator(tmp_path)
    plan = asyncio.run(orch.advise_async("simple task", budget_ms=500))
    assert plan.budget_ms == 500
    # Tight budget + no API key -> heuristic path.
    assert plan.heuristic is True


# ---------------------------------------------------------------------------
# revise plan
# ---------------------------------------------------------------------------


def test_revise_produces_v2(tmp_path):
    orch = _fresh_orchestrator(tmp_path)
    plan1 = asyncio.run(orch.advise_async("implement login endpoint"))
    plan2 = asyncio.run(
        orch.revise_async(
            session_id=plan1.session_id,
            reason="first plan assumed JWT but project uses sessions",
            completed_steps=["read auth docs"],
        )
    )
    assert plan2.version == 2
    assert plan2.session_id == plan1.session_id
    assert plan2.task == plan1.task  # original task restored


def test_revise_rejects_unknown_session(tmp_path):
    orch = _fresh_orchestrator(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(orch.revise_async("nonexistent-session", "oops"))


# ---------------------------------------------------------------------------
# verifier
# ---------------------------------------------------------------------------


def test_verifier_aggregates_to_unknown_in_empty_dir(tmp_path):
    """In a bare tmp dir there's no git / no tests / no lint / no gh."""
    from forgent.verify import Verifier
    r = Verifier().run(tmp_path)
    # All detectors should return "unknown" -> no pass/fail in `ran`.
    assert r.ran == []
    assert len(r.skipped) == 4
    assert r.success is False  # conjunction over nothing is false by design


def test_verifier_git_diff_passes_when_repo_dirty(tmp_path):
    """In a git repo with uncommitted changes, git_diff passes."""
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "file.txt").write_text("hello")

    from forgent.verify import Verifier
    r = Verifier().run(tmp_path, subset=["git_diff"])
    assert len(r.ran) == 1
    assert r.ran[0].name == "git_diff"
    assert r.ran[0].status == "pass"


# ---------------------------------------------------------------------------
# embeddings
# ---------------------------------------------------------------------------


def test_embedding_pack_unpack_roundtrip():
    from forgent.embeddings import pack_vector, unpack_vector
    vec = [0.1, -0.2, 0.7, 1.5, -3.14]
    blob = pack_vector(vec)
    back = unpack_vector(blob)
    assert len(back) == len(vec)
    for a, b in zip(vec, back):
        assert abs(a - b[0] if isinstance(b, tuple) else a - b) < 1e-5


def test_cosine_similarity_edges():
    from forgent.embeddings import cosine_similarity
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_recall_auto_mode_falls_back_to_bm25_without_embeddings(tmp_path, monkeypatch):
    """Without FORGENT_EMBED_MODEL set, auto mode picks bm25."""
    monkeypatch.delenv("FORGENT_EMBED_MODEL", raising=False)
    mem = MemoryStore(tmp_path / "m.db")
    sid = mem.start_session("seed")
    mem.remember("Stripe webhook handler with idempotency", MemoryType.AGENT_OUTPUT, session_id=sid)
    mem.remember("unrelated text about frogs", MemoryType.AGENT_OUTPUT, session_id=sid)

    hits = mem.recall("stripe webhook", limit=3)
    assert any("Stripe" in r.content for r in hits)


# ---------------------------------------------------------------------------
# IDE setup
# ---------------------------------------------------------------------------


def test_ide_snippets_are_valid():
    from forgent.ide_setup import snippet_for
    import json as _json
    # Every JSON-formatted snippet must parse.
    for editor in ("claude-desktop", "cursor", "cline", "roo", "zed"):
        snip = snippet_for(editor)
        if snip.format == "json":
            parsed = _json.loads(snip.snippet)
            assert isinstance(parsed, dict)


def test_ide_rejects_unknown():
    from forgent.ide_setup import snippet_for
    with pytest.raises(ValueError):
        snippet_for("notepad")


# ---------------------------------------------------------------------------
# MemoryStore schema migration (backward compat)
# ---------------------------------------------------------------------------


def test_memory_migration_adds_embedding_and_team_columns(tmp_path):
    """Simulate a v0.3 DB: create a DB with only the old columns, reopen.

    The migration in __init__ must add `embedding` + `team_id` without
    dropping data.
    """
    import sqlite3
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, task TEXT, created_at REAL,
                               status TEXT, metadata TEXT);
        CREATE TABLE memories (id TEXT PRIMARY KEY, session_id TEXT, type TEXT,
                               content TEXT, tags TEXT, source TEXT,
                               created_at REAL);
        """
    )
    conn.execute(
        "INSERT INTO memories(id, session_id, type, content, tags, source, created_at) VALUES (?,?,?,?,?,?,?)",
        ("abc", None, "note", "old-row", "", None, 0.0),
    )
    conn.commit()
    conn.close()

    # Reopen via MemoryStore -> should migrate cleanly.
    mem = MemoryStore(db_path)
    # Old row still there.
    rows = mem._conn.execute("SELECT id, content, embedding, team_id FROM memories").fetchall()
    assert len(rows) == 1
    assert rows[0]["content"] == "old-row"
    assert rows[0]["embedding"] is None
    assert rows[0]["team_id"] is None
