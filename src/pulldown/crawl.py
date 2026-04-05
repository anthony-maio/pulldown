"""
Bounded site crawler.

Given a start URL, discovers and fetches pages within the same
domain/subpath up to a configurable depth and page limit. Pages
are fetched exactly once — links are extracted from the same
HTML we hand to the extractor.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import urllib.robotparser
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

import httpx

from .core import (
    DEFAULT_HEADERS,
    DEFAULT_MAX_BYTES,
    EXTRACTORS,
    Detail,
    FetchResult,
    UrlNotAllowedError,
    _title_from_lxml,
    _validate_url,
)

logger = logging.getLogger("pulldown")


@dataclass
class CrawlResult:
    """Result of a bounded site crawl."""
    start_url: str
    pages: list[FetchResult] = field(default_factory=list)
    urls_discovered: int = 0
    urls_fetched: int = 0
    urls_skipped: int = 0
    elapsed_ms: float = 0.0

    def to_markdown(self, separator: str = "\n\n---\n\n") -> str:
        """Concatenate all pages into one Markdown document."""
        parts = []
        for page in self.pages:
            if page.ok and page.content:
                header = f"# {page.title or page.url}\n\nSource: {page.url}\n\n"
                parts.append(header + page.content)
        return separator.join(parts)

    def __str__(self) -> str:
        ok = sum(1 for p in self.pages if p.ok)
        return (
            f"CrawlResult({self.start_url}): "
            f"{ok}/{self.urls_fetched} pages OK, "
            f"{self.urls_discovered} discovered, "
            f"{self.urls_skipped} skipped, "
            f"{self.elapsed_ms:.0f}ms"
        )


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _same_subpath(base_url: str, candidate_url: str) -> bool:
    """Check if candidate is under the same domain and path prefix."""
    base = urlparse(base_url)
    cand = urlparse(candidate_url)

    if base.netloc != cand.netloc:
        return False

    base_path = base.path.rstrip("/")
    cand_path = cand.path.rstrip("/")

    if not base_path:
        return True
    return cand_path == base_path or cand_path.startswith(base_path + "/")


_SKIP_EXTENSIONS = frozenset((
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".mp3", ".mp4", ".avi", ".mov", ".zip", ".tar", ".gz",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot", ".ico",
    ".xml", ".json", ".rss", ".atom",
))


def _should_skip(url: str) -> bool:
    """Skip non-HTML resources."""
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    return any(path_lower.endswith(ext) for ext in _SKIP_EXTENSIONS)


def _extract_links(html: str, base_url: str) -> set[str]:
    """Extract href values from <a> tags, resolved to absolute URLs."""
    links: set[str] = set()
    try:
        from lxml import html as lxml_html
        doc = lxml_html.fromstring(html)
    except Exception:
        # If parsing fails entirely, return no links.
        return links

    for href in doc.xpath("//a/@href"):
        href = str(href).strip()
        if not href:
            continue
        if href.startswith(("#", "javascript:", "mailto:", "tel:", "data:", "ftp:")):
            continue
        absolute = urljoin(base_url, href)
        absolute, _ = urldefrag(absolute)
        # Only keep http(s)
        if absolute.startswith(("http://", "https://")):
            links.add(absolute)
    return links


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

async def _load_robots(base_url: str, *, headers: dict[str, str], timeout: float,
                       verify_ssl: bool) -> urllib.robotparser.RobotFileParser | None:
    """Fetch + parse robots.txt for base_url's origin. Returns None on any failure."""
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            verify=verify_ssl,
        ) as client:
            resp = await client.get(robots_url)
            if resp.status_code >= 400:
                return None
            text = resp.text
    except Exception:
        return None

    rp = urllib.robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    return rp


# ---------------------------------------------------------------------------
# Single-fetch extractor
# ---------------------------------------------------------------------------

