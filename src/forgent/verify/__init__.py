"""Outcome verification -- evidence-backed `success: bool`.

The v0.4 gap analysis flagged forgent's `report_outcome(success=True)` as
noisy: competitors (Claude Flow, Task Master) also ship self-reported
outcomes; none verify. forgent's edge is outcome-aware memory -- it only
pays off if the outcomes are trustworthy.

Each detector runs independently and returns a `VerifyResult` (pass/fail/
unknown + evidence). The aggregator runs them in parallel and produces a
single verdict by conjunction: all detectors that *ran* must pass. A
detector that can't run (no test runner, not a git repo) returns
`status="unknown"` and is excluded from the conjunction rather than
dragging the outcome down.

Detectors:
    git_diff   Did touched files actually change? Guards against ghost
               commits.
    tests      pytest / npm test / cargo test / go test. Runs with a
               short timeout; records pass/fail.
    lint       ruff / eslint / biome / cargo clippy. Same shape.
    ci         `gh run view --json status` if gh CLI is auth'd.

Invoke via Verifier.run(cwd) -> AggregateResult.
"""

from forgent.verify.runner import (
    AggregateResult,
    VerifyResult,
    Verifier,
)

__all__ = ["AggregateResult", "VerifyResult", "Verifier"]
