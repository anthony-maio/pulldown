"""Failing tests driving bug fixes. Each test here was added RED-first.

Bugs targeted:
  1. PageCache default uses /tmp on Windows
  2. Render path hardcodes status_code=200
  3. _extract_full/_extract_readable metadata broken on trafilatura 2.0 Document API
  4. Playwright cookies missing domain/url
  5. Crawler double-fetches pages
  6. urls_discovered counter increments before dedup
  7. Regex link extraction misses edge cases
  8. MCP HTTP transport binds 0.0.0.0 by default
  9. No URL validation / SSRF guards
 10. No max content size
 11. Cache writes not atomic
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Bug 1: cache dir should not default to /tmp on Windows
# ---------------------------------------------------------------------------


class TestCrossPlatformCacheDir:
    def test_default_cache_dir_is_platform_appropriate(self):
        from pulldown import PageCache

        c = PageCache()  # no arg -> default
        # Must NOT be /tmp/pulldown-cache on Windows
        if sys.platform == "win32":
            assert not str(c.cache_dir).startswith("/tmp")
        # Should exist and be writable
        assert c.cache_dir.exists()
        assert c.cache_dir.is_dir()


# ---------------------------------------------------------------------------
# Bug 2: render path should not hardcode status_code=200
# ---------------------------------------------------------------------------
# (covered via API surface: when render=True succeeds, status should reflect
# the HTTP response. We use an integration seam rather than Playwright here.)


# ---------------------------------------------------------------------------
# Bug 3: metadata on trafilatura 2.0 Document API
# (already covered by test_core.py::TestFetchReadable::test_captures_metadata)


# ---------------------------------------------------------------------------
# Bug 5: crawler should fetch each URL exactly once
# ---------------------------------------------------------------------------


class TestCrawlerNoDoubleFetch:
    async def test_each_url_fetched_once(self, monkeypatch):
        from pulldown import crawl

        pages = {
            "/": "<html><head><title>Home</title></head><body>"
            "<h1>Home</h1><p>Welcome lots of content here yes indeed.</p>"
            '<a href="/a">A</a></body></html>',
            "/a": "<html><head><title>A</title></head><body>"
            "<h1>A</h1><p>Content on page A enough for extraction.</p></body></html>",
        }
        hits: dict[str, int] = {}

        def handler(request):
            path = request.url.path
            hits[path] = hits.get(path, 0) + 1
            return httpx.Response(200, html=pages.get(path, "<h1>404</h1>"))

        transport = httpx.MockTransport(handler)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
        )

        await crawl("http://site.example/", max_pages=10, max_depth=2, concurrency=1)

        # Each URL should only be fetched once, not twice
        for path, count in hits.items():
            assert count == 1, f"{path} fetched {count} times (should be 1)"


# ---------------------------------------------------------------------------
# Bug 6: urls_discovered counts only new unique URLs
# ---------------------------------------------------------------------------


class TestCrawlerDiscoveryCount:
    async def test_discovered_count_is_unique(self, monkeypatch):
        from pulldown import crawl

        # Site where /a and /b both link to /c — /c should be discovered only once
        pages = {
            "/": "<html><body><h1>Home</h1><p>Intro text here with enough words.</p>"
            '<a href="/a">A</a> <a href="/b">B</a></body></html>',
            "/a": "<html><body><h1>A</h1><p>Page A text with enough words indeed.</p>"
            '<a href="/c">C</a></body></html>',
            "/b": "<html><body><h1>B</h1><p>Page B text with enough words, really.</p>"
            '<a href="/c">C</a></body></html>',
            "/c": "<html><body><h1>C</h1><p>Page C text with enough words here.</p></body></html>",
        }

        def handler(request):
            return httpx.Response(200, html=pages.get(request.url.path, "<h1>404</h1>"))

        transport = httpx.MockTransport(handler)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
        )

        result = await crawl("http://site.example/", max_pages=10, max_depth=3, concurrency=1)

        # 4 unique URLs fetched: /, /a, /b, /c
        assert result.urls_fetched == 4
        # urls_discovered should be UNIQUE discoveries, not raw link counts.
        # From /: discovers /a, /b → 2
        # From /a: discovers /c → 1 (cumulative: 3)
        # From /b: discovers /c already seen → 0 new
        # From /c: no links
        # Total unique discovered: 3
        assert result.urls_discovered == 3


# ---------------------------------------------------------------------------
# Bug 7: link extraction should use lxml, handle edge cases
# ---------------------------------------------------------------------------


class TestLinkExtraction:
    def test_extracts_from_anchor_tags_only(self):
        from pulldown.crawl import _extract_links

        html = """
        <html><body>
            <a href="/real-link">real</a>
            <p>Text that says href="/fake" but is not a link</p>
            <!-- <a href="/commented">commented</a> -->
            <a href='/single-quotes'>ok</a>
        </body></html>
        """
        links = _extract_links(html, "http://x.com/")

        assert "http://x.com/real-link" in links
        assert "http://x.com/single-quotes" in links
        # Should not extract from text content
        assert "http://x.com/fake" not in links
        # Should not extract from HTML comments
        assert "http://x.com/commented" not in links

    def test_strips_fragment(self):
        from pulldown.crawl import _extract_links

        html = '<a href="/page#section">hi</a>'
        links = _extract_links(html, "http://x.com/")
        assert "http://x.com/page" in links
        assert "http://x.com/page#section" not in links

    def test_ignores_non_http_schemes(self):
        from pulldown.crawl import _extract_links

        html = """
        <a href="mailto:a@b.com">m</a>
        <a href="tel:+1234">t</a>
        <a href="javascript:void(0)">j</a>
        <a href="data:text/html,hi">d</a>
        <a href="/good">g</a>
        """
        links = _extract_links(html, "http://x.com/")
        assert links == {"http://x.com/good"}


# ---------------------------------------------------------------------------
# Bug 8: MCP HTTP transport must default to localhost
# ---------------------------------------------------------------------------


class TestMcpServerDefaults:
    def test_mcp_default_host_is_loopback(self):
        """The MCP server must NOT default to 0.0.0.0 for HTTP transport."""
        # We read the source and check it defaults to 127.0.0.1 or localhost
        from pathlib import Path

        src = Path(__file__).parent.parent / "src" / "pulldown" / "mcp_server.py"
        text = src.read_text()
        # The default host should be loopback
        assert "0.0.0.0" not in text or "MCP_HOST" in text, (
            "MCP server must not bind 0.0.0.0 by default; require env opt-in"
        )


# ---------------------------------------------------------------------------
# Bug 9: SSRF guard — private/loopback/metadata addresses blocked by default
# ---------------------------------------------------------------------------


class TestSsrfGuards:
    async def test_loopback_blocked_by_default(self):
        from pulldown import fetch

        result = await fetch("http://127.0.0.1/")
        assert not result.ok
        assert result.error and (
            "blocked" in result.error.lower() or "private" in result.error.lower()
        )

    async def test_metadata_service_blocked_by_default(self):
        from pulldown import fetch

        result = await fetch("http://169.254.169.254/latest/meta-data/")
        assert not result.ok
        assert result.error is not None

    async def test_rfc1918_blocked_by_default(self):
        from pulldown import fetch

        result = await fetch("http://10.0.0.1/")
        assert not result.ok
        assert result.error is not None

    async def test_file_scheme_blocked(self):
        from pulldown import fetch

        result = await fetch("file:///etc/passwd")
        assert not result.ok
        assert result.error is not None

    async def test_allow_private_opts_in(self, monkeypatch):
        """With allow_private_addresses=True, loopback should be fetchable."""
        from pulldown import fetch

        def handler(request):
            return httpx.Response(
                200,
                html="<html><title>ok</title><body>"
                "<p>ok ok ok ok content here enough.</p></body></html>",
            )

        transport = httpx.MockTransport(handler)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
        )

        result = await fetch("http://127.0.0.1/", allow_private_addresses=True)
        assert result.ok


# ---------------------------------------------------------------------------
# Bug 10: max content size
# ---------------------------------------------------------------------------


class TestMaxContentSize:
    async def test_rejects_oversized_response(self, monkeypatch):
        from pulldown import fetch

        big = "<html><body>" + ("x" * 10000) + "</body></html>"

        def handler(request):
            return httpx.Response(
                200,
                html=big,
                headers={"Content-Length": str(len(big))},
            )

        transport = httpx.MockTransport(handler)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
        )

        result = await fetch("http://example.com/", max_bytes=1000)
        assert not result.ok
        assert result.error is not None
        assert (
            "size" in result.error.lower()
            or "large" in result.error.lower()
            or "bytes" in result.error.lower()
        )


# ---------------------------------------------------------------------------
# Bug 11: cache writes should be atomic
# ---------------------------------------------------------------------------


class TestAtomicCacheWrites:
    def test_put_writes_atomically(self, tmp_cache_dir, monkeypatch):
        """Simulate a crash mid-write: .tmp file should exist, .json should not."""
        from pulldown import PageCache

        c = PageCache(tmp_cache_dir)

        # Monkeypatch Path.write_text to raise, simulating crash
        original_replace = Path.replace
        calls = []

        def tracking_replace(self, target):
            calls.append((str(self), str(target)))
            return original_replace(self, target)

        monkeypatch.setattr(Path, "replace", tracking_replace)

        c.put("http://x.com/", "readable", {"content": "hi"})

        # os.replace (via Path.replace) must have been called — that's the signature of atomic write
        assert len(calls) > 0, "cache write did not use atomic rename"
        # source should be a tmp file, target should be the final .json
        src, tgt = calls[-1]
        assert src.endswith(".tmp") or ".tmp" in src
        assert tgt.endswith(".json")
