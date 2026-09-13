"""Tests for the URL fetch tool (fully mocked, no network)."""

import io
import socket
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from agents.tools import REGISTRY, TOOL_NAMES
from agents.tools import url_fetch as url_fetch_module
from agents.tools.url_fetch import _blocked_host, url_fetch

HTML_BODY = (
    b"<html><head><title>Example</title></head>"
    b"<body><h1>Hello</h1><p>World text here</p></body></html>"
)


class FakeResponse:
    """Minimal http.client-style response returning canned bytes."""

    def __init__(self, body: bytes, url: str = "https://example.com/", status: int = 200, content_type: str = "text/html; charset=utf-8") -> None:
        self._body = body
        self.url = url
        self.status = status
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self.url


class FakeOpener:
    """Builds a fake urllib opener that returns one canned response."""

    def __init__(self, response) -> None:
        self._response = response

    def open(self, url: str, timeout: int = 15):
        return self._response


def test_url_fetch_extracts_text_and_title(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *_a: FakeOpener(FakeResponse(HTML_BODY))
    )
    result = url_fetch("https://example.com/")
    assert result["ok"] is True
    assert result["title"] == "Example"
    assert "Hello" in result["text"]
    assert "World text here" in result["text"]
    assert result["final_url"] == "https://example.com/"
    assert result["truncated"] is False


def test_url_fetch_rejects_non_http_schemes():
    result = url_fetch("file:///etc/passwd")
    assert result["ok"] is False
    assert "only http(s)" in result["error"]
    result = url_fetch("ftp://example.com/file")
    assert result["ok"] is False


def test_url_fetch_rejects_missing_hostname():
    result = url_fetch("https:///path")
    assert result["ok"] is False
    assert "hostname" in result["error"]


def test_url_fetch_rejects_credentials_in_url():
    result = url_fetch("https://user:pass@example.com/")
    assert result["ok"] is False
    assert "credentials" in result["error"]


def test_url_fetch_refuses_blocked_host(monkeypatch):
    monkeypatch.setattr(url_fetch_module, "_blocked_host", lambda _host: "destination resolves to a non-public address (10.0.0.1)")
    result = url_fetch("http://10.0.0.1/")
    assert result["ok"] is False
    assert "refusing to fetch" in result["error"]


def test_url_fetch_truncates_oversized_body(monkeypatch):
    big = b"x" * (url_fetch_module.DEFAULT_MAX_BYTES + 5000)
    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *_a: FakeOpener(FakeResponse(big))
    )
    result = url_fetch("https://example.com/", max_bytes=100_000)
    assert result["ok"] is True
    assert result["truncated"] is True
    assert result["bytes"] == 100_000


def test_url_fetch_reports_http_error(monkeypatch):
    def broken_open(url: str, timeout: int = 15):
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, io.BytesIO(b""))

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_a: SimpleNamespace(open=broken_open))
    result = url_fetch("https://example.com/")
    assert result["ok"] is False
    assert "503" in result["error"]


def test_url_fetch_reports_network_error(monkeypatch):
    def broken_open(url: str, timeout: int = 15):  # noqa: ARG001
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_a: SimpleNamespace(open=broken_open))
    result = url_fetch("https://example.com/")
    assert result["ok"] is False
    assert "connection refused" in result["error"]


def test_blocked_host_rejects_loopback(monkeypatch):
    monkeypatch.setattr(
        url_fetch_module.socket,
        "getaddrinfo",
        lambda _host, _port: [(2, 1, 6, "", ("127.0.0.1", 0))],
    )
    assert _blocked_host("localhost") is not None


def test_blocked_host_rejects_private_range(monkeypatch):
    monkeypatch.setattr(
        url_fetch_module.socket,
        "getaddrinfo",
        lambda _host, _port: [(2, 1, 6, "", ("192.168.1.10", 0))],
    )
    assert _blocked_host("intranet.local") is not None


def test_blocked_host_allows_public_ip(monkeypatch):
    monkeypatch.setattr(
        url_fetch_module.socket,
        "getaddrinfo",
        lambda _host, _port: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    assert _blocked_host("example.com") is None


def test_blocked_host_reports_unresolvable(monkeypatch):
    def raise_gai(_host, _port):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(url_fetch_module.socket, "getaddrinfo", raise_gai)
    assert _blocked_host("no-such-host.invalid") is not None


def test_redirect_handler_refuses_non_http():
    handler = url_fetch_module._SafeRedirectHandler()
    assert handler.redirect_request(None, None, 302, "Found", None, "file:///etc/passwd") is None


def test_redirect_handler_refuses_blocked_host(monkeypatch):
    handler = url_fetch_module._SafeRedirectHandler()
    monkeypatch.setattr(url_fetch_module, "_blocked_host", lambda _host: "non-public")
    with pytest.raises(ValueError, match="redirect refused"):
        handler.redirect_request(None, None, 302, "Found", None, "https://10.0.0.1/")


def test_url_fetch_registered_in_registry():
    assert "url_fetch" in TOOL_NAMES
    assert callable(REGISTRY["url_fetch"]["callable"])