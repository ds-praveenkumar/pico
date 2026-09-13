"""Weather tool for pico.

Fetches current conditions and a short forecast from the Open-Meteo APIs
(no API key required) using only the standard library. Safe by construction:
only the two Open-Meteo hosts are ever contacted, each request has a hard
timeout and a byte cap, and every response is shaped into a compact,
LLM-friendly payload.
"""

import json
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from brain.logging_setup import get_logger

logger = get_logger(__name__)

MAX_BYTES = 256_000
DEFAULT_TIMEOUT = 15
MAX_DAYS = 7

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

ALLOWED_HOSTS = {urllib.parse.urlparse(url).netloc for url in (GEOCODING_URL, FORECAST_URL)}

_urlopen = urllib.request.urlopen

# WMO weather interpretation codes -> short human labels
WMO_CODES: Dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Freezing drizzle, light",
    57: "Freezing drizzle, dense",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Freezing rain, light",
    67: "Freezing rain, heavy",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _fetch_json(url: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Download and parse a JSON payload from an allowlisted host."""
    host = urllib.parse.urlparse(url).netloc
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"host not allowlisted: {host!r}")
    with _urlopen(url, timeout=timeout) as response:
        raw = response.read(MAX_BYTES + 1)
    return json.loads(raw[:MAX_BYTES])


def _geocode(location: str) -> Dict[str, Any]:
    """Resolve a place name to coordinates via the Open-Meteo geocoding API."""
    query = urllib.parse.urlencode({"name": location, "count": 1, "language": "en", "format": "json"})
    data = _fetch_json(f"{GEOCODING_URL}?{query}")
    results = data.get("results") or []
    if not results:
        raise ValueError(f"no location found for {location!r}")
    return results[0]


def _condition(code: Any) -> str:
    """Map a WMO weather code to its short label."""
    try:
        return WMO_CODES.get(int(code), f"Code {code}")
    except (TypeError, ValueError):
        return "Unknown"


def weather(location: str, days: int = 1) -> Dict[str, Any]:
    """Return current weather and a ``days``-long forecast for ``location``."""
    days = max(1, min(int(days), MAX_DAYS))
    location = location.strip()
    if not location:
        return {"ok": False, "error": "location is required"}

    try:
        place = _geocode(location)
        latitude, longitude = place["latitude"], place["longitude"]
        name = place.get("name", location)
        country = place.get("country", "")
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
            "timezone": "auto",
            "forecast_days": days,
        }
        forecast = _fetch_json(f"{FORECAST_URL}?{urllib.parse.urlencode(params)}")
    except Exception as exc:  # noqa: BLE001 - one failed lookup must not crash pico
        logger.warning(f"[bold yellow]Weather lookup failed[/bold yellow]: {location!r} -> {exc}")
        return {"ok": False, "error": str(exc), "location": location}

    current = forecast.get("current") or {}
    daily = forecast.get("daily") or {}
    codes = daily.get("weather_code") or []
    maxes = daily.get("temperature_2m_max") or []
    mins = daily.get("temperature_2m_min") or []
    precip = daily.get("precipitation_probability_max") or []
    dates = daily.get("time") or []

    daily_out: List[Dict[str, Any]] = []
    for index, day_date in enumerate(dates):
        daily_out.append(
            {
                "date": day_date,
                "condition": _condition(codes[index] if index < len(codes) else None),
                "high_c": maxes[index] if index < len(maxes) else None,
                "low_c": mins[index] if index < len(mins) else None,
                "precip_probability": precip[index] if index < len(precip) else None,
            }
        )

    result: Dict[str, Any] = {
        "ok": True,
        "location": f"{name}, {country}".strip().strip(","),
        "current": {
            "temperature_c": current.get("temperature_2m"),
            "feels_like_c": current.get("apparent_temperature"),
            "humidity": current.get("relative_humidity_2m"),
            "wind_speed_kmh": current.get("wind_speed_10m"),
            "condition": _condition(current.get("weather_code")),
        },
        "forecast": daily_out,
    }
    logger.info(f"[bold green]Fetched weather[/bold green]: {result['location']} ({days}d forecast)")
    return result