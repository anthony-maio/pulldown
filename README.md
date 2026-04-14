# pulldown

Pull down web pages as clean Markdown for LLM agents.

- HTTP-first with browser-like defaults
- Optional Chromium rendering for JS-heavy pages
- Four detail levels: `minimal`, `readable`, `full`, `raw`
- Core installs decode Brotli-compressed pages correctly
- Concurrent batch fetching with `fetch_many()`
- Bounded site crawling with `robots.txt` support and per-domain politeness
- Validator-based caching (ETag / Last-Modified) with atomic writes
- SSRF guards: private/loopback/metadata addresses blocked by default
- Response size caps and transient-error retries
- CLI, Python API, and MCP server

## Install

```bash
pip install pulldown                 # core
pip install 'pulldown[render]'       # + Playwright (Chromium rendering)
pip install 'pulldown[mcp]'          # + MCP server
pip install 'pulldown[all]'          # everything
```

Core installs include Brotli support, so `br`-compressed HTML is decoded before
`minimal`, `readable`, `full`, or `raw` processing.

For rendered pages, also run `playwright install chromium` once.

## Quick Start

### CLI

```bash
pulldown get https://example.com
pulldown get https://example.com --detail minimal
pulldown get https://example.com --render --scroll 3
pulldown crawl https://docs.example.com --max-pages 20 --delay-ms 200
pulldown bench https://example.com --runs 5
pulldown cache stats
```

### Python

```python
import asyncio
from pulldown import fetch, fetch_many, crawl, Detail, PageCache

async def main():
    # Single fetch
    result = await fetch("https://example.com", detail=Detail.readable)
    print(result.title, result.content)

    # Batch fetch with caching
    cache = PageCache(ttl=3600)
    results = await fetch_many(
        ["https://a.com", "https://b.com"],
        concurrency=5,
        cache=cache,
        retries=2,
    )

    # Crawl a docs site
    crawl_result = await crawl(
        "https://docs.example.com/",
        max_pages=50,
        max_depth=2,
        respect_robots=True,
        per_domain_delay_ms=200,
    )
    markdown = crawl_result.to_markdown()

asyncio.run(main())
```

### MCP

Add to your client config (e.g. Claude Desktop):

```json
{
  "mcpServers": {
    "pulldown": {
      "command": "python",
      "args": ["-m", "pulldown.mcp_server"],
      "env": {
        "PULLDOWN_CACHE_DIR": "~/.cache/pulldown"
      }
    }
  }
}
```

Environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `MCP_HOST` | `127.0.0.1` | Bind address for HTTP transport |
| `MCP_PORT` | `8080` | Port for HTTP transport |
| `PULLDOWN_CACHE_DIR` | _unset_ | Enable caching to this directory |
| `PULLDOWN_CACHE_TTL` | `3600` | Cache TTL in seconds |
| `PULLDOWN_ALLOW_PRIVATE` | `0` | Set to `1` to allow private addresses |

## Detail Levels

| Level | Output | Best for |
|---|---|---|
| `minimal` | Title + plain text | Lowest-token summarisation |
| `readable` | Clean Markdown with links | RAG, reading, structured landing pages (default) |
| `full` | Full-page Markdown incl. chrome | Pages without clear article body |
| `raw` | Untouched HTML | Custom parsing downstream |

## Security

pulldown refuses to fetch URLs that resolve to private, loopback,
link-local, or cloud-metadata addresses by default. This prevents
LLM-driven SSRF into internal services (e.g., AWS metadata at
`169.254.169.254`, Redis on `localhost:6379`). Override with
`allow_private_addresses=True` if you understand the risk.

Responses above 10 MiB are rejected by default (`max_bytes` parameter).

Only `http` and `https` schemes are accepted; `file:`, `ftp:`, etc. are
rejected.

## License

MIT
