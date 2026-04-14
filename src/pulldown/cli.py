"""
CLI interface for pulldown.

Usage:
    pulldown get https://example.com
    pulldown get https://example.com --detail minimal
    pulldown get https://example.com --detail structured
    pulldown get https://example.com --render --scroll 3
    pulldown crawl https://docs.example.com --max-pages 20
    pulldown bench https://example.com https://httpbin.org --runs 5
    pulldown cache stats
    pulldown cache clear
"""

from __future__ import annotations

import asyncio
import json
import sys

import click

from .core import DEFAULT_MAX_BYTES


def _run(coro):
    """Run an async coroutine."""
    return asyncio.run(coro)


@click.group()
def main():
    """pulldown: Fetch web pages as clean Markdown for LLM agents."""
    pass


# ---------------------------------------------------------------------------
# get subcommand (primary)
# ---------------------------------------------------------------------------


@main.command()
@click.argument("url")
@click.option(
    "--detail",
    "-d",
    type=click.Choice(["minimal", "readable", "structured", "full", "raw"]),
    default="readable",
    help="Extraction detail level.",
)
@click.option("--render", "-r", is_flag=True, help="Use Chromium rendering (needs playwright).")
@click.option("--scroll", type=int, default=0, help="Number of viewport scrolls (render mode).")
@click.option("--wait", type=int, default=2000, help="Wait ms after page load (render mode).")
@click.option("--timeout", "-t", type=float, default=30.0, help="HTTP timeout in seconds.")
@click.option("--proxy", type=str, default=None, help="HTTP proxy URL.")
@click.option("--header", "-H", multiple=True, help="Extra header as 'Key: Value'.")
@click.option("--cookie", multiple=True, help="Cookie as 'name=value'.")
@click.option("--cache-dir", type=str, default=None, help="Enable caching with this directory.")
@click.option("--cache-ttl", type=int, default=3600, help="Cache TTL in seconds.")
@click.option("--routing-log", type=click.Path(), default=None, help="Append routing diagnostics JSONL.")
@click.option("--no-verify", is_flag=True, help="Disable SSL verification (dangerous).")
@click.option(
    "--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help="Maximum response size in bytes."
)
@click.option(
    "--allow-private", is_flag=True, help="Allow fetching private/loopback addresses (dangerous)."
)
@click.option("--json-output", "-j", is_flag=True, help="Output as JSON.")
@click.option("--meta", "-m", is_flag=True, help="Include metadata in output.")
@click.option("--output", "-o", type=click.Path(), default=None, help="Write output to file.")
def get(
    url,
    detail,
    render,
    scroll,
    wait,
    timeout,
    proxy,
    header,
    cookie,
    cache_dir,
    cache_ttl,
    routing_log,
    no_verify,
    max_bytes,
    allow_private,
    json_output,
    meta,
    output,
):
    """Fetch a single URL and extract clean Markdown."""
    from .cache import PageCache
    from .core import fetch as _fetch

    if no_verify:
        click.echo("WARNING: TLS certificate verification disabled.", err=True)
    if allow_private:
        click.echo("WARNING: private-address guard disabled.", err=True)

    headers = {}
    for h in header:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()

    cookies = []
    for c in cookie:
        if "=" in c:
            name, value = c.split("=", 1)
            cookies.append({"name": name.strip(), "value": value.strip()})

    cache = PageCache(cache_dir, ttl=cache_ttl) if cache_dir else None

    result = _run(
        _fetch(
            url,
            detail=detail,
            render=render,
            headers=headers if headers else None,
            cookies=cookies if cookies else None,
            proxy=proxy,
            timeout=timeout,
            verify_ssl=not no_verify,
            max_bytes=max_bytes,
            allow_private_addresses=allow_private,
            render_wait_ms=wait,
            render_scroll_count=scroll,
            cache=cache,
            routing_log_path=routing_log,
        )
    )

    if json_output:
        out = {
            "url": result.url,
            "status_code": result.status_code,
            "title": result.title,
            "content": result.content,
            "elapsed_ms": round(result.elapsed_ms, 1),
            "from_cache": result.from_cache,
            "ok": result.ok,
        }
        if meta and result.meta:
            out["meta"] = result.meta
        if result.error:
            out["error"] = result.error
        text = json.dumps(out, indent=2, ensure_ascii=False)
    else:
        parts = []
        if result.title:
            parts.append(f"# {result.title}\n")
        if meta and result.meta:
            for k, v in result.meta.items():
                parts.append(f"**{k}**: {v}")
            parts.append("")
        if result.error:
            parts.append(f"Error: {result.error}")
        else:
            parts.append(result.content)
        parts.append(f"\n<!-- {result} -->")
        text = "\n".join(parts)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
        click.echo(f"Wrote {len(text)} chars to {output}")
    else:
        click.echo(text)

    if not result.ok:
        sys.exit(1)


# ---------------------------------------------------------------------------
# crawl subcommand
# ---------------------------------------------------------------------------


