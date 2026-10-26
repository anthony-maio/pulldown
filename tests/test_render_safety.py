from __future__ import annotations

import importlib
from types import SimpleNamespace

import httpx
import pytest

from pulldown import Detail, PageCache, fetch

core = importlib.import_module("pulldown.core")


async def test_render_bypasses_http_cache_and_does_not_replace_it(monkeypatch, tmp_path):
    url = "http://127.0.0.1/dashboard"
    http_html = "<html><body><main>HTTP shell</main></body></html>"
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=http_html))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(**kwargs, transport=transport))
    rendered_options = []

    async def render_page(url, **kwargs):
        rendered_options.append(kwargs)
        return f"<html><body>Rendered after {kwargs['wait_ms']}ms</body></html>", 200

    monkeypatch.setattr(core, "_render_page", render_page)
    cache = PageCache(tmp_path / "cache")
    first = await fetch(url, detail=Detail.raw, cache=cache, allow_private_addresses=True)
    rendered = await fetch(url, detail=Detail.raw, render=True, render_wait_ms=10, cache=cache, allow_private_addresses=True)
    later = await fetch(url, detail=Detail.raw, render=True, render_wait_ms=20, cache=cache, allow_private_addresses=True)
    cached_http = await fetch(url, detail=Detail.raw, cache=cache, allow_private_addresses=True)

    assert first.ok and rendered.ok and later.ok and cached_http.ok
    assert "Rendered after 10ms" in rendered.content
    assert "Rendered after 20ms" in later.content
    assert not rendered.from_cache and not later.from_cache
    assert cached_http.from_cache and cached_http.content == http_html
    assert all(options["allow_private_addresses"] for options in rendered_options)


class FakeResponse:
    def __init__(self, status=200, headers=None):
        self.status = status
        self.headers = headers or {"content-type": "text/html"}
        self.disposed = False

    async def dispose(self):
        self.disposed = True


class FakeRoute:
    def __init__(self, url, responses=None, headers=None, method="GET"):
        self.request = SimpleNamespace(url=url, headers=headers or {}, method=method)
        self.responses = responses or {url: FakeResponse()}
        self.sent = []
        self.fulfilled = None
        self.aborted = None

    async def fetch(self, **kwargs):
        assert kwargs["max_redirects"] == 0
        self.sent.append(kwargs)
        return self.responses[kwargs["url"]]

    async def fulfill(self, **kwargs):
        self.fulfilled = kwargs

    async def abort(self, reason):
        self.aborted = reason


@pytest.mark.parametrize("target", ["http://127.0.0.1/private", "http://169.254.169.254/latest", "file:///secret"])
async def test_render_request_blocks_private_or_unsupported_redirect_before_contact(target):
    public = "https://8.8.8.8/start"
    route = FakeRoute(public, {public: FakeResponse(302, {"location": target})})
    with pytest.raises(core.UrlNotAllowedError):
        await core._route_render_request(route, allow_private_addresses=False, timeout_ms=1000)
    assert [request["url"] for request in route.sent] == [public]
    assert route.fulfilled is None
    assert route.responses[public].disposed


@pytest.mark.parametrize("target", ["http://127.0.0.1/api", "http://[::1]/image", "file:///secret"])
async def test_render_subrequest_is_blocked_before_contact(target):
    route = FakeRoute(target)
    with pytest.raises(core.UrlNotAllowedError):
        await core._route_render_request(route, allow_private_addresses=False, timeout_ms=1000)
    assert not route.sent


async def test_render_public_redirect_is_rejected_without_forwarding_credentials():
    first, final = "https://8.8.8.8/start", "https://1.1.1.1/final"
    route = FakeRoute(first, {
        first: FakeResponse(302, {"location": final}),
        final: FakeResponse(200, {"content-security-policy": "default-src 'self'"}),
    }, headers={"Authorization": "Bearer secret", "Cookie": "session=secret", "Proxy-Authorization": "secret", "Accept": "text/html"})
    with pytest.raises(core.UrlNotAllowedError, match="canonical URL: https://1.1.1.1/final"):
        await core._route_render_request(route, allow_private_addresses=False, timeout_ms=1000)
    assert [request["url"] for request in route.sent] == [first]
    assert route.fulfilled is None
    assert route.responses[first].disposed


async def test_render_keeps_existing_csp_and_adds_worker_and_socket_restrictions():
    url = "https://8.8.8.8/page"
    route = FakeRoute(url, {url: FakeResponse(200, {"content-security-policy": "default-src 'self'"})})
    await core._route_render_request(route, allow_private_addresses=False, timeout_ms=1000)
    assert "default-src 'self'" in route.fulfilled["headers"]["content-security-policy"]
    assert "worker-src 'none'" in route.fulfilled["headers"]["content-security-policy"]
    assert "connect-src http: https:" in route.fulfilled["headers"]["content-security-policy"]
    assert all(response.disposed for response in route.responses.values())


