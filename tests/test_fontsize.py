"""Tests for terminal font-size control (mintty OSC 7770 + ``PICO_FONT_SIZE``).

The helper only ever writes to a real terminal: unset sizes, out-of-range
values, and non-TTY streams must stay silent so no control bytes leak into
pipes, captured logs, or the rich log queue.
"""

from __future__ import annotations

import io

import pytest

from ui.fontsize import (
    FONT_SIZES,
    apply_font_size,
    font_size_from_env,
    font_size_sequence,
)


class _Tty(io.StringIO):
    """A write-capturing stream that reports itself as a terminal."""

    def isatty(self) -> bool:
        return True


def test_font_size_sequence_uses_osc_7770():
    assert font_size_sequence(18) == "\x1b]7770;18\x07"


def test_font_size_sequence_accepts_numeric_strings():
    assert font_size_sequence("20") == "\x1b]7770;20\x07"


def test_apply_writes_the_sequence_to_a_tty():
    stream = _Tty()
    assert apply_font_size(18, stream=stream) is True
    assert stream.getvalue() == "\x1b]7770;18\x07"


def test_apply_skips_non_tty_streams():
    stream = io.StringIO()
    assert apply_font_size(18, stream=stream) is False
    assert stream.getvalue() == ""


def test_apply_without_size_or_env_is_a_noop(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PICO_FONT_SIZE", raising=False)
    stream = _Tty()
    assert apply_font_size(stream=stream) is False
    assert stream.getvalue() == ""


def test_apply_falls_back_to_env_size(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PICO_FONT_SIZE", "24")
    stream = _Tty()
    assert apply_font_size(stream=stream) is True
    assert stream.getvalue() == "\x1b]7770;24\x07"


def test_apply_prefers_the_explicit_size(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PICO_FONT_SIZE", "24")
    stream = _Tty()
    assert apply_font_size(18, stream=stream) is True
    assert stream.getvalue() == "\x1b]7770;18\x07"


def test_env_size_is_parsed_and_trimmed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PICO_FONT_SIZE", "  20 ")
    assert font_size_from_env() == 20


def test_env_size_unset_is_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PICO_FONT_SIZE", raising=False)
    assert font_size_from_env() is None


@pytest.mark.parametrize("raw", ["", "   ", "abc", "1.5", "4", "500"])
def test_env_size_rejects_blank_unparsable_and_out_of_range(
    monkeypatch: pytest.MonkeyPatch, raw: str
):
    monkeypatch.setenv("PICO_FONT_SIZE", raw)
    assert font_size_from_env() is None


def test_offered_sizes_are_all_accepted(monkeypatch: pytest.MonkeyPatch):
    for size in FONT_SIZES:
        monkeypatch.setenv("PICO_FONT_SIZE", str(size))
        assert font_size_from_env() == size
