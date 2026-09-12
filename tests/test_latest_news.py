"""Tests for the latest-news RSS tool (fully mocked, no network)."""

from typing import Any, Dict

import pytest

from agents.tools import latest_news as news_module
from agents.tools.latest_news import fetch_feed, latest_news, parse_feed

RSS_BODY = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Sample News</title>
<item><title>Headline One</title><link>https://example.com/1</link><pubDate>Sat, 12 Sep 2026 09:00:00 GMT</pubDate></item>
<item><title>Headline Two</title><link>https://example.com/2</link><pubDate>Sat, 12 Sep 2026 08:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM_BODY = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Sample</title>
<entry><title>Atom Story</title><link href="https://example.com/a"/><updated>2026-09-12T09:00:00Z</updated></entry>
</feed>"""

MANY_ITEMS_RSS = (
    b'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
    + b"".join(
        b"<item><title>Headline %d</title><link>https://example.com/%d</link></item>" % (i, i)
        for i in range(12)
    )
    + b"</channel></rss>"
)


class FakeResponse:
    """A minimal http.client-style response returning canned bytes."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        return self._body


def _fake_open(bodies: Dict[str, bytes]):
    def open_url(url: str, timeout: int = 15) -> FakeResponse:  # noqa: ARG001
        for source in news_module.DEFAULT_SOURCES:
            if source["url"] == url:
                return FakeResponse(bodies.get(url, RSS_BODY))
        raise AssertionError(f"unexpected url: {url}")
    return open_url


def test_parse_rss_items():
    items = parse_feed(RSS_BODY)
    assert [i["headline"] for i in items] == ["Headline One", "Headline Two"]
    assert items[0]["link"] == "https://example.com/1"
    assert items[1]["published"] == "Sat, 12 Sep 2026 08:00:00 GMT"


def test_parse_atom_entries():
    items = parse_feed(ATOM_BODY)
    assert items[0]["headline"] == "Atom Story"
    assert items[0]["link"] == "https://example.com/a"
    assert items[0]["published"] == "2026-09-12T09:00:00Z"


def test_parse_feed_rejects_garbage():
    with pytest.raises(ValueError, match="malformed XML"):
        parse_feed(b"this is not xml at all")


def test_fetch_feed_accepts_only_allowlisted_hosts():
    with pytest.raises(ValueError, match="allowlisted"):
        fetch_feed("https://evil.example.com/feed.xml")


def test_latest_news_returns_headlines(monkeypatch):
    monkeypatch.setattr(news_module, "_urlopen", _fake_open({}))
    result = latest_news(limit=1)
    assert result["ok"] is True
    assert result["count"] == len(news_module.DEFAULT_SOURCES)
    assert result["headlines"][0]["source"] == news_module.DEFAULT_SOURCES[0]["name"]
    assert result["headlines"][0]["headline"] == "Headline One"
    assert result["date"]


def test_latest_news_limit_is_clamped(monkeypatch):
    url0 = news_module.DEFAULT_SOURCES[0]["url"]
    monkeypatch.setattr(news_module, "_urlopen", _fake_open({url0: MANY_ITEMS_RSS}))
    small = latest_news(limit=0)  # clamped up to 1
    assert small["ok"] is True
    assert small["count"] == 1 * 3
    big = latest_news(limit=100)  # clamped down to 10
    assert big["ok"] is True
    counts = [h["source"] for h in big["headlines"]].count
    assert counts(news_module.DEFAULT_SOURCES[0]["name"]) == 10
    assert big["count"] == 10 + 2 + 2


def test_latest_news_handles_feed_failure(monkeypatch):
    def broken_open(url: str, timeout: int = 15) -> Any:  # noqa: ARG001
        raise OSError("network refused")

    monkeypatch.setattr(news_module, "_urlopen", broken_open)
    result = latest_news()
    assert result["ok"] is False
    assert "no news feeds could be loaded" in result["error"]
    assert len(result["failed"]) == len(news_module.DEFAULT_SOURCES)


def test_partial_failure_still_reports_ok(monkeypatch):
    calls = {"n": 0}

    def flaky_open(url: str, timeout: int = 15):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("first feed down")
        return FakeResponse(RSS_BODY)

    monkeypatch.setattr(news_module, "_urlopen", flaky_open)
    result = latest_news(limit=2)
    assert result["ok"] is True
    assert len(result["failed"]) == 1
    assert len(result["headlines"]) == (len(news_module.DEFAULT_SOURCES) - 1) * 2


def test_registry_has_latest_news(monkeypatch):
    from agents.tools import REGISTRY, TOOL_NAMES
    from agents.tools import dispatch

    assert "latest_news" in TOOL_NAMES
    assert callable(REGISTRY["latest_news"]["callable"])
    monkeypatch.setattr(news_module, "_urlopen", _fake_open({}))
    result = dispatch("latest_news", limit=1)
    assert result["ok"] is True
    assert result["count"] == len(news_module.DEFAULT_SOURCES)