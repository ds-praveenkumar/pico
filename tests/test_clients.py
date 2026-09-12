"""Tests for the brain LLM clients (mocked OpenAI layer, no network)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agents.base_agent import openai_tool_schemas
from brain.base_llm import BaseLLM
from brain.cerebras_client import CerebrasClient
from brain.nvidia_client import NvidiaClient
from brain.openai_client import OpenAIClient


def _fake_response(content="hi"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))])


def _attach_mock(client):
    fake = MagicMock()
    fake.chat.completions.create.return_value = _fake_response()
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