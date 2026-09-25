"""Shared Anthropic plumbing for the router, planner, and forge.

One place owns the model defaults and the request shape so the three LLM
call sites cannot drift apart again. Every call asks for JSON through
structured outputs (`output_config.format`) instead of forcing a tool call:
forced `tool_choice` returns a 400 on Claude Opus 5.5 and newer models,
while structured outputs work on every current model.

Defaults (override per role with env vars):

    FORGENT_ROUTER_MODEL   claude-sonnet-5     FORGENT_ROUTER_EFFORT   low
    FORGENT_PLANNER_MODEL  claude-opus-5-5     FORGENT_PLANNER_EFFORT  medium
    FORGENT_FORGE_MODEL    claude-opus-5-5     FORGENT_FORGE_EFFORT    high
    FORGENT_FALLBACK_MODEL claude-opus-5       (retried once on a refusal)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

ROUTER_MODEL_DEFAULT = "claude-sonnet-5"
PLANNER_MODEL_DEFAULT = "claude-opus-5-5"
FORGE_MODEL_DEFAULT = "claude-opus-5-5"
FALLBACK_MODEL_DEFAULT = "claude-opus-5"

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class Role:
    """Model + effort settings for one LLM call site."""

    name: str
    model_env: str
    model_default: str
    effort_env: str
    effort_default: str

    def model(self, override: str | None = None) -> str:
        return override or os.environ.get(self.model_env) or self.model_default

    def effort(self) -> str:
        val = (os.environ.get(self.effort_env) or self.effort_default).strip().lower()
        return val if val in EFFORT_LEVELS else self.effort_default


ROUTER = Role("router", "FORGENT_ROUTER_MODEL", ROUTER_MODEL_DEFAULT, "FORGENT_ROUTER_EFFORT", "low")
PLANNER = Role("planner", "FORGENT_PLANNER_MODEL", PLANNER_MODEL_DEFAULT, "FORGENT_PLANNER_EFFORT", "medium")
FORGE = Role("forge", "FORGENT_FORGE_MODEL", FORGE_MODEL_DEFAULT, "FORGENT_FORGE_EFFORT", "high")


class LLMRefusal(RuntimeError):
    """The model declined the request (stop_reason == "refusal")."""


def make_client(api_key: str | None = None) -> Any | None:
    """Build an Anthropic client, or None when no credentials are configured.

    Callers treat None as "run the deterministic heuristic path".
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic  # noqa: WPS433

        return anthropic.Anthropic(api_key=key)
    except Exception:
        return None


def supports_effort(model: str) -> bool:
    """`output_config.effort` errors on Haiku 4.5, Sonnet 4.5, and older models."""
    legacy = ("claude-haiku-", "claude-sonnet-4-5", "claude-opus-4-1", "claude-opus-4-0", "claude-3")
    return not model.startswith(legacy)


def strict_schema(schema: dict) -> dict:
    """Make a JSON schema acceptable to structured outputs.

    Structured outputs require `additionalProperties: false` on every object
    and reject numeric/length/array-size constraints. The schemas in this
    package are written for readability; this normalizes them.
    """
    dropped = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
               "multipleOf", "minLength", "maxLength", "maxItems", "minItems"}

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items() if k not in dropped}
            if out.get("type") == "object":
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def structured_call(
    client: Any,
    *,
    role: Role,
    model: str,
    system: str,
    user: str,
    schema: dict,
    max_tokens: int,
) -> dict:
    """Run one request constrained to `schema` and return the parsed JSON.

    Thinking stays at each model's default (adaptive on the Claude 5 family,
    where it cannot be disabled on Opus 5.5); effort is the depth control.
    On a refusal the request is retried once on FORGENT_FALLBACK_MODEL; if
    that also declines, LLMRefusal is raised and the caller falls back to
    its heuristic path.
    """
    fallback = os.environ.get("FORGENT_FALLBACK_MODEL", FALLBACK_MODEL_DEFAULT)
    models = [model] + ([fallback] if fallback and fallback != model else [])
    last_refusal: LLMRefusal | None = None
    for candidate in models:
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": strict_schema(schema)},
        }
        if supports_effort(candidate):
            output_config["effort"] = role.effort()
        resp = client.messages.create(
            model=candidate,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
        )
        stop = getattr(resp, "stop_reason", None)
        if stop == "refusal":
            details = getattr(resp, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            last_refusal = LLMRefusal(f"{candidate} declined ({category or 'unspecified'})")
            if category == "reasoning_extraction":
                break  # not retried on another model
            continue
        if stop == "max_tokens":
            raise RuntimeError(f"{role.name}: {candidate} hit max_tokens={max_tokens}")
        text = next(
            (b.text for b in resp.content if getattr(b, "type", None) == "text"), ""
        )
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"{role.name}: expected a JSON object, got {type(data).__name__}")
        return data
    raise last_refusal or LLMRefusal(f"{role.name}: request declined")
