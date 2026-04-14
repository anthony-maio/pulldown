# Changelog

All notable changes to pulldown will be documented here.

## [0.3.0] - 2026-04-13

### Added
- Regression coverage for structured landing-page extraction using a stable
  `making-minds.ai` fixture.
- Regression coverage for Brotli-compressed HTTP responses to ensure all
  detail levels operate on decoded HTML.

### Changed
- `readable` now falls back to a cleaned `<main>`/content-landmark Markdown
  conversion when article extraction is structurally weak.
- Markdown normalization now repairs split headings, definition-list pairs,
  collapsed list formatting, and empty link stubs commonly produced on
  portfolio and landing pages.
- Core installs now include Brotli support so `br`-compressed pages decode
  correctly before `minimal`, `readable`, `full`, or `raw` processing.

## [0.2.0] - 2026-04-05

### Added
- **SSRF guard**: `fetch()` and `crawl()` refuse URLs resolving to loopback,
  RFC1918, link-local, or metadata-service addresses by default. Override
  with `allow_private_addresses=True`.
- **Scheme guard**: Only `http` and `https` are accepted (no `file:`, `ftp:`,
  etc.).
- **`max_bytes` parameter** (default 10 MiB) on `fetch()` and `crawl()` caps
  response size, checked against `Content-Length` and decoded body length.
- **Validator-based caching**: `PageCache` now stores ETag and Last-Modified,
  and `fetch()` issues conditional requests (`If-None-Match`,
  `If-Modified-Since`) for stale entries, accepting 304 responses.
- **`PageCache.prune_expired()`** for explicit cleanup of TTL-expired entries.
- **`respect_robots=True`** default on `crawl()` — consults origin's
  robots.txt and drops disallowed URLs.
- **`per_domain_delay_ms`** option on `crawl()` for politeness delays.
- **`user_agent`** option on `crawl()` used for both robots matching and
  HTTP requests.
- `MCP_HOST` environment variable for the MCP HTTP transport bind address.
- `PULLDOWN_ALLOW_PRIVATE` environment variable for MCP server.
- `py.typed` marker for type-hint distribution.

### Changed
- **Default cache dir** is now platform-appropriate
  (`%LOCALAPPDATA%\pulldown\cache` on Windows, `~/.cache/pulldown` on Linux,
  `~/Library/Caches/pulldown` on macOS) instead of `/tmp/pulldown-cache`.
- **MCP server HTTP transport defaults to 127.0.0.1** instead of 0.0.0.0.
  Set `MCP_HOST=0.0.0.0` to restore the old behavior.
- **Render path now reports the real HTTP status code** from Playwright's
  response instead of hardcoding 200.
- **Crawler fetches each page exactly once** — previously it fetched twice
  (once for extraction, once for link discovery). Link extraction now uses
  lxml `//a/@href` instead of a regex and uses the same HTML as the
  extractor.
- **`urls_discovered`** now counts unique discoveries instead of every link
  seen.
- **Cache writes are atomic** (temp file + `os.replace`) — concurrent writers
  can no longer corrupt each other.
- **`fetch()` honours `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`** env vars via
  `trust_env=True`.

### Fixed
- `_extract_readable` metadata extraction is compatible with trafilatura
  2.0's `Document` object (the previous code silently failed on
  `bare.get()`).
- Playwright cookie handling attaches `url` to bare `{name, value}` entries
  so `context.add_cookies()` no longer raises.
- `_same_subpath` no longer treats `/docs-alt` as being under `/docs`.
- Corrupt cache files are removed and treated as a miss instead of raising.
- `/tmp` fallback no longer silently creates `C:\tmp\...` on Windows.

### Security
- Private-address guard prevents SSRF into cloud metadata services
  (169.254.169.254), loopback, and RFC1918 ranges by default.
- Response size cap prevents memory exhaustion from hostile large pages.
- MCP HTTP transport no longer exposes the service to the network by
  default.

## [0.1.0] - Initial prototype