async def _fetch_and_extract(
    url: str,
    *,
    detail: Detail,
    headers: dict[str, str],
    cookies: list[dict] | None,
    proxy: str | None,
    timeout: float,
    verify_ssl: bool,
    max_bytes: int,
    allow_private_addresses: bool,
    cache: Any | None,
) -> tuple[FetchResult, str]:
    """
    Fetch once, extract locally, and return (result, raw_html).
    Avoids the double-fetch problem in the previous implementation.

    Rendering is not supported on this path — rendered crawls route
    through the per-page ``fetch()`` call for simplicity.
    """
    t0 = time.perf_counter()

    try:
        _validate_url(url, allow_private_addresses=allow_private_addresses)
    except UrlNotAllowedError as e:
        elapsed = (time.perf_counter() - t0) * 1000
        return FetchResult(
            url=url, status_code=0, content="", elapsed_ms=elapsed,
            error=f"URL blocked: {e}",
        ), ""

    # Cache fast path — but we still need raw HTML for link discovery.
    # Strategy: check cache; if hit, still fetch raw once for links (cheaper
    # than extracting twice) but only if we might still need to discover links.
    # Actually: callers pass cache=None or handle this externally. For now,
    # when a cache hit exists, return it and an empty raw_html — the caller
    # will then skip link discovery for that page (acceptable: cached pages
    # rarely change their link graph quickly).
    if cache is not None:
        cached = cache.get(url, detail.value)
        if cached is not None:
            elapsed = (time.perf_counter() - t0) * 1000
            return FetchResult(
                url=url, status_code=200,
                content=cached["content"], title=cached.get("title"),
                meta=cached.get("meta", {}), elapsed_ms=elapsed, from_cache=True,
            ), ""

    merged_headers = {**DEFAULT_HEADERS, **headers}
    if cache is not None:
        merged_headers.update(cache.validators_for(url, detail.value))

    html = ""
    status_code = 0
    etag = None
    last_modified = None

    try:
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

            resp = await client.get(url)
            status_code = resp.status_code

            if status_code == 304 and cache is not None:
                cached = cache.get_stale(url, detail.value)
                if cached is not None:
                    cache.touch(url, detail.value)
                    elapsed = (time.perf_counter() - t0) * 1000
                    return FetchResult(
                        url=url, status_code=200,
                        content=cached["content"], title=cached.get("title"),
                        meta=cached.get("meta", {}), elapsed_ms=elapsed, from_cache=True,
                    ), ""

            resp.raise_for_status()

            cl = resp.headers.get("content-length")
            if cl is not None:
                try:
                    if int(cl) > max_bytes:
                        elapsed = (time.perf_counter() - t0) * 1000
                        return FetchResult(
                            url=url, status_code=status_code, content="",
                            elapsed_ms=elapsed,
                            error=f"Content-Length {cl} exceeds max_bytes ({max_bytes})",
                        ), ""
                except ValueError:
                    pass

            body = resp.content
            if len(body) > max_bytes:
                elapsed = (time.perf_counter() - t0) * 1000
                return FetchResult(
                    url=url, status_code=status_code, content="",
                    elapsed_ms=elapsed,
                    error=f"response body ({len(body)} bytes) exceeds max_bytes ({max_bytes})",
                ), ""

            html = resp.text
            etag = resp.headers.get("etag")
            last_modified = resp.headers.get("last-modified")
    except httpx.HTTPStatusError as e:
        elapsed = (time.perf_counter() - t0) * 1000
        return FetchResult(
            url=url, status_code=e.response.status_code, content="",
            elapsed_ms=elapsed,
            error=f"HTTP {e.response.status_code}: {e.response.reason_phrase}",
        ), ""
    except Exception as e:
        logger.debug("crawl fetch failed for %s", url, exc_info=True)
        elapsed = (time.perf_counter() - t0) * 1000
        return FetchResult(
            url=url, status_code=0, content="", elapsed_ms=elapsed,
            error=str(e) or type(e).__name__,
        ), ""

    # Extract
    if detail == Detail.raw:
        title = _title_from_lxml(html)
        content, meta = html, {}
    else:
        try:
            content, title, meta = EXTRACTORS[detail](html, url)
        except Exception:
            logger.debug("extraction failed, falling back", exc_info=True)
            content, title, meta = html, _title_from_lxml(html), {}
        content = content or ""

    elapsed = (time.perf_counter() - t0) * 1000
    result = FetchResult(
        url=url, status_code=status_code, content=content,
        title=title, meta=meta, elapsed_ms=elapsed,
    )

    if cache is not None and result.ok:
        cache.put(url, detail.value, {
            "content": result.content,
            "title": result.title,
            "meta": result.meta,
        }, etag=etag, last_modified=last_modified)

    return result, html


# ---------------------------------------------------------------------------
# Crawl entry point
# ---------------------------------------------------------------------------

