"""
Core fetch + extract pipeline.

Detail levels (agent picks one):
    minimal    – title + plain text, no links/images. Smallest token count.
    readable   – article body as Markdown with links. Default.
    structured – hierarchy-preserving Markdown for dashboards/listings/tables.
    full       – full-page Markdown including nav/sidebar/footer.
    raw        – raw HTML, no extraction (for custom parsing).
"""

from __future__ import annotations

import asyncio
import enum
import ipaddress
import logging
import re
import socket
import time
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

try:
    import brotli
except ImportError:  # pragma: no cover - core installs should provide this
    brotli = None

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
    structured = "structured"
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
        raise UrlNotAllowedError(f"scheme {parsed.scheme!r} not allowed (only http/https)")
    if not parsed.hostname:
        raise UrlNotAllowedError("URL has no host")
    if not allow_private_addresses and _is_private_host(parsed.hostname):
        raise UrlNotAllowedError(
            f"host {parsed.hostname!r} resolves to a private/loopback address; "
            "pass allow_private_addresses=True to override"
        )


async def _get_following_safe_redirects(
    client: httpx.AsyncClient,
    url: str,
    *,
    allow_private_addresses: bool,
    max_redirects: int = 10,
) -> httpx.Response:
    """GET that manually follows redirects, validating each target for SSRF.

    Raises UrlNotAllowedError if any redirect destination is a private/blocked
    host. Raises httpx.TooManyRedirects if the chain exceeds max_redirects.
    """
    current = url
    for _ in range(max_redirects + 1):
        resp = await client.get(current)
        if not resp.is_redirect:
            return resp
        location = resp.headers.get("location", "")
        if not location:
            return resp
        next_url = urljoin(current, location)
        # Raises UrlNotAllowedError before we ever open a connection to the target.
        _validate_url(next_url, allow_private_addresses=allow_private_addresses)
        current = next_url
    raise httpx.TooManyRedirects(
        f"Exceeded {max_redirects} redirects", request=resp.request  # type: ignore[possibly-undefined]
    )


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

_METADATA_KEYS = ("author", "date", "sitename", "description", "categories", "tags", "language")
_HEADING_LINE_RE = re.compile(r"^#{1,6}\s*$")
_EMPTY_LINK_LINE_RE = re.compile(r"^\[\s*\]\([^)]+\)$")
_BLOCK_LINK_HEADINGS = " or ".join(f"self::h{i}" for i in range(1, 7))
_BOILERPLATE_TOKENS = frozenset(
    {
        "nav",
        "navbar",
        "footer",
        "menu",
        "mobile",
        "sidebar",
        "drawer",
        "breadcrumb",
        "social",
        "share",
        "sharing",
    }
)
_STRUCTURED_TABLE_ROW_LIMIT = 8


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


def _squash_whitespace(text: str) -> str:
    return " ".join(text.split())


def _markdown_heading_count(markdown: str) -> int:
    return sum(1 for line in markdown.splitlines() if line.lstrip().startswith("#"))


def _markdown_list_item_count(markdown: str) -> int:
    return sum(1 for line in markdown.splitlines() if line.lstrip().startswith("- "))


def _markdown_image_count(markdown: str) -> int:
    return sum(1 for line in markdown.splitlines() if line.lstrip().startswith("!["))


def _body_node(tree: Any) -> Any | None:
    bodies = tree.xpath("//body")
    return bodies[0] if bodies else None


def _page_stats(node: Any) -> dict[str, int]:
    if node is None:
        return {
            "paragraphs": 0,
            "tables": 0,
            "rows": 0,
            "list_items": 0,
            "headings": 0,
            "articles": 0,
            "sections": 0,
            "words": 0,
        }

    text = _squash_whitespace(" ".join(node.itertext()))
    return {
        "paragraphs": len(node.xpath(".//p")),
        "tables": len(node.xpath(".//table")),
        "rows": len(node.xpath(".//table//tr")),
        "list_items": len(node.xpath(".//ul/li | .//ol/li")),
        "headings": len(node.xpath(".//*[self::h1 or self::h2 or self::h3 or self::h4]")),
        "articles": len(node.xpath(".//article")),
        "sections": len(node.xpath(".//section")),
        "words": len(text.split()),
    }


