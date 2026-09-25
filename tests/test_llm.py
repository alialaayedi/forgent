"""Tests for forgent.llm -- the shared structured-output call.

Uses a fake client, so no API key or network is needed. Checks the request
shape the Claude 5 models require (structured outputs, no forced tool_choice,
effort only where supported) and the refusal fallback.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from forgent import llm
from forgent.registry.loader import Registry
from forgent.router.router import Router


class FakeMessages:
    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses: list):
        self.messages = FakeMessages(responses)


def _resp(payload: dict | None = None, stop: str = "end_turn", category: str | None = None):
    content = [SimpleNamespace(type="thinking", thinking="")]
    if payload is not None:
        content.append(SimpleNamespace(type="text", text=json.dumps(payload)))
    details = SimpleNamespace(category=category) if stop == "refusal" else None
    return SimpleNamespace(stop_reason=stop, stop_details=details, content=content)


SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "items": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "object", "properties": {"name": {"type": "string"}}},
        },
    },
    "required": ["score", "items"],
}


def test_defaults_are_current_models(monkeypatch):
    for var in ("FORGENT_ROUTER_MODEL", "FORGENT_PLANNER_MODEL", "FORGENT_FORGE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    assert llm.ROUTER.model() == "claude-sonnet-5"
    assert llm.PLANNER.model() == "claude-opus-5-5"
    assert llm.FORGE.model() == "claude-opus-5-5"


def test_env_overrides_model_and_effort(monkeypatch):
    monkeypatch.setenv("FORGENT_PLANNER_MODEL", "claude-opus-5")
    monkeypatch.setenv("FORGENT_PLANNER_EFFORT", "xhigh")
    assert llm.PLANNER.model() == "claude-opus-5"
    assert llm.PLANNER.effort() == "xhigh"
    monkeypatch.setenv("FORGENT_PLANNER_EFFORT", "turbo")  # invalid -> default
    assert llm.PLANNER.effort() == "medium"


def test_strict_schema_normalizes_for_structured_outputs():
    out = llm.strict_schema(SCHEMA)
    assert out["additionalProperties"] is False
    assert out["properties"]["items"]["items"]["additionalProperties"] is False
    assert "minimum" not in out["properties"]["score"]
    assert "maxItems" not in out["properties"]["items"]


def test_structured_call_request_shape():
    client = FakeClient([_resp({"score": 0.9, "items": []})])
    data = llm.structured_call(
        client, role=llm.PLANNER, model="claude-opus-5-5",
        system="sys", user="task", schema=SCHEMA, max_tokens=16000,
    )
    assert data == {"score": 0.9, "items": []}
    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert "tool_choice" not in call and "tools" not in call
    assert "thinking" not in call  # Opus 5.5 rejects disabling thinking
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == "medium"


def test_effort_omitted_for_haiku():
    client = FakeClient([_resp({"score": 1, "items": []})])
    llm.structured_call(
        client, role=llm.ROUTER, model="claude-haiku-4-5",
        system="s", user="u", schema=SCHEMA, max_tokens=1000,
    )
    assert "effort" not in client.messages.calls[0]["output_config"]


def test_refusal_retries_on_fallback_model(monkeypatch):
    monkeypatch.delenv("FORGENT_FALLBACK_MODEL", raising=False)
    client = FakeClient([_resp(stop="refusal", category="cyber"), _resp({"score": 0.5, "items": []})])
    data = llm.structured_call(
        client, role=llm.PLANNER, model="claude-opus-5-5",
        system="s", user="u", schema=SCHEMA, max_tokens=1000,
    )
    assert data["score"] == 0.5
    assert [c["model"] for c in client.messages.calls] == ["claude-opus-5-5", "claude-opus-5"]


def test_reasoning_extraction_refusal_is_not_retried():
    client = FakeClient([_resp(stop="refusal", category="reasoning_extraction")])
    with pytest.raises(llm.LLMRefusal):
        llm.structured_call(
            client, role=llm.PLANNER, model="claude-opus-5-5",
            system="s", user="u", schema=SCHEMA, max_tokens=1000,
        )
    assert len(client.messages.calls) == 1


def test_router_parses_structured_output_and_clamps_scores():
    reg = Registry.load()
    router = Router(registry=reg)
    router._client = FakeClient([_resp({
        "primary": "python-pro",
        "supporting": ["not-a-real-agent"],
        "mode": "single",
        "reasoning": "python task",
        "confidence": 1.7,
        "alternates": [{"name": "debugger", "score": -2, "reasoning": "close"}],
    })])
    decision = router.route("fix a python bug")
    assert decision.primary == "python-pro"
    assert decision.supporting == []  # hallucinated name dropped
    assert decision.confidence == 1.0
    assert decision.alternates[0].score == 0.0
