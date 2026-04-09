"""Show how the memory store learns from past sessions.

Forgent persists every task, routing decision, and agent output. On the next
related task, it pulls the relevant prior context into the new prompt
automatically. This example simulates two related tasks and shows how the
memory store recalls relevant entries.

Requires:
    pip install forgent
    (no API key needed for this example — uses the heuristic router)

Run:
    python examples/03_memory_recall.py
"""

from forgent.memory import MemoryStore, MemoryType


def main() -> None:
    mem = MemoryStore("/tmp/forgent-recall-demo.db")

    # Simulate a past session
    sid = mem.start_session("design a Stripe webhook handler with idempotency")
    mem.remember(
        "Used payment-integration agent. Recommended HMAC verification + "
        "idempotency keys stored in Redis with 24h TTL.",
        MemoryType.ROUTING,
        session_id=sid,
        tags=["stripe", "webhook"],
    )
    mem.remember(
        "Implementation: signed_payload = sig + '.' + body; verify HMAC "
        "with the webhook signing secret; store event_id in Redis SETNX.",
        MemoryType.AGENT_OUTPUT,
        session_id=sid,
        tags=["stripe", "hmac", "redis"],
    )
    mem.close_session(sid)

    # Now: a new related task. context_for() pulls the relevant past memories.
    new_task = "add a refund handler to my Stripe integration"
    context = mem.context_for(new_task, k=3)

    print("New task:")
    print(f"  {new_task}")
    print()
    print("What the memory store recalls as context:")
    print("-" * 60)
    print(context or "(nothing yet)")
    print("-" * 60)
    print()

    # You can also recall by type
    routing_history = mem.recall("stripe", limit=5, type=MemoryType.ROUTING)
    print(f"Past routing decisions for 'stripe': {len(routing_history)}")
    for entry in routing_history:
        print(f"  - {entry.content[:80]}...")

    # And ask for stats
    print()
    print("Memory store stats:")
    for type_, count in mem.stats().items():
        print(f"  {type_}: {count}")


if __name__ == "__main__":
    main()
