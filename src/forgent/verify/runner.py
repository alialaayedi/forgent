"""Verifier runner -- detectors + aggregator.

Detectors are small, self-contained Python functions of shape:

    def detect(cwd: Path) -> VerifyResult

They must:
  - Return quickly (< 60s worst case; most return in < 5s)
  - Never raise; wrap all subprocess errors in VerifyResult(status="unknown")
  - Be stateless -- no module-level caches that cross invocations

The aggregator runs all registered detectors in a thread pool and builds
an AggregateResult. Conjunction semantics: "pass" only if every detector
that RAN (status != unknown) passed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class VerifyResult:
    """One detector's verdict."""

    name: str
    status: str  # "pass" | "fail" | "unknown"
    evidence: str = ""  # short human-readable explanation
    duration_ms: int = 0


@dataclass
class AggregateResult:
    """Combined verdict across all detectors."""

    success: bool
    ran: list[VerifyResult] = field(default_factory=list)
    skipped: list[VerifyResult] = field(default_factory=list)  # status=="unknown"

    def to_summary(self) -> str:
        """One-line human-readable summary."""
        passed = sum(1 for r in self.ran if r.status == "pass")
        failed = sum(1 for r in self.ran if r.status == "fail")
        skipped = len(self.skipped)
        return f"verify: {passed} pass / {failed} fail / {skipped} skip"


# --------------------------------------------------------------------------- helpers


def _run(cmd: list[str], cwd: Path, timeout: int = 30) -> tuple[int, str, str]:
    """Run a subprocess; return (returncode, stdout, stderr). Never raises."""
    try:
        res = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        return res.returncode, res.stdout or "", res.stderr or ""
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as exc:
        return -1, "", str(exc)


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


# --------------------------------------------------------------------------- detectors


def _detect_git_diff(cwd: Path) -> VerifyResult:
    """Pass if the working tree shows any changed files."""
    import time
    t0 = time.monotonic()
    if not (cwd / ".git").exists() and not _has("git"):
        return VerifyResult("git_diff", "unknown", "not a git repo")
    rc, out, err = _run(["git", "status", "--porcelain=v1"], cwd)
    dt = int((time.monotonic() - t0) * 1000)
    if rc != 0:
        return VerifyResult("git_diff", "unknown", f"git failed: {err[:200]}", dt)
    if out.strip():
        files = [line.split()[-1] for line in out.splitlines() if line.strip()]
        return VerifyResult(
            "git_diff", "pass",
            f"{len(files)} file(s) changed: {', '.join(files[:5])}", dt,
        )
    return VerifyResult("git_diff", "fail", "no files changed", dt)


def _detect_tests(cwd: Path) -> VerifyResult:
    """Run the project's test suite with a short timeout."""
    import time
    t0 = time.monotonic()
    cmd = _pick_test_cmd(cwd)
    if cmd is None:
        return VerifyResult("tests", "unknown", "no test runner detected")
    rc, out, err = _run(cmd, cwd, timeout=120)
    dt = int((time.monotonic() - t0) * 1000)
    if rc == 0:
        # Many test frameworks print summaries; grab the last ~100 chars.
        tail = (out + err).strip().splitlines()[-1:] if (out or err) else []
        return VerifyResult(
            "tests", "pass", tail[0] if tail else "exit 0", dt,
        )
    tail = (err or out).strip().splitlines()[-1:] if (out or err) else []
    return VerifyResult("tests", "fail", tail[0] if tail else f"exit {rc}", dt)


def _pick_test_cmd(cwd: Path) -> list[str] | None:
    """Pick a reasonable test command for the project language.

    Order: pyproject/pytest > package.json/npm > Cargo.toml > go.mod. Only
    returns a command if the tool is on PATH.
    """
    if (cwd / "pyproject.toml").exists() or (cwd / "pytest.ini").exists() or (cwd / "tests").is_dir():
        if _has("pytest"):
            return ["pytest", "-x", "--tb=no", "-q"]
    if (cwd / "package.json").exists():
        if _has("npm"):
            return ["npm", "test", "--silent"]
    if (cwd / "Cargo.toml").exists():
        if _has("cargo"):
            return ["cargo", "test", "--quiet"]
    if (cwd / "go.mod").exists():
        if _has("go"):
            return ["go", "test", "./..."]
    return None


