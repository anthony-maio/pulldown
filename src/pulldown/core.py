"""
Core fetch + extract pipeline.

Detail levels (agent picks one):
    minimal   – title + plain text, no links/images. Smallest token count.
    readable  – article body as Markdown with links. Default.
    full      – full-page Markdown including nav/sidebar/footer.
    raw       – raw HTML, no extraction (for custom parsing).
"""

from __future__ import annotations

import asyncio
import enum
import ipaddress
import logging
import socket
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("pulldown")

# Reasonable default cap: 10 MiB. Large enough for real pages, small enough
# to stop runaway responses from OOM-ing the process.
DEFAULT_MAX_BYTES = 10 * 1024 * 1024

# ---------------------------------------------------------------------------
# Detail enum
# ---------------------------------------------------------------------------

class Detail(str, enum.Enum):
    """How much content to extract."""
    minimal = "minimal"
    readable = "readable"
    full = "full"
    raw = "raw"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class FetchResult:
    """Holds the output of a single fetch."""
    url: str
    status_code: int
    content: str
    title: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    from_cache: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status_code < 400

    def __str__(self) -> str:
        if self.error:
            return f"[ERROR] {self.url}: {self.error}"
        lines = self.content.count("\n") + 1
        chars = len(self.content)
        return f"[{self.status_code}] {self.url} ({chars} chars, {lines} lines, {self.elapsed_ms:.0f}ms)"


# ---------------------------------------------------------------------------
# HTTP defaults — browser-like
# ---------------------------------------------------------------------------

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


# ---------------------------------------------------------------------------
# URL validation / SSRF guards
# ---------------------------------------------------------------------------

_ALLOWED_SCHEMES = frozenset(("http", "https"))


class UrlNotAllowedError(ValueError):
    """Raised when a URL is rejected by the SSRF / scheme guard."""


def _is_private_host(host: str) -> bool:
    """Return True if host resolves to a private, loopback, link-local, or reserved address."""
    # First: the host string itself may be an IP literal.
    try:
        ip = ipaddress.ip_address(host)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        pass

    # Otherwise resolve the hostname and check every answer.
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        # If we can't resolve it, let httpx fail with its own error.
        return False
    for info in infos:
        sockaddr = info[4]
        addr = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])  # strip IPv6 zone id
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


