"""Robots requests must obey the same address policy as crawled pages."""

import httpx
import pytest

from pulldown import crawl


@pytest.mark.parametrize("target", ["http://127.0.0.1/private", "http://169.254.169.254/latest"])
async def test_robots_redirect_cannot_fetch_private_address(monkeypatch, target):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, text="<html><body><p>Public page.</p></body></html>")

    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: original(*a, **{**kw, "transport": transport}))
    await crawl("https://public.example/", max_pages=1)
    assert "https://public.example/robots.txt" in seen
    assert target not in seen


async def test_public_robots_redirect_still_controls_crawl(monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(302, headers={"location": "/rules.txt"})
        if request.url.path == "/rules.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(200, text="<html><body>Must not fetch.</body></html>")

    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: original(*a, **{**kw, "transport": transport}))
    result = await crawl("https://public.example/", max_pages=1)
    assert seen == ["https://public.example/robots.txt", "https://public.example/rules.txt"]
    assert not result.pages


async def test_explicit_local_crawl_keeps_local_robots_support(monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(302, headers={"location": "/rules.txt"})
        return httpx.Response(200, text="User-agent: *\nDisallow: /\n")

    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: original(*a, **{**kw, "transport": transport}))
    await crawl("http://127.0.0.1/", max_pages=1, allow_private_addresses=True)
    assert seen == ["http://127.0.0.1/robots.txt", "http://127.0.0.1/rules.txt"]