def _node_depth(node: Any) -> int:
    depth = 0
    current = node
    while current is not None and isinstance(current.tag, str):
        depth += 1
        current = current.getparent()
    return depth


def _structured_root_score(stats: dict[str, int]) -> int:
    return (
        stats["rows"] * 20
        + stats["tables"] * 40
        + min(stats["articles"], 40) * 14
        + min(stats["sections"], 20) * 10
        + min(stats["headings"], 40) * 6
        + min(stats["list_items"], 60) * 2
    )


def _classify_page(html: str) -> dict[str, Any]:
    from lxml import etree

    tree = etree.HTML(html)
    if tree is None:
        return {
            "page_type": "generic",
            "readerable": False,
            "extraction_quality": "low",
        }

    body = _body_node(tree)
    if body is None:
        body = tree
    readable_landmark = _select_readable_landmark(tree)
    content_root = readable_landmark if readable_landmark is not None else body
    content_stats = _page_stats(content_root)
    document_stats = _page_stats(body)

    dashboard_like = (
        document_stats["rows"] >= 12
        or (document_stats["tables"] >= 2 and document_stats["rows"] >= 8)
        or document_stats["articles"] >= 8
        or (document_stats["rows"] >= 6 and document_stats["headings"] >= 4)
        or (
            document_stats["tables"] >= 1
            and document_stats["rows"] >= 8
            and (document_stats["articles"] >= 2 or document_stats["headings"] >= 3)
        )
        or (
            document_stats["headings"] >= 6
            and document_stats["list_items"] >= 12
            and content_stats["paragraphs"] <= 6
        )
    )
    article_like = (
        not dashboard_like
        and content_stats["paragraphs"] >= 3
        and content_stats["words"] >= 80
        and content_stats["rows"] < 6
        and content_stats["tables"] == 0
        and content_stats["list_items"] < 20
    )

    if dashboard_like:
        return {
            "page_type": "dashboard",
            "readerable": False,
            "extraction_quality": (
                "high"
                if document_stats["rows"] >= 40 or document_stats["articles"] >= 12
                else "medium"
            ),
        }
    if article_like:
        return {
            "page_type": "article",
            "readerable": True,
            "extraction_quality": "high" if content_stats["words"] >= 350 else "medium",
        }
    return {
        "page_type": "generic",
        "readerable": content_stats["paragraphs"] >= 3 and content_stats["words"] >= 80,
        "extraction_quality": "medium" if content_stats["words"] >= 120 else "low",
    }


def _select_readable_landmark(tree: Any) -> Any | None:
    candidates = tree.xpath("//main | //*[@role='main'] | //article")
    if candidates:
        return max(
            candidates,
            key=lambda node: (
                len(node.xpath(".//section")) * 20
                + len(node.xpath(".//*[self::h2 or self::h3 or self::h4]")) * 10
                + len(node.xpath(".//ul/li | .//ol/li")) * 3
                + len(node.xpath(".//dt")) * 4
                + len(_squash_whitespace(" ".join(node.itertext()))),
            ),
        )
    return _body_node(tree)


def _select_structured_landmark(tree: Any) -> Any | None:
    body = _body_node(tree)
    candidates: list[Any] = []

    if body is not None:
        candidates.append(body)
    candidates.extend(tree.xpath("//main | //*[@role='main']"))

    readable_landmark = _select_readable_landmark(tree)
    node = readable_landmark
    while node is not None and isinstance(node.tag, str):
        candidates.append(node)
        if node.tag.lower() == "body":
            break
        node = node.getparent()

    seen: set[int] = set()
    unique_candidates: list[Any] = []
    for candidate in candidates:
        marker = id(candidate)
        if marker in seen:
            continue
        seen.add(marker)
        unique_candidates.append(candidate)

    if not unique_candidates:
        return body

    scored: list[tuple[Any, dict[str, int], int, int]] = []
    for candidate in unique_candidates:
        stats = _page_stats(candidate)
        scored.append((candidate, stats, _structured_root_score(stats), _node_depth(candidate)))

    best_score = max(score for _, _, score, _ in scored)
    eligible = [item for item in scored if item[2] >= max(best_score * 0.8, 20)]
    chosen = min(eligible or scored, key=lambda item: (item[1]["words"], -item[3]))
    return chosen[0]


