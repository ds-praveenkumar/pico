"""Tests for the text-to-speech tool (subprocess mocked, no audio)."""

import subprocess
import sys

from agents.tools import REGISTRY, TOOL_NAMES
from agents.tools.voice import voice_speak


class FakeProc:
    """Minimal stand-in for a completed subprocess."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_voice_speak_says_text(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):  # noqa: ARG001
        calls.append(argv)
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "platform", "darwin")
    result = voice_speak("hello world")
    assert result["ok"] is True
    assert result["spoken"] == "hello world"
    assert calls == [["say", "-r", "175", "hello world"]]


def test_voice_speak_honors_rate(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):  # noqa: ARG001
        calls.append(argv)
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "platform", "darwin")
    voice_speak("hi", rate=200)
    assert calls[0][2] == "200"


def test_voice_speak_rate_is_clamped(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):  # noqa: ARG001
        calls.append(argv)
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "platform", "darwin")
    voice_speak("hi", rate=5000)
    assert calls[0][2] == "400"


def test_voice_speak_reports_error_on_non_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    result = voice_speak("hello")
    assert result["ok"] is False
    assert "macOS" in result["error"]


def test_voice_speak_reports_missing_say(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("agents.tools.voice.shutil.which", lambda _name: None)
    result = voice_speak("hello")
    assert result["ok"] is False
    assert "say" in result["error"]


def test_voice_speak_rejects_empty_text(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    result = voice_speak("   ")
    assert result["ok"] is False
    assert "empty" in result["error"]


def test_voice_speak_reports_say_failure(monkeypatch):
    def fake_run(argv, **kwargs):  # noqa: ARG001
        return FakeProc(returncode=2, stderr="bad input")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "platform", "darwin")
    result = voice_speak("hello")
    assert result["ok"] is False
    assert "bad input" in result["error"]


def test_voice_registered_in_registry():
    assert "voice_speak" in TOOL_NAMES
    assert callable(REGISTRY["voice_speak"]["callable"])