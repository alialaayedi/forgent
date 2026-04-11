"""Minimal end-to-end example.

Ask forgent to plan a task. The router picks a knowledge pack, the planner
synthesizes steps/gotchas/success criteria, and memory is persisted. The
host (you, or a coding agent) executes the plan with its own tools.

Requires:
    pip install forgent
    export ANTHROPIC_API_KEY=sk-ant-...   # optional -- heuristic plan if omitted

Run:
    python examples/01_quickstart.py
"""

from forgent import Orchestrator


def main() -> None:
    # Per-project memory: each working directory gets its own ./forgent.db.
    # Set FORGENT_DB or pass db_path to control this.
    orch = Orchestrator()

    plan = orch.advise(
        "Review this Python snippet for security issues:\n\n"
        "    def login(username, password):\n"
        "        query = f\"SELECT * FROM users WHERE name='{username}' AND pw='{password}'\"\n"
        "        return db.execute(query)"
    )

    print("=" * 60)
    print(f"Knowledge pack: {plan.primary_agent}")
    print(f"Confidence:     {plan.confidence:.2f}")
    print(f"Reasoning:      {plan.routing_reasoning}")
    print("=" * 60)
    print("Steps:")
    for i, step in enumerate(plan.steps, 1):
        print(f"  {i}. {step}")
    print()
    print("Gotchas:")
    for g in plan.gotchas:
        print(f"  - {g}")
    print()
    print("Success criteria:")
    for c in plan.success_criteria:
        print(f"  - {c}")
    print("=" * 60)
    print(f"Session: {plan.session_id}")
    print("After executing the plan, call:")
    print(
        f"    orch.record_outcome(session_id={plan.session_id[:8]!r}, "
        f"success=True, agent_name={plan.primary_agent!r})"
    )


if __name__ == "__main__":
    main()