def _validate_url(url: str, *, allow_private_addresses: bool) -> None:
    """Validate scheme + (optionally) reject private addresses. Raises UrlNotAllowedError."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UrlNotAllowedError(
            f"scheme {parsed.scheme!r} not allowed (only http/https)"
        )
    if not parsed.hostname:
        raise UrlNotAllowedError("URL has no host")
    if not allow_private_addresses and _is_private_host(parsed.hostname):
        raise UrlNotAllowedError(
            f"host {parsed.hostname!r} resolves to a private/loopback address; "
            "pass allow_private_addresses=True to override"
        )


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

_METADATA_KEYS = ("author", "date", "sitename", "description", "categories", "tags", "language")


def _metadata_from_document(doc: Any) -> dict[str, Any]:
    """Pull metadata from a trafilatura Document (2.x) or dict (1.x)."""
    if doc is None:
        return {}
    out: dict[str, Any] = {}
    for key in _METADATA_KEYS:
        value = None
        if hasattr(doc, key):
            value = getattr(doc, key, None)
        elif isinstance(doc, dict):
            value = doc.get(key)
        if value:
            out[key] = value
    return out


def _title_from_document(doc: Any) -> str | None:
    if doc is None:
        return None
    if hasattr(doc, "title"):
        return doc.title or None
    if isinstance(doc, dict):
        return doc.get("title")
    return None


def _extract_minimal(html: str, url: str) -> tuple[str, str | None, dict]:
    """Title + plain text only."""
    import trafilatura
    text = trafilatura.extract(
        html,
        output_format="txt",
        include_links=False,
        include_images=False,
        include_tables=True,
        include_comments=False,
        url=url,
    )
    title = _title_from_lxml(html)
    return (text or ""), title, {}


def _extract_readable(html: str, url: str) -> tuple[str, str | None, dict]:
    """Article body as Markdown with links."""
    import trafilatura
    md = trafilatura.extract(
        html,
        output_format="markdown",
        include_links=True,
        include_images=True,
        include_tables=True,
        include_comments=False,
        url=url,
    )
    title = _title_from_lxml(html)
    meta_info: dict[str, Any] = {}
    try:
        doc = trafilatura.bare_extraction(html, url=url, with_metadata=True)
        meta_info = _metadata_from_document(doc)
        if not title:
            title = _title_from_document(doc)
    except Exception as e:
        logger.debug("metadata extraction failed for %s: %s", url, e)
    return (md or ""), title, meta_info


def _extract_full(html: str, url: str) -> tuple[str, str | None, dict]:
    """Full-page Markdown, including boilerplate."""
    from html_to_markdown import convert
    result = convert(html)
    meta: dict[str, Any] = {}
    title: str | None = None
    if isinstance(result, dict):
        md = result.get("content", "")
        doc_meta = (result.get("metadata") or {}).get("document") or {}
        for k in ("title", "description", "author", "language"):
            v = doc_meta.get(k)
            if v:
                meta[k] = v
        title = doc_meta.get("title")
    else:
        md = str(result)
    if not title:
        title = _title_from_lxml(html)
    return md, title, meta


def _title_from_lxml(html: str) -> str | None:
    try:
        from lxml import etree
        tree = etree.HTML(html)
        if tree is None:
            return None
        titles = tree.xpath("//title/text()")
        if titles:
            return str(titles[0]).strip()
    except Exception as e:
        logger.debug("title extraction failed: %s", e)
    return None


EXTRACTORS = {
    Detail.minimal: _extract_minimal,
    Detail.readable: _extract_readable,
    Detail.full: _extract_full,
}


# ---------------------------------------------------------------------------
# Rendering (optional Chromium via Playwright)
# ---------------------------------------------------------------------------

async def _render_page(
    url: str,
    *,
    wait_ms: int = 2000,
    scroll_count: int = 0,
    scroll_delay_ms: int = 500,
    timeout_ms: int = 30000,
    headers: dict[str, str] | None = None,
    cookies: list[dict] | None = None,
    proxy: str | None = None,
) -> tuple[str, int]:
    """Render a page with Playwright, return (html, status_code)."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise ImportError(
            "Playwright is required for rendering. Install with: "
            "pip install 'pulldown[render]' && playwright install chromium"
        ) from e

    # Playwright's cookie format requires either `url` or (`domain` and `path`).
    # Normalize bare {name, value} entries by attaching the target URL.
    normalized_cookies: list[dict] | None = None
    if cookies:
        normalized_cookies = []
        for c in cookies:
            entry = dict(c)
            if "url" not in entry and "domain" not in entry:
                entry["url"] = url
            normalized_cookies.append(entry)

    async with async_playwright() as p:
        launch_args: dict[str, Any] = {"headless": True}
        if proxy:
            launch_args["proxy"] = {"server": proxy}

        browser = await p.chromium.launch(**launch_args)
        context_args: dict[str, Any] = {}
        if headers:
            context_args["extra_http_headers"] = headers
        context = await browser.new_context(**context_args)

        if normalized_cookies:
            await context.add_cookies(normalized_cookies)

        page = await context.new_page()
        status_code = 0
        try:
            response = await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            if response is not None:
                status_code = response.status
            await page.wait_for_timeout(wait_ms)

            for _ in range(scroll_count):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(scroll_delay_ms)

            html = await page.content()
        finally:
            await browser.close()

    return html, status_code


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------

