"""Forge a brand-new knowledge pack on demand.

When the curated catalog doesn't cover a task class you care about, ask the
forge to design a specialist for it. The new pack gets a full system prompt,
capabilities, and a category — and is persisted so it's reused next time.

Requires:
    pip install forgent
    export ANTHROPIC_API_KEY=sk-ant-...

Run:
    python examples/02_forge_specialist.py
"""

import asyncio

from forgent import Orchestrator


async def main() -> None:
    orch = Orchestrator()

    # Step 1: forge the specialist explicitly. The LLM picks a name and writes
    # a 400+ word knowledge pack tailored to this task class.
    forged = await orch.forge_agent(
        task=(
            "Design RFC-compliant SAML 2.0 SSO integrations with enterprise "
            "identity providers (Okta, Azure AD, Google Workspace), including "
            "metadata exchange, attribute mapping, and SP-initiated and "
            "IdP-initiated flows."
        ),
    )
    print(f"Forged knowledge pack: {forged.spec.name}")
    print(f"  capabilities: {', '.join(forged.spec.capabilities)}")
    print(f"  category:     {forged.spec.category}")
    print(f"  is_new:       {forged.is_new}")
    print()
    print(forged.body[:600] + "...")
    print()

    # Step 2: plan an actual SAML task. The router will now see the new pack
    # in the registry and route to it.
    plan = await orch.advise_async(
        "Walk me through implementing SP-initiated SSO with Okta as the IdP. "
        "I need the metadata XML, the redirect URL, and how to validate the "
        "signed assertion server-side in Python."
    )
    print("=" * 60)
    print(f"Routed to:      {plan.primary_agent}")
    print(f"Reasoning:      {plan.routing_reasoning}")
    print("=" * 60)
    print("Knowledge synthesis:")
    print(f"  {plan.knowledge_pack_summary}")
    print()
    print("Steps:")
    for i, step in enumerate(plan.steps, 1):
        print(f"  {i}. {step}")


if __name__ == "__main__":
    asyncio.run(main())
