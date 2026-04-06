"""Tests for ETag / Last-Modified conditional caching."""

from __future__ import annotations

import httpx

from pulldown import PageCache, fetch

HTML = (
    "<html><head><title>Thing</title></head><body>"
    "<article><p>Plenty of body text here to satisfy the extractor, "
    "enough sentences for trafilatura to be happy about length.</p>"
    "<p>Second paragraph with yet more content to round things out.</p>"
    "</article></body></html>"
)


def _patched_client(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
    )


class TestValidatorCache:
    async def test_etag_stored_on_fetch(self, tmp_cache_dir, monkeypatch):
        """First fetch should capture ETag into the cache."""

        def handler(request):
            return httpx.Response(200, html=HTML, headers={"ETag": '"v1"'})

        _patched_client(monkeypatch, handler)
        cache = PageCache(tmp_cache_dir, ttl=3600)

        r = await fetch("http://example.com/thing", cache=cache)
        assert r.ok
        assert cache.validators_for("http://example.com/thing", "readable") == {
            "If-None-Match": '"v1"',
        }

    async def test_last_modified_stored(self, tmp_cache_dir, monkeypatch):
        def handler(request):
            return httpx.Response(
                200,
                html=HTML,
                headers={"Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT"},
            )

        _patched_client(monkeypatch, handler)
        cache = PageCache(tmp_cache_dir, ttl=3600)

        r = await fetch("http://example.com/thing", cache=cache)
        assert r.ok
        validators = cache.validators_for("http://example.com/thing", "readable")
        assert validators["If-Modified-Since"] == "Wed, 21 Oct 2015 07:28:00 GMT"

    async def test_revalidation_sends_if_none_match(self, tmp_cache_dir, monkeypatch):
        """After TTL expiry, next fetch should send If-None-Match and accept 304."""
        state = {"count": 0, "last_headers": {}}

        def handler(request):
            state["count"] += 1
            state["last_headers"] = dict(request.headers)
            if state["count"] == 1:
                return httpx.Response(200, html=HTML, headers={"ETag": '"v1"'})
            # Second request should carry If-None-Match
            return httpx.Response(304)

        _patched_client(monkeypatch, handler)

        # Seed cache with a 1-hour entry
        cache = PageCache(tmp_cache_dir, ttl=3600)
        r1 = await fetch("http://example.com/thing", cache=cache)
        assert r1.ok
        assert not r1.from_cache

        # Now use a TTL=0 cache (same dir) — entry is stale but validators remain
        stale = PageCache(tmp_cache_dir, ttl=0)
        r2 = await fetch("http://example.com/thing", cache=stale)

        # Request 2 should have been made (TTL expired)
        assert state["count"] == 2
        # It should have carried the If-None-Match header
        assert state["last_headers"].get("if-none-match") == '"v1"'
        # And on 304 we should have served from cache
        assert r2.from_cache
        assert r2.ok
        assert "first paragraph" not in r2.content or "Plenty of body text" in r2.content

    def test_prune_expired(self, tmp_cache_dir):
        cache = PageCache(tmp_cache_dir, ttl=3600)
        cache.put("http://a.com/", "readable", {"content": "a"})

        # Now shrink TTL and prune
        cache_stale = PageCache(tmp_cache_dir, ttl=0)
        # Sleep briefly so the entry is stale
        import time

        time.sleep(0.01)
        removed = cache_stale.prune_expired()
        assert removed == 1