def _node_tokens(node: Any) -> set[str]:
    values: list[str] = []
    for attr in ("class", "id", "role", "aria-label"):
        value = node.get(attr)
        if value:
            values.extend(re.split(r"[^a-z0-9]+", value.lower()))
    return {value for value in values if value}


def _remove_node(node: Any) -> None:
    parent = node.getparent()
    if parent is not None:
        parent.remove(node)


def _unwrap_or_rewrite_block_links(root: Any, base_url: str) -> None:
    from lxml import etree

    for node in list(root.xpath(".//a[*]")):
        href = (node.get("href") or "").strip()
        if href:
            node.set("href", urljoin(base_url, href))

        for image in list(node.xpath(".//img | .//picture | .//*[local-name()='svg']")):
            _remove_node(image)

        heading = next(iter(node.xpath(f".//*[{_BLOCK_LINK_HEADINGS}]")), None)
        if heading is not None and not heading.xpath("./a"):
            heading_text = _squash_whitespace(" ".join(heading.itertext()))
            if heading_text:
                for child in list(heading):
                    heading.remove(child)
                heading.text = None
                link = etree.Element("a", href=href or base_url)
                link.text = heading_text
                heading.append(link)

        node.tag = "div"
        for attr in ("href", "target", "rel"):
            node.attrib.pop(attr, None)


def _is_short_link_cluster(node: Any) -> bool:
    if node.tag not in {"div", "p"}:
        return False
    children = [child for child in node if isinstance(child.tag, str)]
    if not children:
        return False
    child_tags = {child.tag for child in children}
    if not child_tags <= {"a", "button", "span"}:
        return False
    text = _squash_whitespace(" ".join(node.itertext()))
    return len(text.split()) <= 12


def _clean_landmark(root: Any, base_url: str) -> None:
    for node in list(
        root.xpath(
            ".//*[self::nav or self::footer or self::aside or self::script or self::style or "
            "self::template or self::noscript or self::button or local-name()='svg']"
        )
    ):
        _remove_node(node)

    for node in list(root.xpath(".//*[@hidden or @aria-hidden='true']")):
        _remove_node(node)

    for node in list(root.xpath(".//img | .//picture")):
        _remove_node(node)

    for node in list(root.xpath(".//*[@href]")):
        href = node.get("href")
        if href:
            node.set("href", urljoin(base_url, href))

    _unwrap_or_rewrite_block_links(root, base_url)

    for node in list(root.xpath(".//*")):
        if not isinstance(node.tag, str):
            continue
        if _node_tokens(node) & _BOILERPLATE_TOKENS:
            _remove_node(node)
            continue
        if _is_short_link_cluster(node):
            _remove_node(node)


def _extract_cleaned_landmark_markdown(html: str, url: str) -> str:
    from html_to_markdown import convert
    from lxml import etree

    tree = etree.HTML(html)
    if tree is None:
        return ""

    landmark = _select_readable_landmark(tree)
    if landmark is None:
        return ""

    cleaned = deepcopy(landmark)
    _clean_landmark(cleaned, url)
    cleaned_html = etree.tostring(cleaned, encoding="unicode", method="html")
    if not cleaned_html.strip():
        return ""

    result = convert(cleaned_html)
    if isinstance(result, dict):
        return str(result.get("content", "") or "")
    return str(result)


def _cell_texts(row: Any) -> list[str]:
    cells = row.xpath("./th | ./td")
    return [_squash_whitespace(" ".join(cell.itertext())) for cell in cells]


def _heading_prefix(level: int) -> str:
    return "#" * max(2, min(level, 4))


