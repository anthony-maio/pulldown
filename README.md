# pulldown

Pull down web pages as clean Markdown for LLM agents.

- HTTP-first with browser-like defaults
- Optional Chromium rendering for JS-heavy pages
- Five detail levels: `minimal`, `readable`, `structured`, `full`, `raw`
- Core installs decode Brotli-compressed pages correctly
- Page-type aware routing with nested `meta["routing"]` diagnostics
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
`minimal`, `readable`, `structured`, `full`, or `raw` processing.

Core installs also include `lxml_html_clean`, avoiding the missing-helper import
issue some agent sandboxes hit on older releases.

For rendered pages, also run `playwright install chromium` once.

## A complete first run

[Compare the source and captured outputs in the example gallery](https://pulldown.making-minds.ai/examples/).
It uses a stable article with headings, links, and a service table, plus a second
page whose release cards are inserted by JavaScript. The service data is fictional.

In a fresh Python 3.10+ virtual environment:

```bash
python -m pip install "pulldown==0.4.2"
pulldown get https://pulldown.making-minds.ai/examples/field-notes.html --detail structured --json-output --meta
```

Look for `Release field notes`, the section headings, and the table summary.
Change `--detail` to `minimal`, `readable`, `full`, or `raw` and compare the result
with the source page. Structured tables are summaries; use `full` or `raw` when
every cell matters.

Then try the browser path:

```bash
python -m pip install "pulldown[render]==0.4.2"
python -m playwright install chromium
pulldown get https://pulldown.making-minds.ai/examples/live-board.html --detail readable
pulldown get https://pulldown.making-minds.ai/examples/live-board.html --detail readable --render --wait 100
```

The first result lacks the release cards. The rendered result includes
`RENDERED-BOARD-READY` and the Documentation, Search, and Export cards.
Rendered requests bypass `PageCache`, so a prior HTTP result cannot suppress the
browser run. This example verifies the installed package on known fixtures; it
does not establish extraction quality on every site.

Browser rendering checks HTTP(S) requests, blocks service workers and other
worker execution, and closes WebSockets. It rejects redirects; use the final
canonical URL when rendering. Ordinary HTTP fetching continues to support
guarded redirects. Sites that depend on workers or sockets may not render
completely. Address checks are not a
replacement for process and network isolation when handling untrusted pages;
DNS changes or proxy-side resolution require their own network controls.

## Quick Start

### CLI

```bash
pulldown get https://example.com
pulldown get https://example.com --detail minimal
pulldown get https://example.com --detail structured
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
    print(result.title)
    print(result.meta["routing"])

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
| `PULLDOWN_ROUTING_LOG` | _unset_ | Append per-page routing diagnostics JSONL |

## Detail Levels

| Level | Output | Best for |
|---|---|---|
| `minimal` | Title + plain text | Lowest-token summarisation |
| `readable` | Auto-routed readable Markdown with links | Default. Uses article extraction for narrative pages and routes non-article pages to a better strategy |
| `structured` | Hierarchy-preserving Markdown with summarized tables | Dashboards, listings, landing pages, and table-heavy app views |
| `full` | Full-page Markdown incl. chrome | Pages without clear article body |
| `raw` | Untouched HTML | Custom parsing downstream |

`readable` now routes dashboard and listing pages toward a structured extractor
instead of trying to flatten them into pseudo-articles. Result metadata includes
the detected page type and extraction strategy under `meta["routing"]` so
agents can branch explicitly.

Example routing payload:

```python
{
    "page_type": "listing",
    "source": "rules",
    "confidence": 1.0,
    "abstained": False,
    "strategy_used": "structured",
    "quality_grade": "high",
    "render_recommended": False,
}
```

Use `--routing-log path.jsonl` in the CLI or `routing_log_path="path.jsonl"`
in Python to capture feature vectors, probabilities, fallback decisions, and
quality outcomes for offline retraining.

## Security

pulldown checks resolved addresses and rejects private, loopback,
link-local, or cloud-metadata destinations by default. Ordinary HTTP requests
validate each redirect destination. Rendering rejects redirects and checks its
HTTP(S) subrequests. The checks do not pin the eventual connection address or
control proxy-side DNS; use network isolation when serving untrusted input.
`allow_private_addresses=True` disables the address restriction and is intended
for explicitly trusted local sources, such as the gallery's fixture server.

HTTP responses above 10 MiB are rejected by default (`max_bytes` parameter).
Rendering checks the serialized DOM after loading; this is not a browser memory
or total network-transfer limit.

Only `http` and `https` schemes are accepted; `file:`, `ftp:`, etc. are
rejected.

## MCP Metadata

The MCP tools keep their default plain-content behavior, but callers can ask
for structured metadata explicitly:

```python
await pulldown("https://example.com", include_meta=True)
```

That JSON response includes the same nested `meta["routing"]` object returned
by the Python API.

## Verify the installed package

From a source checkout, build a wheel and run the same checks used before
publication:

```bash
python -m pip install ".[dev]" build twine
python -m pytest tests/ -q
python -m build
python scripts/smoke-installed.py --wheel dist/pulldown-0.4.2-py3-none-any.whl --output-dir website/examples/outputs
```

The smoke check creates a separate environment, installs the wheel and Chromium,
serves the fixture pages on loopback, and runs the actual CLI. It verifies all
five modes, routing metadata, the private-address default, and the HTTP/render
comparison with a shared cache. Captures include a manifest identifying their
version and local fixture source. On Linux, `--install-browser-deps` also installs
Chromium's system dependencies.

## License

MIT
