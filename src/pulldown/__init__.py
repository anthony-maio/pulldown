"""pulldown: Fetch web pages as clean Markdown for LLM agents."""

__version__ = "0.3.1"

from .cache import PageCache
from .core import Detail, FetchResult, fetch, fetch_many
from .crawl import CrawlResult, crawl

__all__ = [
    "fetch",
    "fetch_many",
    "crawl",
    "Detail",
    "FetchResult",
    "CrawlResult",
    "PageCache",
]
