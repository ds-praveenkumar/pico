"""Tests for the weather tool (fully mocked, no network)."""

import json

from agents.tools import REGISTRY, TOOL_NAMES
from agents.tools import weather as weather_module
from agents.tools.weather import weather

GEOCODING_BODY = {
    "results": [
        {
            "name": "Bengaluru",
            "country": "India",
            "admin1": "Karnataka",
            "latitude": 12.97,
            "longitude": 77.59,
        }
    ]
}

FORECAST_BODY = {
    "current": {
        "temperature_2m": 25.0,
        "apparent_temperature": 27.0,
        "relative_humidity_2m": 52,
        "weather_code": 2,
        "wind_speed_10m": 11.0,
    },
    "daily": {
        "time": ["2026-09-13", "2026-09-14"],
        "weather_code": [2, 61],
        "temperature_2m_max": [28.0, 26.0],
        "temperature_2m_min": [21.0, 20.0],
        "precipitation_probability_max": [10, 80],
    },
}


class FakeResponse:
    """Minimal http.client-style response returning canned bytes."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        return self._body


def _fake_urlopen(routes: dict):
    """Route URLs by host via the ``geocode``/``forecast`` keys."""

    def open_url(url: str, timeout: int = 15):  # noqa: ARG001
        if "geocoding-api.open-meteo.com" in url:
            return FakeResponse(json.dumps(routes.get("geocode", GEOCODING_BODY)).encode())
        if "api.open-meteo.com" in url:
            return FakeResponse(json.dumps(routes.get("forecast", FORECAST_BODY)).encode())
        raise AssertionError(f"unexpected url: {url}")

    return open_url


def test_weather_returns_current_and_forecast(monkeypatch):
    monkeypatch.setattr(weather_module, "_urlopen", _fake_urlopen({}))
    result = weather("Bengaluru")
    assert result["ok"] is True
    assert "Bengaluru" in result["location"]
    assert result["current"]["temperature_c"] == 25.0
    assert result["current"]["condition"] == "Partly cloudy"
    assert len(result["forecast"]) == 2
    assert result["forecast"][1]["condition"] == "Slight rain"


def test_weather_clamps_days(monkeypatch):
    monkeypatch.setattr(weather_module, "_urlopen", _fake_urlopen({}))
    result = weather("Bengaluru", days=99)
    assert result["ok"] is True
    assert len(result["forecast"]) == 2  # mock only has 2 daily rows
    small = weather("Bengaluru", days=0)
    assert small["ok"] is True


def test_weather_rejects_empty_location():
    result = weather("   ")
    assert result["ok"] is False
    assert "location" in result["error"]


def test_weather_reports_unknown_location(monkeypatch):
    monkeypatch.setattr(
        weather_module, "_urlopen", _fake_urlopen({"geocode": {"results": []}})
    )
    result = weather("Atlantis")
    assert result["ok"] is False
    assert "Atlantis" in result["error"]


def test_weather_reports_download_failure(monkeypatch):
    def broken_open(url: str, timeout: int = 15):  # noqa: ARG001
        raise OSError("network refused")

    monkeypatch.setattr(weather_module, "_urlopen", broken_open)
    result = weather("Bengaluru")
    assert result["ok"] is False
    assert "network refused" in result["error"]


def test_fetch_json_rejects_non_allowlisted_host():
    import pytest

    with pytest.raises(ValueError, match="allowlisted"):
        weather_module._fetch_json("https://evil.example.com/api")


def test_weather_registered_in_registry():
    assert "weather" in TOOL_NAMES
    assert callable(REGISTRY["weather"]["callable"])