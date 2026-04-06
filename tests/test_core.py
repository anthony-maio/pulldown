"""Characterization tests for the core fetch + extract pipeline."""

from __future__ import annotations

import httpx
import pytest

from pulldown import Detail, FetchResult, fetch, fetch_many

# ---------------------------------------------------------------------------
# Detail enum
# ---------------------------------------------------------------------------


class TestDetail:
    def test_enum_values(self):
        assert Detail.minimal.value == "minimal"
        assert Detail.readable.value == "readable"
        assert Detail.full.value == "full"
        assert Detail.raw.value == "raw"

    def test_enum_from_string(self):
        assert Detail("minimal") == Detail.minimal
        assert Detail("readable") == Detail.readable

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            Detail("nonsense")


# ---------------------------------------------------------------------------
# FetchResult
# ---------------------------------------------------------------------------


class TestFetchResult:
    def test_ok_when_200(self):
        r = FetchResult(url="http://x", status_code=200, content="")
        assert r.ok is True

    def test_ok_false_on_error(self):
        r = FetchResult(url="http://x", status_code=200, content="", error="oops")
        assert r.ok is False

    def test_ok_false_on_4xx(self):
        r = FetchResult(url="http://x", status_code=404, content="")
        assert r.ok is False

    def test_ok_false_on_5xx(self):
        r = FetchResult(url="http://x", status_code=500, content="")
        assert r.ok is False

    def test_ok_true_on_3xx(self):
        r = FetchResult(url="http://x", status_code=301, content="")
        assert r.ok is True

    def test_str_on_error(self):
        r = FetchResult(url="http://x", status_code=0, content="", error="boom")
        assert "[ERROR]" in str(r)
        assert "boom" in str(r)

    def test_str_on_success(self):
        r = FetchResult(url="http://x", status_code=200, content="hi\nthere", elapsed_ms=42.3)
        s = str(r)
        assert "[200]" in s
        assert "http://x" in s


# ---------------------------------------------------------------------------
# Fetch pipeline — using httpx MockTransport for hermeticity
# ---------------------------------------------------------------------------


def make_mock_transport(html: str, status: int = 200):
    """Return an httpx MockTransport that serves html for every request."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, html=html)

    return httpx.MockTransport(handler)


class TestFetchReadable:
    async def test_extracts_article_body(self, sample_article_html, monkeypatch):
        transport = make_mock_transport(sample_article_html)

        # Monkeypatch AsyncClient to use our transport
        orig = httpx.AsyncClient

        def patched(*args, **kwargs):
            kwargs["transport"] = transport
            return orig(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", patched)

        result = await fetch("http://example.com/article", detail=Detail.readable)

        assert result.ok
        assert result.status_code == 200
        assert result.title == "A Fine Article"
        assert "first paragraph" in result.content
        assert "second paragraph" in result.content
        # boilerplate should be gone
        assert "Copyright 2026" not in result.content

    async def test_captures_metadata(self, sample_article_html, monkeypatch):
        transport = make_mock_transport(sample_article_html)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
        )

        result = await fetch("http://example.com/article", detail=Detail.readable)

        # trafilatura should pull author from meta tag
        assert result.meta.get("author") is not None


class TestFetchMinimal:
    async def test_plain_text_no_links(self, sample_article_html, monkeypatch):
        transport = make_mock_transport(sample_article_html)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
        )

        result = await fetch("http://example.com/article", detail=Detail.minimal)

        assert result.ok
        assert result.title == "A Fine Article"
        assert "first paragraph" in result.content
        # no markdown link syntax
        assert "](" not in result.content


class TestFetchRaw:
    async def test_returns_html_unchanged(self, sample_article_html, monkeypatch):
        transport = make_mock_transport(sample_article_html)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
        )

        result = await fetch("http://example.com/article", detail=Detail.raw)

        assert result.ok
        assert result.content == sample_article_html
        assert result.title == "A Fine Article"


class TestFetchErrorHandling:
    async def test_http_404_returns_error_result(self, monkeypatch):
        transport = make_mock_transport("<h1>Not Found</h1>", status=404)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
        )

        result = await fetch("http://example.com/missing")

        assert not result.ok
        assert result.status_code == 404
        assert result.error is not None

    async def test_network_error_returns_error_result(self, monkeypatch):
        def handler(request):
            raise httpx.ConnectError("refused")

        transport = httpx.MockTransport(handler)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
        )

        result = await fetch("http://example.com/")

        assert not result.ok
        assert result.error is not None
        assert result.status_code == 0

    async def test_detail_as_string_accepted(self, sample_article_html, monkeypatch):
        transport = make_mock_transport(sample_article_html)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
        )

        result = await fetch("http://example.com/", detail="readable")

        assert result.ok


# ---------------------------------------------------------------------------
# fetch_many
# ---------------------------------------------------------------------------


class TestFetchMany:
    async def test_preserves_input_order(self, sample_article_html, monkeypatch):
        transport = make_mock_transport(sample_article_html)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
        )

        urls = [
            "http://example.com/a",
            "http://example.com/b",
            "http://example.com/c",
        ]
        results = await fetch_many(urls, concurrency=2)

        assert len(results) == 3
        assert [r.url for r in results] == urls