@main.command()
@click.argument("start_url")
@click.option(
    "--detail",
    "-d",
    type=click.Choice(["minimal", "readable", "structured", "full", "raw"]),
    default="readable",
)
@click.option("--max-pages", type=int, default=50, help="Max pages to fetch.")
@click.option("--max-depth", type=int, default=3, help="Max link depth.")
@click.option("--concurrency", "-c", type=int, default=3, help="Concurrent fetches.")
@click.option("--render", "-r", is_flag=True)
@click.option("--timeout", "-t", type=float, default=30.0)
@click.option("--proxy", type=str, default=None)
@click.option("--no-verify", is_flag=True, help="Disable SSL verification (dangerous).")
@click.option("--allow-private", is_flag=True, help="Allow private addresses (dangerous).")
@click.option("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
@click.option("--include", type=str, default=None, help="Regex: only crawl matching URLs.")
@click.option("--exclude", type=str, default=None, help="Regex: skip matching URLs.")
@click.option("--ignore-robots", is_flag=True, help="Do not consult robots.txt.")
@click.option("--delay-ms", type=int, default=0, help="Per-domain delay in ms.")
@click.option("--routing-log", type=click.Path(), default=None, help="Append routing diagnostics JSONL.")
@click.option("--json-output", "-j", is_flag=True)
@click.option(
    "--output", "-o", type=click.Path(), default=None, help="Write combined output to file."
)
@click.option(
    "--output-dir",
    type=click.Path(),
    default=None,
    help="Write each page to a separate file in this dir.",
)
def crawl(
    start_url,
    detail,
    max_pages,
    max_depth,
    concurrency,
    render,
    timeout,
    proxy,
    no_verify,
    allow_private,
    max_bytes,
    include,
    exclude,
    ignore_robots,
    delay_ms,
    routing_log,
    json_output,
    output,
    output_dir,
):
    """Crawl a site starting from START_URL and extract all pages."""
    import os

    from .crawl import crawl as _crawl

    result = _run(
        _crawl(
            start_url,
            detail=detail,
            max_pages=max_pages,
            max_depth=max_depth,
            concurrency=concurrency,
            render=render,
            timeout=timeout,
            proxy=proxy,
            verify_ssl=not no_verify,
            max_bytes=max_bytes,
            allow_private_addresses=allow_private,
            include_pattern=include,
            exclude_pattern=exclude,
            respect_robots=not ignore_robots,
            per_domain_delay_ms=delay_ms,
            routing_log_path=routing_log,
        )
    )

    click.echo(str(result), err=True)

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        for i, page in enumerate(result.pages):
            if page.ok and page.content:
                slug = page.url.replace("https://", "").replace("http://", "")
                slug = slug.replace("/", "_").replace("?", "_").rstrip("_")
                if len(slug) > 100:
                    slug = slug[:100]
                fname = f"{i:03d}_{slug}.md"
                fpath = os.path.join(output_dir, fname)
                with open(fpath, "w", encoding="utf-8") as f:
                    if page.title:
                        f.write(f"# {page.title}\n\nSource: {page.url}\n\n")
                    f.write(page.content)
        click.echo(f"Wrote {len(result.pages)} pages to {output_dir}", err=True)
        return

    if json_output:
        out = {
            "start_url": result.start_url,
            "urls_discovered": result.urls_discovered,
            "urls_fetched": result.urls_fetched,
            "urls_skipped": result.urls_skipped,
            "elapsed_ms": round(result.elapsed_ms, 1),
            "pages": [
                {
                    "url": p.url,
                    "title": p.title,
                    "status_code": p.status_code,
                    "content": p.content,
                    "ok": p.ok,
                    "error": p.error,
                }
                for p in result.pages
            ],
        }
        click.echo(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        combined = result.to_markdown()
        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(combined)
            click.echo(f"Wrote {len(combined)} chars to {output}", err=True)
        else:
            click.echo(combined)


# ---------------------------------------------------------------------------
# bench subcommand
# ---------------------------------------------------------------------------


@main.command()
@click.argument("urls", nargs=-1, required=True)
@click.option(
    "--detail",
    "-d",
    type=click.Choice(["minimal", "readable", "structured", "full", "raw"]),
    default="readable",
)
@click.option("--render", "-r", is_flag=True)
@click.option("--runs", type=int, default=3, help="Number of benchmark runs.")
@click.option("--concurrency", "-c", type=int, default=5)
@click.option("--timeout", "-t", type=float, default=30.0)
@click.option("--no-verify", is_flag=True, help="Disable SSL verification.")
@click.option("--allow-private", is_flag=True, help="Allow private addresses (dangerous).")
@click.option("--routing-log", type=click.Path(), default=None, help="Append routing diagnostics JSONL.")
@click.option("--json-output", "-j", is_flag=True)
def bench(urls, detail, render, runs, concurrency, timeout, no_verify, allow_private, routing_log, json_output):
    """Benchmark fetch+extract throughput."""
    from .benchmark import benchmark as _benchmark

    result = _run(
        _benchmark(
            urls,
            detail=detail,
            render=render,
            runs=runs,
            concurrency=concurrency,
            timeout=timeout,
            verify_ssl=not no_verify,
            allow_private_addresses=allow_private,
            routing_log_path=routing_log,
        )
    )

    if json_output:
        click.echo(json.dumps(result.summary(), indent=2))
    else:
        click.echo(result.report())


# ---------------------------------------------------------------------------
# cache subcommand
# ---------------------------------------------------------------------------


@main.command("cache")
@click.argument("action", type=click.Choice(["stats", "clear"]))
@click.option("--cache-dir", type=str, default=None)
def cache_cmd(action, cache_dir):
    """Manage the page cache."""
    from .cache import PageCache

    cache = PageCache(cache_dir)

    if action == "stats":
        s = cache.stats()
        click.echo(json.dumps(s, indent=2))
    elif action == "clear":
        n = cache.clear()
        click.echo(f"Cleared {n} cache entries.")


if __name__ == "__main__":
    main()
