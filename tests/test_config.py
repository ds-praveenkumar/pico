"""Tests for brain.config (configurable master name)."""

import pytest

from brain.config import DEFAULT_MASTER_NAME, master_name, personalize


def test_master_name_defaults_to_praveen():
    assert DEFAULT_MASTER_NAME == "Praveen"
    assert master_name() == DEFAULT_MASTER_NAME


def test_master_name_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PICO_MASTER_NAME", "Kumar")
    assert master_name() == "Kumar"


def test_master_name_blank_env_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PICO_MASTER_NAME", "   ")
    assert master_name() == DEFAULT_MASTER_NAME


def test_personalize_replaces_all_placeholders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PICO_MASTER_NAME", "Kumar")
    assert personalize("for {master} and {master} only") == "for Kumar and Kumar only"


def test_system_prompt_uses_configured_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from agents.pico import _read_system_prompt

    monkeypatch.setenv("PICO_MASTER_NAME", "Kumar")
    prompt = _read_system_prompt()
    assert "{master}" not in prompt
    assert "Kumar" in prompt


def test_executor_system_instructions_uses_configured_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from agents.executor import _EXECUTOR_SYSTEM_PROMPT

    monkeypatch.setenv("PICO_MASTER_NAME", "Kumar")
    assert "Kumar" in personalize(_EXECUTOR_SYSTEM_PROMPT)


def test_ask_master_description_uses_configured_name(monkeypatch: pytest.MonkeyPatch) -> None:
    import agents.tools as tools

    monkeypatch.setenv("PICO_MASTER_NAME", "Kumar")
    description = tools._ask_master_description()
    assert "Kumar" in description
    assert "{master}" not in description


def test_read_skill_personalizes_content(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    from agents.tools import skill_read
    from pathlib import Path

    monkeypatch.setenv("PICO_MASTER_NAME", "Kumar")
    skill_dir = Path(skill_read.SKILLS_DIR) / "personalize-check"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yml").write_text("serves {master}\n", encoding="utf-8")
    try:
        result = skill_read.read_skill("personalize-check")
        assert result["ok"] is True
        assert result["content"] == "serves Kumar\n"
    finally:
        (skill_dir / "skill.yml").unlink()
        skill_dir.rmdir()