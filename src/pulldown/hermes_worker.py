"""One bounded fetch for the Hermes plugin, running in Pulldown's environment."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

from .cache import PageCache
from .core import fetch
from .hermes_plugin import PluginError, _MAX_INPUT_BYTES, _arguments, _integer


def _error(code, message):
    return {"ok": False, "error": message, "code": code}


def _fetch_error(result, render):
    message = (result.error or "").lower()
    if render and any(term in message for term in (
        "playwright is required", "executable doesn't exist", "playwright install", "playwright 1.48",
    )):
        return _error("render_unavailable", "Install pulldown[render] and run python -m playwright install chromium in the plugin's Python environment, then retry.")
    if "redirect" in message and render:
        return _error("render_redirect", "Rendering does not follow redirects. Use the page's canonical URL or retry with render=false.")
    if "private/loopback" in message or "url blocked" in message or "rendered request blocked" in message:
        return _error("url_blocked", "The requested page or a redirected/subresource URL was blocked by Pulldown's public-address policy. Use a public HTTP(S) URL.")
    if "max_bytes" in message:
        return _error("response_too_large", "The page exceeds Pulldown's 2 MiB content limit.")
    if result.status_code >= 400:
        return _error("http_error", f"The page returned HTTP {result.status_code}.")
    return _error("fetch_failed", "Could not fetch or extract this page. Check the URL and network; for JavaScript pages, check the rendering environment.")


async def run(request):
    if not isinstance(request, dict) or set(request) - {"url", "detail", "render", "max_chars", "cache_dir", "timeout_seconds"}:
        raise PluginError("invalid_arguments", "Unexpected worker arguments.")
    arguments = _arguments({key: request[key] for key in ("url", "detail", "render", "max_chars") if key in request})
    timeout = _integer(request.get("timeout_seconds", 60), "timeout_seconds", 5, 120)
    cache_dir = request.get("cache_dir")
    if not isinstance(cache_dir, str) or not Path(cache_dir).is_absolute():
        raise PluginError("invalid_configuration", "The plugin cache directory must be absolute.")
    result = await asyncio.wait_for(fetch(
        arguments["url"], detail=arguments["detail"], render=arguments["render"],
        timeout=min(30, timeout - 2), render_timeout_ms=min(30000, (timeout - 2) * 1000),
        render_wait_ms=500, render_scroll_count=0, retries=0,
        max_bytes=2 * 1024 * 1024, verify_ssl=True, allow_private_addresses=False,
        cache=PageCache(cache_dir, ttl=3600),
    ), timeout=timeout - 1)
    if not result.ok:
        return {**_fetch_error(result, arguments["render"]), "url": arguments["url"], "status_code": result.status_code}
    content = result.content[:arguments["max_chars"]]
    routing = result.meta.get("routing", {})
    # Routing metadata is diagnostic, not a second unbounded content channel.
    safe_routing = {
        key: value[:200] if isinstance(value, str) else value
        for key, value in routing.items()
        if key in {"page_type", "source", "confidence", "abstained", "strategy_used", "quality_grade", "render_recommended"}
        and (value is None or type(value) in {str, bool, int, float})
    } if isinstance(routing, dict) else {}
    return {
        "ok": True, "url": arguments["url"], "source_url": result.url,
        "status_code": result.status_code, "title": (result.title or "")[:500],
        "content": content, "content_chars": len(result.content), "returned_chars": len(content),
        "truncated": len(content) < len(result.content), "detail": arguments["detail"],
        "rendered": arguments["render"], "from_cache": result.from_cache,
        "routing": safe_routing, "elapsed_ms": round(result.elapsed_ms, 1),
    }


def main():
    try:
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            raise PluginError("invalid_arguments", "Request exceeds its input limit.")
        result = asyncio.run(run(json.loads(raw)))
    except PluginError as exc:
        result = _error(exc.code, str(exc))
    except (ValueError, UnicodeError):
        result = _error("invalid_arguments", "Expected a valid JSON request.")
    except asyncio.TimeoutError:
        result = _error("fetch_timeout", "Pulldown exceeded the request timeout.")
    except Exception:
        result = _error("fetch_failed", "Pulldown could not complete the request. Check its runtime and cache directory.")
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
