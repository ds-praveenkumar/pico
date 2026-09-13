"""Latest-news tool for pico.

Fetches today's top headlines from a small, curated set of public news RSS
feeds (Google News, BBC World, The Guardian World) using only the standard
library. Safe by construction: only allowlisted feed hosts are ever contacted,
each request has a hard timeout and a byte cap, and the returned headlines are
shaped into a compact, LLM-friendly payload — pico never sees a raw feed dump.
"""

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, Iterator, List

from brain.logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_LIMIT = 5
DEFAULT_TIMEOUT = 15
MAX_FEED_BYTES = 512_000

DEFAULT_SOURCES: List[Dict[str, str]] = [
    {"name": "Google News (top stories)", "url": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"},
    {"name": "BBC World News", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "The Guardian World", "url": "https://www.theguardian.com/world/rss"},
]


def _topic_source(topic: str) -> Dict[str, str]:
    """Build a Google News topic-search feed source for ``topic``."""
    query = urllib.parse.quote(topic.strip())
    return {
        "name": f"Google News ({topic.strip()})",
        "url": f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
    }

ALLOWED_HOSTS = {urllib.parse.urlparse(source["url"]).netloc for source in DEFAULT_SOURCES}

_urlopen = urllib.request.urlopen


def _local_name(tag: str) -> str:
    """Return an XML tag's local name, ignoring any namespace prefix."""
    return tag.rsplit("}", 1)[-1]


def _text(element: ET.Element) -> str:
    """Return the concatenated text of an element, trimmed."""
    return "".join(element.itertext()).strip()


def _first_text(entry: ET.Element, names: set) -> str:
    """Return the text of an entry's first direct child whose local name matches."""
    for child in entry:
        if _local_name(child.tag) in names:
            text = _text(child)
            if text:
                return text
    return ""


def _entry_link(entry: ET.Element) -> str:
    """Extract an entry's link, honoring both RSS text links and Atom href links."""
    for child in entry:
        if _local_name(child.tag) == "link":
            href = child.get("href")
            if href:
                return href
            return _text(child)
    return ""


def _entries(root: ET.Element) -> Iterator[ET.Element]:
    """Yield feed entries (RSS items or Atom entries) in document order."""
    for element in root.iter():
        if _local_name(element.tag) in {"item", "entry"}:
            yield element


def parse_feed(raw: bytes) -> List[Dict[str, str]]:
    """Parse an RSS or Atom feed body into a list of headline dicts."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"malformed XML: {exc}") from exc
    parsed: List[Dict[str, str]] = []
    for entry in _entries(root):
        headline = _first_text(entry, {"title"})
        if not headline:
            continue
        parsed.append(
            {
                "headline": headline,
                "link": _entry_link(entry),
                "published": _first_text(entry, {"pubDate", "published", "updated"}),
            }
        )
    return parsed


def fetch_feed(url: str, timeout: int = DEFAULT_TIMEOUT) -> bytes:
    """Download a feed body, enforcing the entry-point host allowlist."""
    host = urllib.parse.urlparse(url).netloc
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"feed host not allowlisted: {host!r}")
    with _urlopen(url, timeout=timeout) as response:
        raw = response.read(MAX_FEED_BYTES + 1)
    return raw[:MAX_FEED_BYTES]


def latest_news(limit: int = DEFAULT_LIMIT, topic: str = "") -> Dict[str, Any]:
    """Return today's top headlines, or news about ``topic`` when provided."""
    limit = max(1, min(int(limit), 10))
    today = datetime.now().astimezone().strftime("%A, %B %d, %Y")
    headlines: List[Dict[str, Any]] = []
    failed: List[str] = []
    sources = DEFAULT_SOURCES if not topic.strip() else [_topic_source(topic)]
    for source in sources:
        name, url = source["name"], source["url"]
        try:
            raw = fetch_feed(url)
            items = parse_feed(raw)
        except Exception as exc:  # noqa: BLE001 - one dead feed must not kill the briefing
            logger.warning(f"[bold yellow]News feed failed[/bold yellow]: {name} -> {exc}")
            failed.append(f"{name}: {exc}")
            continue
        for item in items[:limit]:
            headlines.append({"source": name, **item})
    loaded = len(sources) - len(failed)
    if not headlines:
        return {
            "ok": False,
            "date": today,
            "topic": topic.strip() or None,
            "error": "no news feeds could be loaded",
            "failed": failed,
        }
    logger.info(f"[bold green]Fetched latest news[/bold green]: {len(headlines)} headlines from {loaded}/{len(sources)} sources")
    return {
        "ok": True,
        "date": today,
        "topic": topic.strip() or None,
        "count": len(headlines),
        "headlines": headlines,
        "failed": failed,
    }