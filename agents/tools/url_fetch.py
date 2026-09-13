"""Read-only URL fetch tool for pico.

Downloads a single public http(s) page and returns it as trimmed plain text —
no JavaScript, no cookies, just the bytes the server sends. Protects pico with
a protocol allowlist (http/https only), an SSRF guard that refuses destinations
resolving to loopback/private/link-local addresses, a byte cap, and a hard
timeout. Plain pages and docs go through this; pages that need JavaScript or
interaction belong to the ego-lite browser.
"""

import html.parser
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from brain.logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_BYTES = 500_000
DEFAULT_TIMEOUT = 15
MAX_BYTES_CEILING = 2_000_000
MAX_TEXT_CHARS = 20_000

_BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "table", "section", "article", "ul", "ol"}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}


class _TextExtractor(html.parser.HTMLParser):
    """Collect visible text and the <title> from an HTML document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.title = ""
        self._skip_depth = 0
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._title_depth += 1
        if self._skip_depth == 0 and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title" and self._title_depth:
            self._title_depth -= 1

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        if tag == "br" and self._skip_depth == 0:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._title_depth:
            self.title = (self.title + data).strip()
        if self._skip_depth == 0:
            text = " ".join(data.split())
            if text:
                self.parts.append(text + " ")

    def text(self, limit: int = MAX_TEXT_CHARS) -> str:
        """Return the trimmed visible text, capped at ``limit`` characters."""
        lines = [line.strip() for line in "".join(self.parts).splitlines() if line.strip()]
        return "\n".join(lines)[:limit]


def _blocked_host(host: str) -> Optional[str]:
    """Return a reason string when ``host`` is unsafe to fetch, else None."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return "could not resolve hostname"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return f"destination resolves to a non-public address ({ip.compressed})"
    return None


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only toward public http(s) destinations."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme not in ("http", "https"):
            return None
        reason = _blocked_host(parsed.hostname or "")
        if reason:
            raise ValueError(f"redirect refused: {newurl} ({reason})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _decode(raw: bytes, content_type: str) -> str:
    """Decode page bytes using the declared charset, falling back to UTF-8."""
    encoding = "utf-8"
    for part in content_type.split(";"):
        part = part.strip().lower()
        if part.startswith("charset="):
            encoding = part.split("=", 1)[1].strip("\"'") or encoding
    try:
        return raw.decode(encoding, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


def url_fetch(url: str, max_bytes: int = DEFAULT_MAX_BYTES, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Fetch one public http(s) page and return its text plus metadata."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"ok": False, "error": f"only http(s) URLs are allowed (got scheme {parsed.scheme!r})", "url": url}
    if not parsed.hostname:
        return {"ok": False, "error": "URL has no hostname", "url": url}
    if parsed.username or parsed.password:
        return {"ok": False, "error": "credentials in URLs are not allowed", "url": url}
    reason = _blocked_host(parsed.hostname)
    if reason:
        return {"ok": False, "error": f"refusing to fetch: {reason}", "url": url}

    cap = max(1_000, min(int(max_bytes), MAX_BYTES_CEILING))
    timeout = max(1, min(int(timeout), 60))
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    try:
        with opener.open(url, timeout=timeout) as response:
            raw = response.read(cap + 1)
            final_url = response.geturl()
            status = getattr(response, "status", 200)
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        logger.warning(f"[bold yellow]HTTP error fetching[/bold yellow]: {url} -> {exc.code}")
        return {"ok": False, "error": f"HTTP {exc.code}", "url": url}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning(f"[bold yellow]Fetch failed[/bold yellow]: {url} -> {exc}")
        return {"ok": False, "error": str(exc), "url": url}

    truncated = len(raw) > cap
    raw = raw[:cap]
    body = _decode(raw, content_type)
    extractor = _TextExtractor()
    extractor.feed(body)
    text = extractor.text()
    logger.info(f"[bold green]Fetched page[/bold green]: {final_url} ({len(raw)} bytes, {len(text)} chars of text)")
    return {
        "ok": True,
        "url": url,
        "final_url": final_url,
        "status": status,
        "content_type": content_type,
        "title": extractor.title.strip(),
        "text": text,
        "bytes": len(raw),
        "truncated": truncated,
    }