def _extract_structured(html: str, url: str) -> tuple[str, str | None, dict]:
    from lxml import etree

    tree = etree.HTML(html)
    if tree is None:
        return "", _title_from_lxml(html), {}

    landmark = _select_structured_landmark(tree)
    if landmark is None:
        return "", _title_from_lxml(html), {}

    cleaned = deepcopy(landmark)
    _clean_landmark(cleaned, url)

    lines: list[str] = []

    def walk(node: Any) -> None:
        if not isinstance(node.tag, str):
            return

        tag = node.tag.lower()

        if tag in {"h1", "h2", "h3", "h4"}:
            text = _squash_whitespace(" ".join(node.itertext()))
            if text:
                level = int(tag[1])
                lines.append(f"{_heading_prefix(level)} {text}")
                lines.append("")
            return

        if tag == "p":
            text = _squash_whitespace(" ".join(node.itertext()))
            if text:
                lines.append(text)
                lines.append("")
            return

        if tag in {"ul", "ol"}:
            items = node.xpath("./li")
            for item in items[:12]:
                text = _squash_whitespace(" ".join(item.itertext()))
                if text:
                    lines.append(f"- {text}")
            if items:
                lines.append("")
            return

        if tag == "table":
            rows = node.xpath(".//tr")
            header_cells = _cell_texts(rows[0]) if rows else []
            body_rows = rows[1:] if len(rows) > 1 else []
            if header_cells:
                lines.append(f"- Table columns: {' | '.join(header_cells)}")
            if body_rows:
                shown = body_rows[:_STRUCTURED_TABLE_ROW_LIMIT]
                lines.append(f"- Showing first {len(shown)} of {len(body_rows)} rows")
                for row in shown:
                    values = _cell_texts(row)
                    if values:
                        trimmed = values[1:] if len(values) > 1 else values
                        lines.append(f"- {' | '.join(trimmed)}")
            lines.append("")
            return

        if tag in {"section", "article", "div", "main", "header", "body"}:
            start = len(lines)
            for child in node:
                walk(child)
            if len(lines) == start:
                text = _squash_whitespace(" ".join(node.itertext()))
                if text and len(text.split()) >= 2:
                    lines.append(text)
                    lines.append("")

    walk(cleaned)

    title = _title_from_lxml(html)
    content = _normalize_readable_markdown("\n".join(lines))
    return content, title, {}


def _should_use_landmark_fallback(primary_markdown: str, fallback_markdown: str) -> bool:
    primary_text = _squash_whitespace(primary_markdown)
    fallback_text = _squash_whitespace(fallback_markdown)

    if not fallback_text:
        return False
    if not primary_text:
        return True
    if len(primary_text) < 200 and len(fallback_text) > len(primary_text) * 2:
        return True

    primary_headings = _markdown_heading_count(primary_markdown)
    fallback_headings = _markdown_heading_count(fallback_markdown)
    primary_lists = _markdown_list_item_count(primary_markdown)
    fallback_lists = _markdown_list_item_count(fallback_markdown)
    primary_images = _markdown_image_count(primary_markdown)
    fallback_images = _markdown_image_count(fallback_markdown)

    if (
        fallback_headings >= max(primary_headings, 3)
        and fallback_lists >= max(primary_lists, 2)
        and primary_images > fallback_images + 2
    ):
        return True

    return (
        fallback_headings >= 3
        and fallback_lists >= 2
        and primary_headings < 2
        and len(fallback_text) >= int(len(primary_text) * 0.75)
    )


