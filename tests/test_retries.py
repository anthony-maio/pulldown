"""Tests for transient-error retry logic."""
from __future__ import annotations

import httpx

from pulldown import fetch

HTML_OK = "<html><head><title>OK</title></head><body><article>" \
          "<p>Recovered content with enough body text for extraction here.</p>" \
          "<p>Second paragraph for trafilatura happiness and extraction.</p>" \
          "</article></body></html>"


def _patch(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
    )


class TestRetries:
    async def test_retries_on_500(self, monkeypatch):
        state = {"count": 0}

        def handler(request):
            state["count"] += 1
            if state["count"] < 3:
                return httpx.Response(500, text="server error")
            return httpx.Response(200, html=HTML_OK)

        _patch(monkeypatch, handler)

        r = await fetch("http://example.com/", retries=3, retry_delay_ms=1)
        assert r.ok
        assert state["count"] == 3

    async def test_retries_on_429(self, monkeypatch):
        state = {"count": 0}

        def handler(request):
            state["count"] += 1
            if state["count"] < 2:
                return httpx.Response(429, text="slow down")
            return httpx.Response(200, html=HTML_OK)

        _patch(monkeypatch, handler)

        r = await fetch("http://example.com/", retries=2, retry_delay_ms=1)
        assert r.ok

    async def test_does_not_retry_on_404(self, monkeypatch):
        state = {"count": 0}

        def handler(request):
            state["count"] += 1
            return httpx.Response(404, text="nope")

        _patch(monkeypatch, handler)

        r = await fetch("http://example.com/", retries=3, retry_delay_ms=1)
        assert not r.ok
        assert state["count"] == 1  # 4xx (other than 408/429) is permanent

    async def test_gives_up_after_max_retries(self, monkeypatch):
        state = {"count": 0}

        def handler(request):
            state["count"] += 1
            return httpx.Response(503)

        _patch(monkeypatch, handler)

        r = await fetch("http://example.com/", retries=2, retry_delay_ms=1)
        assert not r.ok
        # retries=2 means: 1 original + 2 retries = 3 attempts max
        assert state["count"] == 3

    async def test_default_retries_zero_means_single_attempt(self, monkeypatch):
        state = {"count": 0}

        def handler(request):
            state["count"] += 1
            return httpx.Response(503)

        _patch(monkeypatch, handler)

        r = await fetch("http://example.com/")  # no retries kwarg
        assert not r.ok
        assert state["count"] == 1