async def test_render_same_origin_redirect_is_rejected_before_target_is_contacted():
    first, final = "https://8.8.8.8/start", "https://8.8.8.8/final"
    route = FakeRoute(first, {first: FakeResponse(307, {"location": "/final"}), final: FakeResponse()}, headers={"cookie": "session=kept", "authorization": "Bearer kept"}, method="POST")
    with pytest.raises(core.UrlNotAllowedError, match="canonical URL"):
        await core._route_render_request(route, allow_private_addresses=False, timeout_ms=1000)
    assert [request["url"] for request in route.sent] == [first]


async def test_render_private_override_still_blocks_non_http_schemes():
    local = FakeRoute("http://127.0.0.1/fixture")
    await core._route_render_request(local, allow_private_addresses=True, timeout_ms=1000)
    assert local.fulfilled is not None
    unsupported = FakeRoute("file:///secret")
    with pytest.raises(core.UrlNotAllowedError):
        await core._route_render_request(unsupported, allow_private_addresses=True, timeout_ms=1000)
    assert not unsupported.sent


async def test_render_redirect_loop_is_bounded():
    url = "https://8.8.8.8/loop"
    route = FakeRoute(url, {url: FakeResponse(302, {"location": url})})
    with pytest.raises(core.UrlNotAllowedError, match="redirect"):
        await core._route_render_request(route, allow_private_addresses=False, timeout_ms=1000)
    assert len(route.sent) == 1


async def test_rendered_crawl_does_not_poison_http_cache(monkeypatch, tmp_path):
    from pulldown import crawl
    crawl_module = importlib.import_module("pulldown.crawl")
    url = "http://127.0.0.1/"
    cache = PageCache(tmp_path / "cache")
    cache.put(url, "raw", {"content": "HTTP only"})
    options = []

    async def render_page(url, **kwargs):
        options.append(kwargs)
        return "<html><body>Rendered content</body></html>", 200

    monkeypatch.setattr(crawl_module, "_render_page", render_page)
    result = await crawl(url, render=True, detail=Detail.raw, allow_private_addresses=True, respect_robots=False, max_pages=1, cache=cache)
    assert result.pages[0].ok
    assert "Rendered content" in result.pages[0].content
    assert cache.get(url, "raw")["content"] == "HTTP only"
    assert options[0]["allow_private_addresses"] is True


def fake_browser(monkeypatch, *, subrequest=None, setup_error=False):
    api = importlib.import_module("playwright.async_api")
    state = SimpleNamespace(closed=False, context_options=None, routes=[], socket_closed=False)

    async def socket_close(**kwargs):
        state.socket_closed = kwargs["code"] == 1008

    async def goto(url, **kwargs):
        route = FakeRoute(url)
        state.routes.append(route)
        await state.http_handler(route)
        if subrequest:
            extra = FakeRoute(subrequest)
            state.routes.append(extra)
            await state.http_handler(extra)
        await state.socket_handler(SimpleNamespace(close=socket_close))
        return SimpleNamespace(status=200)

    async def no_op(*args, **kwargs):
        pass

    async def content():
        return "<html><body>Rendered page</body></html>"

    async def new_page():
        assert state.http_handler and state.socket_handler
        return SimpleNamespace(goto=goto, wait_for_timeout=no_op, evaluate=no_op, content=content)

    async def route(pattern, handler):
        assert pattern == "**/*"
        state.http_handler = handler

    async def route_web_socket(pattern, handler):
        assert pattern == "**/*"
        state.socket_handler = handler

    async def new_context(**kwargs):
        state.context_options = kwargs
        if setup_error:
            raise RuntimeError("context setup failed")
        return SimpleNamespace(route=route, route_web_socket=route_web_socket, add_cookies=no_op, new_page=new_page)

    async def close():
        state.closed = True

    async def launch(**kwargs):
        return SimpleNamespace(new_context=new_context, close=close)

    class FakePlaywright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(api, "async_playwright", FakePlaywright)
    return state


async def test_renderer_disables_service_workers_and_sockets_before_loading(monkeypatch):
    state = fake_browser(monkeypatch)
    html, status = await core._render_page("https://8.8.8.8/", wait_ms=0)
    assert status == 200 and "Rendered page" in html
    assert state.context_options["service_workers"] == "block"
    assert state.socket_closed
    assert state.closed
    assert "worker-src 'none'" in state.routes[0].fulfilled["headers"]["content-security-policy"]


async def test_renderer_reports_blocked_subrequest_and_closes_browser(monkeypatch):
    state = fake_browser(monkeypatch, subrequest="http://127.0.0.1/private")
    with pytest.raises(core.UrlNotAllowedError, match="Rendered request blocked"):
        await core._render_page("https://8.8.8.8/", wait_ms=0)
    assert not state.routes[1].sent
    assert state.routes[1].aborted == "blockedbyclient"
    assert state.closed


async def test_renderer_closes_browser_if_context_setup_fails(monkeypatch):
    state = fake_browser(monkeypatch, setup_error=True)
    with pytest.raises(RuntimeError, match="context setup failed"):
        await core._render_page("https://8.8.8.8/", wait_ms=0)
    assert state.closed
