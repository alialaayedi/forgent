# Security policy

## Supported versions

forgent is at v0.1.0. Until we cut a v1.0.0, only the latest minor version
receives security fixes. Older versions are not patched.

| Version | Supported |
|---|---|
| 0.1.x   | ✅ |
| < 0.1.0 | ❌ |

## Reporting a vulnerability

**Please do not file public GitHub issues for security problems.**

If you find a vulnerability, email **ali.alaayedi@gmail.com** with:

- A clear description of the issue and the impact
- Steps to reproduce (a minimal proof-of-concept is ideal)
- The affected version(s)
- Your suggested fix, if you have one

You'll get an acknowledgement within **72 hours** and a status update within
**7 days**. Validated issues will be fixed in a patch release within 30 days
of validation, and the reporter is credited (unless you'd rather stay
anonymous).

For now this is a single-maintainer project, so response times will reflect
that. If you don't hear back within 7 days, feel free to follow up.

## Scope

forgent is a meta-orchestrator that routes tasks to LLM-backed agents. The
relevant attack surface includes:

| Area | What we care about |
|---|---|
| **API key handling** | We read `ANTHROPIC_API_KEY` from the environment and never log it, never write it to the memory store, never include it in agent prompts. Reports of leakage in any form are high-priority. |
| **Memory store** | The SQLite database stores task text and agent outputs in plaintext. By design — see "What is *not* in scope" below. We *do* care about path traversal in `FORGENT_DB`, SQL injection in the recall query (FTS5 should be parameterized), and any way an attacker can read another user's `.db` file. |
| **MCP server** | The stdio MCP server exposes 8 tools to whatever client launches it. We care about prompt injection via tool inputs that could exfiltrate data, denial-of-service via large/recursive payloads, and any tool that performs writes the calling Claude session didn't authorize. |
| **AgentForge prompt injection** | The forge writes new agent system prompts to disk based on user input. We care about path traversal in agent names, code injection in YAML frontmatter, and anything that lets a forged agent escape its sandbox. |
| **Vendored agent files** | The 60+ markdown agent definitions are vendored from public MIT/Apache repos. If you find a malicious or backdoored prompt in any of them, please report it — we'll remove the file and notify upstream. |
| **Dependency CVEs** | We pin only loose floors (`anthropic>=0.40.0`, etc.). High-severity CVEs in any direct dependency are in scope. |

## What is *not* in scope

- **Plaintext storage of task content in the memory store**. This is by
  design — the memory store is local to the user's machine and is intended
  to be a long-lived knowledge base. If you need encrypted-at-rest, run
  forgent on a FileVault/LUKS-encrypted disk.
- **Cost-related issues**. Forgent calls the Anthropic API, which costs
  money. Behavior that uses tokens efficiently is a *quality* issue, not a
  security issue.
- **LLM hallucinations or unsafe outputs from the underlying model**. Those
  are upstream concerns for Anthropic, not forgent. Forgent does not add a
  safety layer on top of Claude.
- **Anything in the `sources/` directory**. That's a build-time input only;
  it's not loaded at runtime.

## Disclosure timeline

1. **Day 0** — you report
2. **Day 0–3** — acknowledgement
3. **Day 3–7** — initial assessment + status update
4. **Day 7–30** — fix developed, tested, released
5. **Day 30+** — public disclosure (CVE if applicable, credit in CHANGELOG)

We follow [coordinated disclosure](https://en.wikipedia.org/wiki/Coordinated_disclosure)
and ask reporters to do the same — please don't post details publicly until
the fix is shipped.

## Hall of fame

Reporters of validated vulnerabilities are listed here (with permission).

*(empty for now)*
