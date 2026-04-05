"""Characterization tests for the crawler."""
from __future__ import annotations

import httpx

from pulldown import crawl
from pulldown.crawl import _same_subpath, _should_skip

# ---------------------------------------------------------------------------
# URL boundary helpers
# ---------------------------------------------------------------------------

class TestSameSubpath:
    def test_same_domain_no_path(self):
        assert _same_subpath("http://x.com", "http://x.com/page")

    def test_same_domain_same_path(self):
        assert _same_subpath("http://x.com/docs", "http://x.com/docs/intro")

    def test_same_domain_sibling_path(self):
        assert not _same_subpath("http://x.com/docs", "http://x.com/blog")

    def test_different_domain(self):
        assert not _same_subpath("http://x.com/", "http://y.com/")

    def test_different_subdomain(self):
        assert not _same_subpath("http://x.com/", "http://api.x.com/")

    def test_exact_match(self):
        assert _same_subpath("http://x.com/docs", "http://x.com/docs")


class TestShouldSkip:
    def test_skips_pdf(self):
        assert _should_skip("http://x.com/file.pdf")

    def test_skips_image(self):
        assert _should_skip("http://x.com/image.png")
        assert _should_skip("http://x.com/image.JPG")  # case insensitive

    def test_skips_archive(self):
        assert _should_skip("http://x.com/file.zip")

    def test_skips_asset(self):
        assert _should_skip("http://x.com/app.js")
        assert _should_skip("http://x.com/style.css")

    def test_keeps_html(self):
        assert not _should_skip("http://x.com/page")
        assert not _should_skip("http://x.com/page.html")
        assert not _should_skip("http://x.com/page/")


# ---------------------------------------------------------------------------
# Crawl integration
# ---------------------------------------------------------------------------

def _build_site():
    """Return (handler_fn, urls_served) for a small mock site."""
    pages = {
        "/": '<html><head><title>Home</title></head><body><h1>Home</h1>'
             '<p>Welcome to the site, enjoy your stay.</p>'
             '<a href="/a">A</a> <a href="/b">B</a> <a href="https://other.com/x">ext</a></body></html>',
        "/a": '<html><head><title>A</title></head><body><h1>A</h1>'
              '<p>Page A with enough content to be indexable.</p>'
              '<a href="/c">C</a></body></html>',
        "/b": '<html><head><title>B</title></head><body><h1>B</h1>'
              '<p>Page B with enough content here too.</p></body></html>',
        "/c": '<html><head><title>C</title></head><body><h1>C</h1>'
              '<p>Page C, deep page at the bottom.</p></body></html>',
    }

    requests_seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(str(request.url))
        path = request.url.path
        if path in pages:
            return httpx.Response(200, html=pages[path])
        return httpx.Response(404)

    return handler, requests_seen


class TestCrawl:
    async def test_stays_on_domain(self, monkeypatch):
        handler, seen = _build_site()
        transport = httpx.MockTransport(handler)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
        )

        result = await crawl("http://site.example/", max_pages=10, max_depth=2, concurrency=1)

        urls = {p.url for p in result.pages}
        # All fetched URLs should be on site.example
        for u in urls:
            assert "site.example" in u
            assert "other.com" not in u

    async def test_respects_max_pages(self, monkeypatch):
        handler, seen = _build_site()
        transport = httpx.MockTransport(handler)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
        )

        result = await crawl("http://site.example/", max_pages=2, max_depth=5, concurrency=1)

        assert len(result.pages) <= 2

    async def test_to_markdown_concatenates(self, monkeypatch):
        handler, seen = _build_site()
        transport = httpx.MockTransport(handler)
        orig = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
        )

        result = await crawl("http://site.example/", max_pages=5, max_depth=2, concurrency=1)
        md = result.to_markdown()

        assert md  # non-empty
        # headers separated by the separator
        assert "---" in md
