"""RED-first tests for the 4 P0/P1 security and correctness issues.

Findings:
  P0-1: SSRF guard bypassed by redirects (core.py follow_redirects=True)
  P0-2: crawl() fetches robots.txt before validating start_url
  P1-3: cached crawl collapses to seed page (empty raw_html on cache hit)
  P1-4: crawl(render=True) is single-page for detail != 'raw'
"""

from __future__ import annotations

import httpx
import pytest


# ---------------------------------------------------------------------------
# P0-1: redirect SSRF bypass
# ---------------------------------------------------------------------------


class TestRedirectSsrfBypass:
    """fetch() must validate every redirect target, not just the initial URL."""

    async def test_redirect_to_loopback_is_blocked(self, monkeypatch):
        from pulldown import fetch

        loopback_hit: list[str] = []

        def handler(request):
            host = request.url.host
            if host == "public.example":
                return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
            if host == "127.0.0.1":
                loopback_hit.append(str(request.url))
                return httpx.Response(200, html="<html><body>secret</body></html>")
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda *a, **kw: orig(*a, **{**kw, "transport": transport})
        )

        result = await fetch("http://public.example/")

        assert not result.ok, "redirect to loopback should be blocked"
        assert result.error is not None
        assert loopback_hit == [], f"loopback was actually requested: {loopback_hit}"

    async def test_redirect_to_rfc1918_is_blocked(self, monkeypatch):
        from pulldown import fetch

        def handler(request):
            if request.url.host == "public.example":
                return httpx.Response(301, headers={"location": "http://10.0.0.1/internal"})
            return httpx.Response(200, html="<html><body>internal</body></html>")

        transport = httpx.MockTransport(handler)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda *a, **kw: orig(*a, **{**kw, "transport": transport})
        )

        result = await fetch("http://public.example/")
        assert not result.ok

    async def test_redirect_to_public_host_is_allowed(self, monkeypatch):
        """Normal public-to-public redirects must still work."""
        from pulldown import fetch

        def handler(request):
            if request.url.host == "old.example":
                return httpx.Response(301, headers={"location": "http://new.example/page"})
            return httpx.Response(
                200,
                html="<html><body><h1>Redirected</h1><p>Enough content here.</p></body></html>",
            )

        transport = httpx.MockTransport(handler)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda *a, **kw: orig(*a, **{**kw, "transport": transport})
        )

        result = await fetch("http://old.example/")
        assert result.ok, f"redirect to public host should succeed: {result.error}"


# ---------------------------------------------------------------------------
# P0-2: crawl() robots.txt before SSRF validation
# ---------------------------------------------------------------------------


class TestCrawlStartUrlValidatedBeforeRobots:
    """crawl() must validate start_url before making any network I/O."""

    async def test_private_start_url_makes_no_requests(self, monkeypatch):
        from pulldown import crawl

        request_log: list[str] = []

        def handler(request):
            request_log.append(str(request.url))
            return httpx.Response(200, html="<html><body>page</body></html>")

        transport = httpx.MockTransport(handler)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda *a, **kw: orig(*a, **{**kw, "transport": transport})
        )

        result = await crawl("http://127.0.0.1/", max_pages=5)

        assert request_log == [], f"SSRF: unexpected network requests: {request_log}"
        assert result.urls_fetched == 0


# ---------------------------------------------------------------------------
# P1-3: cached crawl loses link discovery
# ---------------------------------------------------------------------------


class TestCachedCrawlPreservesTraversal:
    """A warm-cache crawl must still follow links found in cached pages."""

    async def test_second_crawl_still_visits_child_pages(self, tmp_cache_dir, monkeypatch):
        from pulldown import PageCache, crawl

        pages = {
            "/": (
                "<html><head><title>Home</title></head><body>"
                "<h1>Home</h1><p>Enough content for the extractor right here.</p>"
                '<a href="/child">child</a></body></html>'
            ),
            "/child": (
                "<html><head><title>Child</title></head><body>"
                "<h1>Child page</h1><p>Enough content for extractor here too.</p></body></html>"
            ),
        }

        def handler(request):
            return httpx.Response(
                200, html=pages.get(request.url.path, "<html><body>404</body></html>")
            )

        transport = httpx.MockTransport(handler)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda *a, **kw: orig(*a, **{**kw, "transport": transport})
        )

        cache = PageCache(tmp_cache_dir, ttl=3600)

        # First crawl: populates cache, fetches both pages
        result1 = await crawl("http://site.example/", max_pages=10, max_depth=2, cache=cache)
        assert result1.urls_fetched == 2, f"first crawl: expected 2, got {result1.urls_fetched}"

        # Second crawl: cache is warm, but traversal must still follow links
        result2 = await crawl("http://site.example/", max_pages=10, max_depth=2, cache=cache)
        assert result2.urls_fetched == 2, (
            f"Second crawl fetched {result2.urls_fetched} page(s) — cache hit must "
            "not drop link discovery."
        )


# ---------------------------------------------------------------------------
# P1-4: render=True crawl not recursive for non-raw detail
# ---------------------------------------------------------------------------


class TestRenderCrawlIsRecursive:
    """crawl(render=True, detail='readable') must follow child links, not stop at seed."""

    async def test_rendered_readable_crawl_follows_links(self, monkeypatch):
        from pulldown import Detail, crawl

        pages = {
            "http://site.example/": (
                "<html><head><title>Home</title></head><body>"
                "<h1>Home</h1><p>Home page content long enough for trafilatura.</p>"
                '<a href="http://site.example/child">child</a></body></html>',
                200,
            ),
            "http://site.example/child": (
                "<html><head><title>Child</title></head><body>"
                "<h1>Child page</h1><p>Child content that is long enough too.</p></body></html>",
                200,
            ),
        }

        async def mock_render_page(url, **kwargs):
            return pages.get(url, ("<html><body>404</body></html>", 404))

        # pulldown.__init__ does `from .crawl import crawl`, which overwrites the
        # `pulldown.crawl` attribute with the function. Use sys.modules to get
        # the actual submodule so monkeypatch can find `_render_page`.
        import sys

        crawl_module = sys.modules["pulldown.crawl"]
        monkeypatch.setattr(crawl_module, "_render_page", mock_render_page)

        result = await crawl(
            "http://site.example/",
            render=True,
            detail=Detail.readable,
            max_pages=10,
            max_depth=2,
        )

        assert result.urls_fetched >= 2, (
            f"Rendered crawl with detail=readable fetched only {result.urls_fetched} "
            "page(s) — link discovery must work for non-raw detail levels."
        )