async def crawl(
    start_url: str,
    *,
    detail: Detail | str = Detail.readable,
    max_pages: int = 50,
    max_depth: int = 3,
    concurrency: int = 3,
    render: bool = False,
    headers: dict[str, str] | None = None,
    cookies: list[dict] | None = None,
    proxy: str | None = None,
    timeout: float = 30.0,
    verify_ssl: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allow_private_addresses: bool = False,
    cache: Any | None = None,
    include_pattern: str | None = None,
    exclude_pattern: str | None = None,
    respect_robots: bool = True,
    per_domain_delay_ms: int = 0,
    user_agent: str | None = None,
    **render_kwargs: Any,
) -> CrawlResult:
    """
    Crawl a site starting from start_url.

    Stays within the same domain and subpath. Respects ``max_pages``
    and ``max_depth``. When ``respect_robots`` is True (the default),
    consults the origin's robots.txt and drops disallowed URLs.

    Parameters
    ----------
    start_url : str
        The URL to start crawling from.
    detail : Detail | str
        Extraction detail level.
    max_pages : int
        Maximum number of pages to fetch. Default 50.
    max_depth : int
        Maximum link depth from start_url. Default 3.
    concurrency : int
        Max concurrent fetches. Default 3.
    render : bool
        Use Playwright for JS rendering (routes through fetch()).
    include_pattern : str, optional
        Regex — only crawl URLs matching this.
    exclude_pattern : str, optional
        Regex — skip URLs matching this.
    respect_robots : bool
        Consult robots.txt. Default True.
    per_domain_delay_ms : int
        Minimum delay (ms) between requests to the same origin. Default 0.
    user_agent : str, optional
        User-Agent string used for both robots.txt matching and HTTP requests.
    """
    if isinstance(detail, str):
        detail = Detail(detail)

    t0 = time.perf_counter()

    include_re = re.compile(include_pattern) if include_pattern else None
    exclude_re = re.compile(exclude_pattern) if exclude_pattern else None

    ua = user_agent or DEFAULT_HEADERS["User-Agent"]
    req_headers = {**(headers or {}), "User-Agent": ua}

    robots = None
    if respect_robots:
        robots = await _load_robots(
            start_url, headers=req_headers, timeout=timeout, verify_ssl=verify_ssl,
        )

    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(start_url, 0)]
    pages: list[FetchResult] = []
    urls_skipped = 0
    discovered: set[str] = set()
    last_hit: dict[str, float] = defaultdict(float)

    sem = asyncio.Semaphore(concurrency)

    async def _respect_delay(origin: str) -> None:
        if per_domain_delay_ms <= 0:
            return
        now = time.monotonic() * 1000
        wait = per_domain_delay_ms - (now - last_hit[origin])
        if wait > 0:
            await asyncio.sleep(wait / 1000.0)
        last_hit[origin] = time.monotonic() * 1000

    async def _fetch_one(url: str) -> tuple[FetchResult, str]:
        async with sem:
            origin = urlparse(url).netloc
            await _respect_delay(origin)
            if render:
                # Rendered path: use fetch() and accept double-parse cost.
                from .core import fetch as _core_fetch
                r = await _core_fetch(
                    url, detail=detail, render=True,
                    headers=headers, cookies=cookies, proxy=proxy,
                    timeout=timeout, verify_ssl=verify_ssl, max_bytes=max_bytes,
                    allow_private_addresses=allow_private_addresses,
                    cache=cache, **render_kwargs,
                )
                # For rendered pages, link discovery uses the final content
                # if the detail is raw; otherwise we skip discovery from
                # extracted content (caller is typically interested in a
                # single rendered page, not a deep rendered crawl).
                return r, (r.content if detail == Detail.raw else "")
            return await _fetch_and_extract(
                url,
                detail=detail,
                headers=req_headers,
                cookies=cookies,
                proxy=proxy,
                timeout=timeout,
                verify_ssl=verify_ssl,
                max_bytes=max_bytes,
                allow_private_addresses=allow_private_addresses,
                cache=cache,
            )

    while queue and len(pages) < max_pages:
        batch_size = min(concurrency, max_pages - len(pages), len(queue))
        batch: list[tuple[str, int]] = []
        for _ in range(batch_size):
            if not queue:
                break
            url, depth = queue.pop(0)
            if url in visited:
                continue
            if robots is not None and not robots.can_fetch(ua, url):
                urls_skipped += 1
                continue
            visited.add(url)
            batch.append((url, depth))

        if not batch:
            break

        tasks = [_fetch_one(url) for url, _ in batch]
        results = await asyncio.gather(*tasks)

        for (url, depth), (result, raw_html) in zip(batch, results, strict=True):
            pages.append(result)

            if depth >= max_depth or not raw_html:
                continue

            new_links = _extract_links(raw_html, url)
            for link in new_links:
                if link in visited or link in discovered:
                    continue
                if _should_skip(link):
                    urls_skipped += 1
                    continue
                if not _same_subpath(start_url, link):
                    urls_skipped += 1
                    continue
                if include_re and not include_re.search(link):
                    urls_skipped += 1
                    continue
                if exclude_re and exclude_re.search(link):
                    urls_skipped += 1
                    continue
                discovered.add(link)
                queue.append((link, depth + 1))

    elapsed = (time.perf_counter() - t0) * 1000

    return CrawlResult(
        start_url=start_url,
        pages=pages,
        urls_discovered=len(discovered),
        urls_fetched=len(pages),
        urls_skipped=urls_skipped,
        elapsed_ms=elapsed,
    )
