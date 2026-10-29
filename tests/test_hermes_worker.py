from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace

import pytest

from pulldown.core import FetchResult
from pulldown import hermes_worker as worker


def request(tmp_path, **values):
    return {"url": "https://example.com/article", "cache_dir": str(tmp_path / "cache"), **values}


@pytest.mark.asyncio
async def test_worker_preserves_fetch_guards_and_reports_truncation(monkeypatch, tmp_path):
    calls = []

    async def fetch(url, **kwargs):
        calls.append((url, kwargs))
        return FetchResult(url=url, status_code=200, title="An article", content="z" * 2001,
                           meta={"routing": {"page_type": "article", "strategy_used": "readable",
                                             "render_recommended": False, "unbounded": "x" * 100000}})

    monkeypatch.setattr(worker, "fetch", fetch)
    result = await worker.run(request(tmp_path, max_chars=1000))
    assert result["content"] == "z" * 1000
    assert result["content_chars"] == 2001
    assert result["returned_chars"] == 1000
    assert result["truncated"] is True
    assert result["source_url"] == "https://example.com/article"
    assert result["routing"] == {"page_type": "article", "strategy_used": "readable", "render_recommended": False}
    _, kwargs = calls[0]
    assert kwargs["allow_private_addresses"] is False
    assert kwargs["verify_ssl"] is True
    assert kwargs["render"] is False
    assert kwargs["max_bytes"] == 2 * 1024 * 1024
    assert kwargs["retries"] == 0
    assert "proxy" not in kwargs
    assert "headers" not in kwargs


@pytest.mark.asyncio
async def test_private_address_is_blocked_without_network(monkeypatch, tmp_path):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: pytest.fail("Private address must not reach HTTP client"))
    result = await worker.run(request(tmp_path, url="http://127.0.0.1/secret"))
    assert result["ok"] is False
    assert result["code"] == "url_blocked"
    assert "secret" not in result["error"]


@pytest.mark.asyncio
async def test_explicit_render_passes_option_and_reports_browser_setup(monkeypatch, tmp_path):
    async def fetch(url, **kwargs):
        assert kwargs["render"] is True
        return FetchResult(url=url, status_code=0, content="", error="BrowserType.launch: Executable doesn't exist at /private/browser/path")

    monkeypatch.setattr(worker, "fetch", fetch)
    result = await worker.run(request(tmp_path, render=True))
    assert result["code"] == "render_unavailable"
    assert "python -m playwright install chromium" in result["error"]
    assert "/private/browser/path" not in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,message,code", [
    (403, "HTTP 403: Forbidden secret", "http_error"),
    (0, "response body exceeds max_bytes (secret)", "response_too_large"),
    (0, "TLS failed while reading /private/config", "fetch_failed"),
])
async def test_errors_do_not_return_raw_exception_text(monkeypatch, tmp_path, status, message, code):
    async def fetch(url, **kwargs):
        return FetchResult(url=url, status_code=status, content="", error=message)

    monkeypatch.setattr(worker, "fetch", fetch)
    result = await worker.run(request(tmp_path))
    assert result["code"] == code
    assert "secret" not in result["error"]
    assert "/private/config" not in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"allow_private_addresses": True}, {"cache_dir": "relative"},
    {"timeout_seconds": 121}, {"url": "https://user:pass@example.com"},
])
async def test_worker_revalidates_its_boundary(monkeypatch, tmp_path, changes):
    monkeypatch.setattr(worker, "fetch", lambda *args, **kwargs: pytest.fail("Invalid request reached fetch"))
    with pytest.raises(worker.PluginError):
        await worker.run(request(tmp_path, **changes))


@pytest.mark.parametrize("input_bytes", [b"not json", b"[]", b"x" * (worker._MAX_INPUT_BYTES + 1)],
                         ids=["malformed", "array", "oversized"])
def test_main_always_returns_json_for_invalid_input(monkeypatch, input_bytes):
    output = BytesIO()
    monkeypatch.setattr(worker.sys, "stdin", SimpleNamespace(buffer=BytesIO(input_bytes)))
    monkeypatch.setattr(worker.sys, "stdout", SimpleNamespace(buffer=output))
    worker.main()
    result = json.loads(output.getvalue())
    assert result["ok"] is False
    assert result["code"] == "invalid_arguments"
