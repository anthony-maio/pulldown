"""
MCP server for pulldown.

Exposes fetch, fetch_many, and crawl as MCP tools so any
MCP-enabled agent can fetch web pages as clean Markdown.

Run:
    python -m pulldown.mcp_server              # stdio (default)
    MCP_TRANSPORT=http python -m pulldown.mcp_server   # http, 127.0.0.1:8080

Environment variables:
    MCP_TRANSPORT           "stdio" (default) or "http"
    MCP_HOST                HTTP bind address. Default "127.0.0.1".
                            Set to "0.0.0.0" only if you understand the
                            exposure and have network-level authz in place.
    MCP_PORT                HTTP port. Default 8080.
    PULLDOWN_CACHE_DIR      Enable caching with this directory.
    PULLDOWN_CACHE_TTL      Cache TTL in seconds. Default 3600.
    PULLDOWN_ALLOW_PRIVATE  Set to "1" to allow private/loopback addresses.
    PULLDOWN_ROUTING_LOG    Append routing diagnostics JSONL to this path.
"""

from __future__ import annotations

import json
import os
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:
    raise ImportError("MCP SDK required. Install with: pip install 'pulldown[mcp]'") from e

from .cache import PageCache
from .core import fetch as _fetch
from .core import fetch_many as _fetch_many
from .crawl import crawl as _crawl

mcp = FastMCP("pulldown")

_cache_dir = os.environ.get("PULLDOWN_CACHE_DIR")
_cache_ttl = int(os.environ.get("PULLDOWN_CACHE_TTL", "3600"))
_cache = PageCache(_cache_dir, ttl=_cache_ttl) if _cache_dir else None
_allow_private = os.environ.get("PULLDOWN_ALLOW_PRIVATE", "").strip() in ("1", "true", "yes")
_routing_log_path = os.environ.get("PULLDOWN_ROUTING_LOG")


@mcp.tool()
async def pulldown(
    url: str,
    detail: str = "readable",
    render: bool = False,
    scroll_count: int = 0,
    timeout: float = 30.0,
    include_meta: bool = False,
) -> str:
    """
    Fetch a URL and return clean Markdown content.

    Args:
        url: The URL to fetch.
        detail: Extraction level — "minimal" (plain text, smallest),
                "readable" (auto-routed readable output, default),
                "structured" (hierarchy-preserving dashboard/listing output),
                "full" (entire page as Markdown), or "raw" (raw HTML).
        render: If true, use headless Chromium to render JavaScript.
                Only needed for SPAs or JS-heavy pages.
        scroll_count: Number of viewport scrolls for lazy-loaded content
                      (only applies when render=true).
        timeout: HTTP timeout in seconds.
        include_meta: If true, return JSON including metadata and routing info.

    Returns:
        Markdown content of the page, or an error message.
    """
    result = await _fetch(
        url,
        detail=detail,
        render=render,
        timeout=timeout,
        render_scroll_count=scroll_count,
        allow_private_addresses=_allow_private,
        cache=_cache,
        routing_log_path=_routing_log_path,
    )

    if not result.ok:
        return f"Error fetching {url}: {result.error}"

    if include_meta:
        return json.dumps(
            {
                "url": result.url,
                "title": result.title,
                "content": result.content,
                "meta": result.meta,
                "ok": result.ok,
            },
            ensure_ascii=False,
            indent=2,
        )

    parts = []
    if result.title:
        parts.append(f"# {result.title}\n")
    parts.append(result.content)
    return "\n".join(parts)


@mcp.tool()
async def pulldown_many(
    urls: list[str],
    detail: str = "readable",
    render: bool = False,
    concurrency: int = 5,
    timeout: float = 30.0,
    include_meta: bool = False,
) -> str:
    """
    Fetch multiple URLs concurrently and return their Markdown content.

    Args:
        urls: List of URLs to fetch.
        detail: Extraction level (minimal/readable/structured/full/raw).
        render: Use headless Chromium rendering.
        concurrency: Max concurrent fetches (default 5).
        timeout: HTTP timeout in seconds.
        include_meta: Include metadata and routing diagnostics in each result.

    Returns:
        JSON array of results, each with url, title, content, ok, error.
    """
    results = await _fetch_many(
        urls,
        detail=detail,
        render=render,
        concurrency=concurrency,
        timeout=timeout,
        allow_private_addresses=_allow_private,
        cache=_cache,
        routing_log_path=_routing_log_path,
    )

    output = []
    for r in results:
        entry: dict[str, Any] = {
            "url": r.url,
            "title": r.title,
            "ok": r.ok,
        }
        if r.ok:
            entry["content"] = r.content
        else:
            entry["error"] = r.error
        if include_meta:
            entry["meta"] = r.meta
        output.append(entry)

    return json.dumps(output, ensure_ascii=False, indent=2)


@mcp.tool()
async def pulldown_crawl(
    start_url: str,
    detail: str = "readable",
    max_pages: int = 20,
    max_depth: int = 2,
    concurrency: int = 3,
    render: bool = False,
    include_pattern: str | None = None,
    exclude_pattern: str | None = None,
    include_meta: bool = False,
) -> str:
    """
    Crawl a site starting from a URL and return all pages as Markdown.

    Stays within the same domain and subpath. Useful for pulling
    documentation sites, blogs, or any multi-page content.

    Args:
        start_url: URL to start crawling from.
        detail: Extraction level (minimal/readable/structured/full/raw).
        max_pages: Maximum pages to fetch (default 20, capped at 100).
        max_depth: Maximum link depth (default 2).
        concurrency: Concurrent fetches (default 3).
        render: Use headless Chromium rendering.
        include_pattern: Regex — only crawl URLs matching this.
        exclude_pattern: Regex — skip URLs matching this.
        include_meta: If true, return JSON including page metadata and routing.

    Returns:
        Combined Markdown of all crawled pages with headers and separators.
    """
    max_pages = min(max_pages, 100)

    result = await _crawl(
        start_url,
        detail=detail,
        max_pages=max_pages,
        max_depth=max_depth,
        concurrency=concurrency,
        render=render,
        include_pattern=include_pattern,
        exclude_pattern=exclude_pattern,
        allow_private_addresses=_allow_private,
        cache=_cache,
        routing_log_path=_routing_log_path,
    )

    if include_meta:
        return json.dumps(
            {
                "start_url": result.start_url,
                "urls_discovered": result.urls_discovered,
                "urls_fetched": result.urls_fetched,
                "urls_skipped": result.urls_skipped,
                "elapsed_ms": round(result.elapsed_ms, 1),
                "pages": [
                    {
                        "url": page.url,
                        "title": page.title,
                        "content": page.content,
                        "ok": page.ok,
                        "error": page.error,
                        "meta": page.meta,
                    }
                    for page in result.pages
                ],
            },
            ensure_ascii=False,
            indent=2,
        )

    parts = [
        f"<!-- Crawl: {result.urls_fetched} pages fetched, "
        f"{result.urls_discovered} discovered, "
        f"{result.elapsed_ms:.0f}ms -->\n",
    ]
    parts.append(result.to_markdown())
    return "\n".join(parts)


def _resolve_bind() -> tuple[str, int]:
    """Return (host, port) for HTTP transport. Defaults to loopback."""
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8080"))
    return host, port


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "http":
        host, port = _resolve_bind()
        mcp.run(transport="http", host=host, port=port)
    else:
        mcp.run(transport="stdio")
