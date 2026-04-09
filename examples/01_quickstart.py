"""Minimal end-to-end example.

Run a single task through the orchestrator. The router picks an agent from
the curated catalog, the matching adapter executes it, and everything is
persisted to the local memory store.

Requires:
    pip install forgent
    export ANTHROPIC_API_KEY=sk-ant-...

Run:
    python examples/01_quickstart.py
"""

from forgent import Orchestrator


def main() -> None:
    # Per-project memory: each working directory gets its own ./forgent.db.
    # Set ORCHESTRATOR_DB or pass db_path to control this.
    orch = Orchestrator()

    result = orch.run(
        "Review this Python snippet for security issues:\n\n"
        "    def login(username, password):\n"
        "        query = f\"SELECT * FROM users WHERE name='{username}' AND pw='{password}'\"\n"
        "        return db.execute(query)"
    )

    print("=" * 60)
    print(f"Routed to: {result.decision.primary}")
    print(f"Mode:      {result.decision.mode}")
    print(f"Reasoning: {result.decision.reasoning}")
    print("=" * 60)
    print(result.output)
    print("=" * 60)
    print(f"Session: {result.session_id}")
    print(f"Success: {result.success}")


if __name__ == "__main__":
    main()