def _normalize_readable_markdown(markdown: str) -> str:
    filtered_lines: list[str] = []
    raw_lines = markdown.splitlines()
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i].rstrip()
        stripped = line.strip()

        if _EMPTY_LINK_LINE_RE.match(stripped):
            i += 1
            continue
        if stripped.startswith("![") and "data:image/" in stripped:
            i += 1
            continue
        if _HEADING_LINE_RE.match(stripped) and i + 1 < len(raw_lines):
            next_line = raw_lines[i + 1].strip()
            if next_line and not next_line.startswith(("#", "-", "*", ">", "[")):
                filtered_lines.append(f"{stripped} {next_line}")
                i += 2
                continue

        filtered_lines.append(stripped if stripped else "")
        i += 1

    combined_lines: list[str] = []
    i = 0
    while i < len(filtered_lines):
        line = filtered_lines[i]
        stripped = line.strip()
        next_line = filtered_lines[i + 1].strip() if i + 1 < len(filtered_lines) else ""
        if stripped.startswith("- ") and next_line.startswith("- — "):
            combined_lines.append(f"{stripped} {next_line[2:].strip()}")
            i += 2
            continue
        if stripped and not stripped.startswith(("#", "-", "*", ">", "[")) and next_line.startswith("— "):
            combined_lines.append(f"- {stripped} {next_line}")
            i += 2
            continue

        if stripped.startswith("### ") and " [→ " in stripped:
            stripped = stripped.split(" [→ ", 1)[0]
            combined_lines.append(stripped)
            i += 1
            continue

        if stripped.startswith("- ") and " - [" in stripped:
            first, *rest = stripped.split(" - [")
            combined_lines.append(first)
            combined_lines.extend(f"- [{item}" for item in rest)
            i += 1
            continue

        combined_lines.append(line)
        i += 1

    normalized = "\n".join(combined_lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


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

    fallback_md = _extract_cleaned_landmark_markdown(html, url)
    chosen = fallback_md if _should_use_landmark_fallback(md or "", fallback_md) else (md or "")
    return _normalize_readable_markdown(chosen), title, meta_info


def _decode_response_content(resp: httpx.Response) -> tuple[bytes, str]:
    body = resp.content
    content_encoding = resp.headers.get("content-encoding", "").lower()

    if "br" in content_encoding and brotli is not None:
        try:
            body = brotli.decompress(body)
        except brotli.error:
            pass

    encoding = resp.charset_encoding or resp.encoding or "utf-8"
    return body, body.decode(encoding, errors="replace")


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
    Detail.structured: _extract_structured,
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
        Extraction detail level: minimal, readable, structured, full, raw.
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
                follow_redirects=False,
                proxy=proxy,
                verify=verify_ssl,
                trust_env=True,
            ) as client:
                if cookies:
                    cookie_str = "; ".join(
                        f"{c['name']}={c['value']}" for c in cookies if "name" in c and "value" in c
                    )
                    if cookie_str:
                        client.headers["Cookie"] = cookie_str

                attempt = 0
                delay_ms = retry_delay_ms
                while True:
                    try:
                        resp = await _get_following_safe_redirects(
                            client,
                            url,
                            allow_private_addresses=allow_private_addresses,
                        )
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
                                error=(f"Content-Length {cl} exceeds max_bytes ({max_bytes})"),
                            )
                    except ValueError:
                        pass

                body, html = _decode_response_content(resp)
                if len(body) > max_bytes:
                    elapsed = (time.perf_counter() - t0) * 1000
                    return FetchResult(
                        url=url,
                        status_code=status_code,
                        content="",
                        elapsed_ms=elapsed,
                        error=f"response body ({len(body)} bytes) exceeds max_bytes ({max_bytes})",
                    )
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

    page_analysis = _classify_page(html)
    extraction_detail = detail
    if detail == Detail.readable and page_analysis["page_type"] == "dashboard":
        extraction_detail = Detail.structured

    # --- extract ---
    if detail == Detail.raw:
        elapsed = (time.perf_counter() - t0) * 1000
        title = _title_from_lxml(html)
        result = FetchResult(
            url=url,
            status_code=status_code,
            content=html,
            title=title,
            meta={
                "page_type": page_analysis["page_type"],
                "readerable": page_analysis["readerable"],
                "extraction_quality": page_analysis["extraction_quality"],
                "strategy_used": "raw",
            },
            elapsed_ms=elapsed,
        )
    else:
        extractor = EXTRACTORS[extraction_detail]
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
        meta = {
            **meta,
            "page_type": page_analysis["page_type"],
            "readerable": page_analysis["readerable"],
            "extraction_quality": page_analysis["extraction_quality"],
            "strategy_used": "article" if extraction_detail == Detail.readable else extraction_detail.value,
        }
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
