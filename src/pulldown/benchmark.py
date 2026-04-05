"""
Benchmarking utilities for pulldown.

Measures extraction throughput and HTTP fetch performance.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .core import Detail, FetchResult, fetch_many


@dataclass
class BenchmarkResult:
    """Results from a benchmark run."""
    urls: list[str]
    detail: str
    render: bool
    runs: int
    results: list[list[FetchResult]] = field(default_factory=list)

    @property
    def all_elapsed(self) -> list[float]:
        """All elapsed_ms values across all runs."""
        return [r.elapsed_ms for run in self.results for r in run]

    @property
    def successful(self) -> list[FetchResult]:
        """All successful results across runs."""
        return [r for run in self.results for r in run if r.ok]

    def summary(self) -> dict[str, Any]:
        elapsed = self.all_elapsed
        if not elapsed:
            return {"error": "no results"}

        ok_count = len(self.successful)
        total = len(elapsed)
        content_sizes = [len(r.content) for r in self.successful]

        return {
            "urls": len(self.urls),
            "runs": self.runs,
            "detail": self.detail,
            "render": self.render,
            "total_fetches": total,
            "successful": ok_count,
            "failed": total - ok_count,
            "timing_ms": {
                "mean": round(statistics.mean(elapsed), 1),
                "median": round(statistics.median(elapsed), 1),
                "p95": round(sorted(elapsed)[int(len(elapsed) * 0.95)], 1) if len(elapsed) >= 2 else round(elapsed[0], 1),
                "min": round(min(elapsed), 1),
                "max": round(max(elapsed), 1),
                "stdev": round(statistics.stdev(elapsed), 1) if len(elapsed) > 1 else 0,
            },
            "content": {
                "mean_chars": round(statistics.mean(content_sizes), 0) if content_sizes else 0,
                "total_chars": sum(content_sizes),
            },
            "throughput": {
                "pages_per_second": round(
                    ok_count / (sum(elapsed) / 1000), 2
                ) if sum(elapsed) > 0 else 0,
            },
        }

    def report(self) -> str:
        """Human-readable benchmark report."""
        s = self.summary()
        if "error" in s:
            return f"Benchmark failed: {s['error']}"

        lines = [
            "=" * 60,
            "pulldown benchmark",
            "=" * 60,
            f"URLs:        {s['urls']}",
            f"Runs:        {s['runs']}",
            f"Detail:      {s['detail']}",
            f"Render:      {s['render']}",
            "-" * 60,
            f"Total fetches: {s['total_fetches']}",
            f"Successful:    {s['successful']}",
            f"Failed:        {s['failed']}",
            "-" * 60,
            "Timing (ms):",
            f"  Mean:    {s['timing_ms']['mean']}",
            f"  Median:  {s['timing_ms']['median']}",
            f"  P95:     {s['timing_ms']['p95']}",
            f"  Min:     {s['timing_ms']['min']}",
            f"  Max:     {s['timing_ms']['max']}",
            f"  StdDev:  {s['timing_ms']['stdev']}",
            "-" * 60,
            "Content:",
            f"  Mean chars:  {s['content']['mean_chars']}",
            f"  Total chars: {s['content']['total_chars']}",
            "-" * 60,
            f"Throughput: {s['throughput']['pages_per_second']} pages/sec",
            "=" * 60,
        ]
        return "\n".join(lines)


async def benchmark(
    urls: Sequence[str],
    *,
    detail: Detail | str = Detail.readable,
    render: bool = False,
    runs: int = 3,
    concurrency: int = 5,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    verify_ssl: bool = True,
    allow_private_addresses: bool = False,
    **kwargs: Any,
) -> BenchmarkResult:
    """
    Run a benchmark across one or more URLs.

    Parameters
    ----------
    urls : sequence of str
        URLs to benchmark.
    detail : Detail | str
        Extraction level.
    render : bool
        Use Playwright rendering.
    runs : int
        Number of runs to average. Default 3.
    concurrency : int
        Max concurrent fetches per run.
    """
    if isinstance(detail, str):
        detail_enum = Detail(detail)
    else:
        detail_enum = detail

    result = BenchmarkResult(
        urls=list(urls),
        detail=detail_enum.value,
        render=render,
        runs=runs,
    )

    for _ in range(runs):
        run_results = await fetch_many(
            urls,
            detail=detail_enum,
            render=render,
            concurrency=concurrency,
            headers=headers,
            timeout=timeout,
            verify_ssl=verify_ssl,
            allow_private_addresses=allow_private_addresses,
            **kwargs,
        )
        result.results.append(run_results)

    return result
