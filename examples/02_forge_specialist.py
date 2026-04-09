"""Forge a brand-new specialist subagent on demand.

When the curated catalog doesn't cover a task class you care about, ask the
forge to design a specialist for it. The new agent gets a full system prompt,
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
    # a 400+ word system prompt tailored to this task class.
    forged = await orch.forge_agent(
        task=(
            "Design RFC-compliant SAML 2.0 SSO integrations with enterprise "
            "identity providers (Okta, Azure AD, Google Workspace), including "
            "metadata exchange, attribute mapping, and SP-initiated and "
            "IdP-initiated flows."
        ),
    )
    print(f"Forged agent: {forged.spec.name}")
    print(f"  capabilities: {', '.join(forged.spec.capabilities)}")
    print(f"  category:     {forged.spec.category}")
    print(f"  is_new:       {forged.is_new}")
    print()
    print(forged.body[:600] + "...")
    print()

    # Step 2: now run an actual SAML task. The router will see the new agent
    # in the registry and pick it.
    result = await orch.run_async(
        "Walk me through implementing SP-initiated SSO with Okta as the IdP. "
        "I need the metadata XML, the redirect URL, and how to validate the "
        "signed assertion server-side in Python."
    )
    print("=" * 60)
    print(f"Routed to: {result.decision.primary}")
    print(f"Reasoning: {result.decision.reasoning}")
    print("=" * 60)
    print(result.output)


if __name__ == "__main__":
    asyncio.run(main())