async def fetch(
    url: str,
    *,
    detail: Detail | str = Detail.readable,
    render: bool = False,
    headers: dict[str, str] | None = None,
    cookies: list[dict] | None = None,
    proxy: str | None = None,
    timeout: float = 30.0,
    verify_ssl: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allow_private_addresses: bool = False,
    retries: int = 0,
    retry_delay_ms: int = 500,
    # render-specific options
    render_wait_ms: int = 2000,
    render_scroll_count: int = 0,
    render_scroll_delay_ms: int = 500,
    render_timeout_ms: int = 30000,
    # cache
    cache: Any | None = None,
) -> FetchResult:
    """
    Fetch a URL and extract content as Markdown.

    Parameters
    ----------
    url : str
        The URL to fetch.
    detail : Detail | str
        Extraction detail level: minimal, readable, full, raw.
    render : bool
        If True, use Playwright/Chromium for JS rendering.
    headers : dict, optional
        Extra HTTP headers (merged with browser-like defaults).
    cookies : list[dict], optional
        Cookies for requests or Playwright context.
    proxy : str, optional
        HTTP proxy URL.
    timeout : float
        HTTP timeout in seconds (httpx path).
    verify_ssl : bool
        Verify TLS certificates. Default True.
    max_bytes : int
        Reject responses larger than this. Default 10 MiB.
    allow_private_addresses : bool
        If False (default), refuse to fetch URLs that resolve to
        loopback / RFC1918 / link-local / metadata-service addresses.
    retries : int
        Number of retry attempts on transient errors (408, 429, 5xx,
        connection failures). Default 0.
    retry_delay_ms : int
        Initial retry delay in milliseconds; doubles with each attempt
        (exponential backoff). Default 500.
    render_wait_ms : int
        Milliseconds to wait after page load (render path).
    render_scroll_count : int
        Number of viewport scrolls for lazy content (render path).
    render_scroll_delay_ms : int
        Delay between scrolls in ms (render path).
    render_timeout_ms : int
        Total page load timeout in ms (render path).
    cache : PageCache, optional
        Cache instance for validator-based caching.

    Returns
    -------
    FetchResult
    """
    if isinstance(detail, str):
        detail = Detail(detail)

    t0 = time.perf_counter()

    # --- URL validation ---
    try:
        _validate_url(url, allow_private_addresses=allow_private_addresses)
    except UrlNotAllowedError as e:
        elapsed = (time.perf_counter() - t0) * 1000
        return FetchResult(
            url=url,
            status_code=0,
            content="",
            elapsed_ms=elapsed,
            error=f"URL blocked: {e}",
        )

    # --- check cache (fast path) ---
    if cache is not None:
        cached = cache.get(url, detail.value)
        if cached is not None:
            logger.debug("cache hit: %s", url)
            return FetchResult(
                url=url,
                status_code=200,
                content=cached["content"],
                title=cached.get("title"),
                meta=cached.get("meta", {}),
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                from_cache=True,
            )

    # --- fetch HTML ---
    merged_headers = {**DEFAULT_HEADERS, **(headers or {})}
    # Add validator headers for a conditional request if we have them.
    if cache is not None:
        merged_headers.update(cache.validators_for(url, detail.value))

    html = ""
    status_code = 0
    etag = None
    last_modified = None

    # Status codes that warrant a retry (with exponential backoff).
    _RETRY_STATUS = {408, 429, 500, 502, 503, 504}

    try:
        if render:
            html, status_code = await _render_page(
                url,
                wait_ms=render_wait_ms,
                scroll_count=render_scroll_count,
                scroll_delay_ms=render_scroll_delay_ms,
                timeout_ms=render_timeout_ms,
                headers=headers,
                cookies=cookies,
                proxy=proxy,
            )
            if len(html.encode("utf-8", errors="ignore")) > max_bytes:
                elapsed = (time.perf_counter() - t0) * 1000
                return FetchResult(
                    url=url,
                    status_code=status_code,
                    content="",
                    elapsed_ms=elapsed,
                    error=f"rendered content exceeds max_bytes ({max_bytes})",
                )
        else:
            async with httpx.AsyncClient(
                headers=merged_headers,
                timeout=httpx.Timeout(timeout),
                follow_redirects=True,
                proxy=proxy,
                verify=verify_ssl,
                trust_env=True,
            ) as client:
                if cookies:
                    cookie_str = "; ".join(
                        f"{c['name']}={c['value']}"
                        for c in cookies
                        if "name" in c and "value" in c
                    )
                    if cookie_str:
                        client.headers["Cookie"] = cookie_str

                attempt = 0
                delay_ms = retry_delay_ms
                while True:
                    try:
                        resp = await client.get(url)
                    except (httpx.TransportError, httpx.TimeoutException):
                        if attempt >= retries:
                            raise
                        await asyncio.sleep(delay_ms / 1000.0)
                        attempt += 1
                        delay_ms *= 2
                        continue

                    if resp.status_code in _RETRY_STATUS and attempt < retries:
                        await asyncio.sleep(delay_ms / 1000.0)
                        attempt += 1
                        delay_ms *= 2
                        continue
                    break

                status_code = resp.status_code

                # Conditional-request 304: serve the stale entry, refresh timestamp.
                if status_code == 304 and cache is not None:
                    cached = cache.get_stale(url, detail.value)
                    if cached is not None:
                        cache.touch(url, detail.value)
                        elapsed = (time.perf_counter() - t0) * 1000
                        return FetchResult(
                            url=url,
                            status_code=200,
                            content=cached["content"],
                            title=cached.get("title"),
                            meta=cached.get("meta", {}),
                            elapsed_ms=elapsed,
                            from_cache=True,
                        )

                resp.raise_for_status()

                # Content-Length preflight check.
                cl = resp.headers.get("content-length")
                if cl is not None:
                    try:
                        if int(cl) > max_bytes:
                            elapsed = (time.perf_counter() - t0) * 1000
                            return FetchResult(
                                url=url,
                                status_code=status_code,
                                content="",
                                elapsed_ms=elapsed,
                                error=(
                                    f"Content-Length {cl} exceeds max_bytes ({max_bytes})"
                                ),
                            )
                    except ValueError:
                        pass

                # Decoded-body size check.
                body = resp.content
                if len(body) > max_bytes:
                    elapsed = (time.perf_counter() - t0) * 1000
                    return FetchResult(
                        url=url,
                        status_code=status_code,
                        content="",
                        elapsed_ms=elapsed,
                        error=f"response body ({len(body)} bytes) exceeds max_bytes ({max_bytes})",
                    )
                html = resp.text
                etag = resp.headers.get("etag")
                last_modified = resp.headers.get("last-modified")

    except httpx.HTTPStatusError as e:
        elapsed = (time.perf_counter() - t0) * 1000
        return FetchResult(
            url=url,
            status_code=e.response.status_code,
            content="",
            elapsed_ms=elapsed,
            error=f"HTTP {e.response.status_code}: {e.response.reason_phrase}",
        )
    except Exception as e:
        logger.debug("fetch failed for %s", url, exc_info=True)
        elapsed = (time.perf_counter() - t0) * 1000
        return FetchResult(
            url=url,
            status_code=0,
            content="",
            elapsed_ms=elapsed,
            error=str(e) or type(e).__name__,
        )

    # --- extract ---
    if detail == Detail.raw:
        elapsed = (time.perf_counter() - t0) * 1000
        title = _title_from_lxml(html)
        result = FetchResult(
            url=url,
            status_code=status_code,
            content=html,
            title=title,
            elapsed_ms=elapsed,
        )
    else:
        extractor = EXTRACTORS[detail]
        try:
            content, title, meta = extractor(html, url)
        except Exception as e:
            logger.warning("extraction failed for %s: %s, falling back to full", url, e)
            try:
                content, title, meta = _extract_full(html, url)
            except Exception:
                logger.debug("fallback extraction also failed", exc_info=True)
                content, title, meta = html, _title_from_lxml(html), {}

        elapsed = (time.perf_counter() - t0) * 1000
        result = FetchResult(
            url=url,
            status_code=status_code,
            content=content or "",
            title=title,
            meta=meta,
            elapsed_ms=elapsed,
        )

    # --- populate cache ---
    if cache is not None and result.ok:
        cache.put(
            url,
            detail.value,
            {
                "content": result.content,
                "title": result.title,
                "meta": result.meta,
            },
            etag=etag,
            last_modified=last_modified,
        )

    return result


