"""Tests for the brain LLM clients (mocked OpenAI layer, no network)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agents.base_agent import openai_tool_schemas
from brain.base_llm import BaseLLM, _coerce_usage
from brain.cerebras_client import CerebrasClient
from brain.nvidia_client import NvidiaClient
from brain.openai_client import OpenAIClient


def _fake_response(content="hi", usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
        usage=usage,
    )


def _attach_mock(client, usage=None):
    fake = MagicMock()
    fake.chat.completions.create.return_value = _fake_response(usage=usage)
    client.client = fake
    return fake


def test_base_llm_is_abstract():
    with pytest.raises(TypeError):
        BaseLLM(provider="openai", model_name="model")  # type: ignore[abstract]


def test_openai_tool_schemas_shape():
    schemas = openai_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert {"bash", "file_read", "file_write", "skill_read"}.issubset(names)
    first = schemas[0]
    assert first["type"] == "function"
    assert first["function"]["parameters"]["type"] == "object"


def test_openai_client_generate():
    client = OpenAIClient(provider="openai", model_name="m", api_key="k")
    fake = _attach_mock(client)
    message = client.generate([{"role": "user", "content": "hi"}])
    assert message.content == "hi"
    assert fake.chat.completions.create.called


def test_nvidia_client_generate():
    client = NvidiaClient(provider="nvidia", model_name="m", api_key="k", base_url="https://api.nv.com/v1")
    fake = _attach_mock(client)
    message = client.generate([{"role": "user", "content": "hi"}])
    assert message.content == "hi"
    assert fake.chat.completions.create.called


def test_cerebras_client_generate():
    client = CerebrasClient(provider="cerebras", model_name="m", api_key="k", base_url="https://api.cb.com/v1")
    fake = _attach_mock(client)
    message = client.generate([{"role": "user", "content": "hi"}])
    assert message.content == "hi"
    assert fake.chat.completions.create.called


def test_cerebras_requires_api_key():
    with pytest.raises(ValueError, match="CEREBRAS_API_KEY"):
        CerebrasClient(provider="cerebras", model_name="m", api_key=None)


def test_generate_forwards_tools_when_set():
    client = OpenAIClient(provider="openai", model_name="m", api_key="k", tools=[{"type": "function"}])
    fake = _attach_mock(client)
    client.generate([{"role": "user", "content": "hi"}])
    kwargs = fake.chat.completions.create.call_args.kwargs
    assert kwargs["tools"] == [{"type": "function"}]


def test_openai_client_records_usage():
    client = OpenAIClient(provider="openai", model_name="m", api_key="k")
    _attach_mock(
        client,
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3, total_tokens=15),
    )
    client.generate([{"role": "user", "content": "hi"}])
    assert client.usage == {"prompt": 12, "completion": 3, "total": 15}
    assert client.last_generation == {"prompt": 12, "completion": 3, "total": 15}


def test_usage_without_payload_stays_zero():
    client = NvidiaClient(provider="nvidia", model_name="m", api_key="k", base_url="https://api.nv.com/v1")
    _attach_mock(client)
    client.generate([{"role": "user", "content": "hi"}])
    assert client.usage["total"] == 0
    assert client.last_generation["total"] == 0


def test_cumulative_usage_across_calls():
    client = NvidiaClient(provider="nvidia", model_name="m", api_key="k", base_url="https://api.nv.com/v1")
    _attach_mock(
        client,
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    client.generate([{"role": "user", "content": "hi"}])
    _attach_mock(
        client,
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3),
    )
    client.generate([{"role": "user", "content": "there"}])
    assert client.usage == {"prompt": 12, "completion": 6, "total": 18}


def test_coerce_usage_from_object():
    assert _coerce_usage(SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3)) == {
        "prompt": 1,
        "completion": 2,
        "total": 3,
    }


def test_coerce_usage_from_dict():
    assert _coerce_usage({"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}) == {
        "prompt": 5,
        "completion": 5,
        "total": 10,
    }


def test_coerce_usage_none():
    assert _coerce_usage(None) == {"prompt": 0, "completion": 0, "total": 0}