def _detect_lint(cwd: Path) -> VerifyResult:
    """Run the project's linter."""
    import time
    t0 = time.monotonic()
    cmd = _pick_lint_cmd(cwd)
    if cmd is None:
        return VerifyResult("lint", "unknown", "no linter detected")
    rc, out, err = _run(cmd, cwd, timeout=60)
    dt = int((time.monotonic() - t0) * 1000)
    if rc == 0:
        return VerifyResult("lint", "pass", "no lint issues", dt)
    tail = (out + err).strip().splitlines()[-1:] if (out or err) else []
    return VerifyResult("lint", "fail", tail[0] if tail else f"exit {rc}", dt)


def _pick_lint_cmd(cwd: Path) -> list[str] | None:
    if (cwd / "pyproject.toml").exists() or (cwd / "setup.py").exists():
        if _has("ruff"):
            return ["ruff", "check", "."]
    if (cwd / "package.json").exists():
        if _has("biome"):
            return ["biome", "check", "."]
        if _has("eslint"):
            return ["eslint", ".", "--max-warnings", "0"]
    if (cwd / "Cargo.toml").exists():
        if _has("cargo"):
            return ["cargo", "clippy", "--quiet", "--", "-D", "warnings"]
    return None


def _detect_ci(cwd: Path) -> VerifyResult:
    """Check the latest GitHub Actions run status via gh, if available."""
    import time
    t0 = time.monotonic()
    if not _has("gh"):
        return VerifyResult("ci", "unknown", "gh CLI not installed")
    rc, out, err = _run(["gh", "run", "list", "--limit", "1", "--json", "status,conclusion"], cwd)
    dt = int((time.monotonic() - t0) * 1000)
    if rc != 0:
        return VerifyResult("ci", "unknown", f"gh failed: {err[:120]}", dt)
    import json
    try:
        runs = json.loads(out)
    except json.JSONDecodeError:
        return VerifyResult("ci", "unknown", "gh returned invalid JSON", dt)
    if not runs:
        return VerifyResult("ci", "unknown", "no CI runs found", dt)
    run = runs[0]
    status = run.get("status")
    conclusion = run.get("conclusion")
    if status != "completed":
        return VerifyResult("ci", "unknown", f"latest run still {status}", dt)
    if conclusion == "success":
        return VerifyResult("ci", "pass", "latest run succeeded", dt)
    return VerifyResult("ci", "fail", f"conclusion: {conclusion}", dt)


# --------------------------------------------------------------------------- verifier


_DETECTORS: dict[str, Callable[[Path], VerifyResult]] = {
    "git_diff": _detect_git_diff,
    "tests": _detect_tests,
    "lint": _detect_lint,
    "ci": _detect_ci,
}


class Verifier:
    """Dispatches detectors in parallel; combines verdicts."""

    def __init__(self, detectors: dict[str, Callable[[Path], VerifyResult]] | None = None):
        self.detectors = dict(detectors or _DETECTORS)

    def run(self, cwd: Path | str, subset: list[str] | None = None) -> AggregateResult:
        """Run all (or subset of) detectors. Conjunction over what actually ran."""
        path = Path(cwd)
        names = subset or list(self.detectors.keys())
        results: list[VerifyResult] = []
        with ThreadPoolExecutor(max_workers=min(4, len(names) or 1)) as pool:
            futures = {
                pool.submit(self.detectors[n], path): n
                for n in names
                if n in self.detectors
            }
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as exc:  # defensive -- never raise
                    results.append(
                        VerifyResult(futures[fut], "unknown", f"detector crashed: {exc}")
                    )
        ran = [r for r in results if r.status != "unknown"]
        skipped = [r for r in results if r.status == "unknown"]
        success = bool(ran) and all(r.status == "pass" for r in ran)
        return AggregateResult(success=success, ran=ran, skipped=skipped)