# ---------------------------------------------------------------------------
# Batch fetch
# ---------------------------------------------------------------------------

async def fetch_many(
    urls: Sequence[str],
    *,
    detail: Detail | str = Detail.readable,
    render: bool = False,
    concurrency: int = 5,
    headers: dict[str, str] | None = None,
    cookies: list[dict] | None = None,
    proxy: str | None = None,
    timeout: float = 30.0,
    verify_ssl: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allow_private_addresses: bool = False,
    retries: int = 0,
    retry_delay_ms: int = 500,
    cache: Any | None = None,
    **render_kwargs: Any,
) -> list[FetchResult]:
    """
    Fetch multiple URLs concurrently, preserving input order.

    Parameters
    ----------
    urls : sequence of str
        URLs to fetch.
    concurrency : int
        Maximum number of concurrent fetches. Default 5.
    (other params same as fetch)

    Returns
    -------
    list[FetchResult] in the same order as input urls.
    """
    sem = asyncio.Semaphore(concurrency)

    async def _guarded(url: str) -> FetchResult:
        async with sem:
            return await fetch(
                url,
                detail=detail,
                render=render,
                headers=headers,
                cookies=cookies,
                proxy=proxy,
                timeout=timeout,
                verify_ssl=verify_ssl,
                max_bytes=max_bytes,
                allow_private_addresses=allow_private_addresses,
                retries=retries,
                retry_delay_ms=retry_delay_ms,
                cache=cache,
                **render_kwargs,
            )

    tasks = [_guarded(u) for u in urls]
    return list(await asyncio.gather(*tasks))
