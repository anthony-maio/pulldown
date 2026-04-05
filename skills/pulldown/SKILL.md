---
name: pulldown
description: Fetch web pages as clean Markdown for downstream LLM processing. Use when the user asks to "fetch", "download", "pull", "scrape", "read", or "extract" web content — including individual URLs, documentation sites, blog posts, and articles — and needs the result as LLM-friendly Markdown. Also use for bulk URL ingestion, building RAG corpora from public docs, or turning a list of links into Markdown. Handles both static HTML (fast path) and JavaScript-rendered pages (Chromium via Playwright). Ships a CLI (`pulldown get|crawl|bench`), a Python API (`fetch`, `fetch_many`, `crawl`), and an MCP server exposing the same tools.
---

# Using pulldown

`pulldown` turns web pages into clean Markdown for LLM consumption. It has an opinionated pipeline: HTTP-first, optional Chromium rendering, four detail levels, validator-based caching, and an SSRF-safe default.

## Decision tree

```
Need one page?           → fetch(url, detail=...)
Need many pages?         → fetch_many(urls, concurrency=5)
Need a whole site?       → crawl(start_url, max_pages=...)
Page is JS-heavy (SPA)?  → add render=True
Repeated fetches?        → pass a PageCache
```

## Detail levels

The agent picks the detail level. **Default to `readable`**. Only change when the default doesn't fit:

| Level | Use when |
|---|---|
| `minimal` | You need the smallest possible token count. Output is title + plain text, no links. |
| `readable` | You want the article body as Markdown with links. This is what you want 90% of the time. |
| `full` | The page is mostly navigation/sidebar/chrome with no clear article body (e.g. marketing landing pages, reference indexes). |
| `raw` | You need the raw HTML for custom parsing. |

**Do not default to `full`.** It includes navigation and footer boilerplate, which costs tokens without adding meaning for most pages.

## When to render (`render=True`)

Rendering launches Chromium and is **~100× slower** than HTTP fetch. Only use it when:
- The page is a single-page app (React/Vue/Svelte) and returns a mostly-empty shell on HTTP.
- You see `<div id="root"></div>` or similar in the raw HTML with no body content.
- The HTTP fetch returns `readable` content that's clearly missing the article.

Do **not** render first to be safe. Try HTTP, check if the content looks complete, then escalate to render.

When rendering, use `scroll_count=N` to trigger lazy-loaded content (infinite scroll, lazy images). Start with 3-5 scrolls.

## Caching

When repeatedly fetching the same URLs (e.g. in a conversation loop), always pass a `PageCache`. It stores validators (ETag, Last-Modified) and issues conditional requests — a 304 response means no re-extraction cost.

```python
from pulldown import PageCache, fetch
cache = PageCache(ttl=3600)  # default dir is platform-appropriate
result = await fetch(url, cache=cache)
```

Don't construct a new `PageCache` on every call — reuse one instance across a session.

## Crawling a site

Use `crawl()` when the user wants a whole docs site or blog archive, not individual pages. Key parameters:

- `max_pages` — hard cap. Set to the smallest number that will satisfy the user.
- `max_depth` — link depth from start URL. `2` is usually enough for docs; `3` for blogs.
- `respect_robots=True` — default. Do not disable unless the user explicitly asks.
- `per_domain_delay_ms` — politeness delay. Set ≥ 200 for unfamiliar sites; set ≥ 500 for sites that might rate-limit.
- `include_pattern` / `exclude_pattern` — regex filters. Use these to skip changelogs, archives, tag indexes.

`crawl()` stays within the same domain and path prefix of `start_url`. If you want the whole `docs.example.com`, start at `https://docs.example.com/`. If you only want `/api/` subtree, start at `https://docs.example.com/api/`.

## Security defaults (do not work around without cause)

- **Private/loopback addresses are blocked.** `http://127.0.0.1/`, `http://10.0.0.1/`, `http://169.254.169.254/` (cloud metadata) are refused by default. Only pass `allow_private_addresses=True` when the user is explicitly testing local services and understands the risk.
- **Only `http`/`https` schemes accepted.** `file:`, `ftp:` etc. are rejected.
- **10 MiB response cap** by default. Override with `max_bytes=` for large archives.

When the user says "fetch localhost", first confirm the intent before flipping `allow_private_addresses`.

## Errors

`fetch()` and `fetch_many()` never raise on network errors — they return a `FetchResult` with `.ok == False` and `.error` populated. Always check `result.ok` before using `result.content`.

```python
result = await fetch(url)
if not result.ok:
    # handle result.error — it's a short human-readable string
    ...
else:
    print(result.title, result.content)
```

For transient errors (5xx, 429, connection failures), pass `retries=2, retry_delay_ms=500` — the client will retry with exponential backoff.

## CLI cheat sheet

```bash
# single page
pulldown get https://example.com
pulldown get https://example.com -d minimal -j   # JSON output

# JS-heavy page with lazy loading
pulldown get https://spa.example.com --render --scroll 5

# whole docs site, polite, cached
pulldown crawl https://docs.example.com/ \
    --max-pages 50 --max-depth 2 --delay-ms 250 \
    --output-dir ./docs-md/

# with caching
pulldown get https://example.com --cache-dir ./cache --cache-ttl 3600
```

## MCP server

When connected via MCP, three tools are exposed:

- `pulldown(url, detail, render, scroll_count, timeout)` — single page
- `pulldown_many(urls, detail, render, concurrency, timeout)` — batch
- `pulldown_crawl(start_url, detail, max_pages, max_depth, concurrency, render, include_pattern, exclude_pattern)` — bounded crawl

The MCP server's HTTP transport binds to `127.0.0.1` by default. It inherits cache settings from `PULLDOWN_CACHE_DIR` environment variable.

## Common pitfalls

- **Don't call `fetch` in a tight loop** without `fetch_many` or a semaphore. Use `fetch_many(urls, concurrency=5)` instead.
- **Don't render when you don't need to.** Check HTTP output first.
- **Don't ignore `result.ok`.** Treat error results explicitly.
- **Don't set `max_depth=10`** expecting to find the thing you want. Narrow by `include_pattern` instead.
- **Don't disable robots.txt** to get around a crawl refusal — that's the signal the site doesn't want to be crawled.
