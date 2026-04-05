"""Characterization tests for PageCache."""
from __future__ import annotations

import time

from pulldown import PageCache


class TestPageCacheBasics:
    def test_miss_returns_none(self, tmp_cache_dir):
        c = PageCache(tmp_cache_dir)
        assert c.get("http://x.com/", "readable") is None

    def test_put_then_get(self, tmp_cache_dir):
        c = PageCache(tmp_cache_dir)
        c.put("http://x.com/", "readable", {"content": "hello", "title": "Hi"})
        cached = c.get("http://x.com/", "readable")
        assert cached is not None
        assert cached["content"] == "hello"
        assert cached["title"] == "Hi"

    def test_different_detail_levels_are_separate(self, tmp_cache_dir):
        c = PageCache(tmp_cache_dir)
        c.put("http://x.com/", "readable", {"content": "readable-version"})
        c.put("http://x.com/", "minimal", {"content": "minimal-version"})

        assert c.get("http://x.com/", "readable")["content"] == "readable-version"
        assert c.get("http://x.com/", "minimal")["content"] == "minimal-version"

    def test_ttl_expiry(self, tmp_cache_dir):
        c = PageCache(tmp_cache_dir, ttl=1)
        c.put("http://x.com/", "readable", {"content": "hi"})
        assert c.get("http://x.com/", "readable") is not None

        time.sleep(1.1)
        assert c.get("http://x.com/", "readable") is None

    def test_invalidate_specific(self, tmp_cache_dir):
        c = PageCache(tmp_cache_dir)
        c.put("http://x.com/", "readable", {"content": "a"})
        c.put("http://x.com/", "minimal", {"content": "b"})

        removed = c.invalidate("http://x.com/", "readable")
        assert removed == 1
        assert c.get("http://x.com/", "readable") is None
        assert c.get("http://x.com/", "minimal") is not None

    def test_invalidate_all_details(self, tmp_cache_dir):
        c = PageCache(tmp_cache_dir)
        c.put("http://x.com/", "readable", {"content": "a"})
        c.put("http://x.com/", "minimal", {"content": "b"})
        c.put("http://x.com/", "full", {"content": "c"})

        removed = c.invalidate("http://x.com/")
        assert removed == 3

    def test_clear_removes_all(self, tmp_cache_dir):
        c = PageCache(tmp_cache_dir)
        c.put("http://a.com/", "readable", {"content": "x"})
        c.put("http://b.com/", "readable", {"content": "y"})

        removed = c.clear()
        assert removed == 2
        assert c.get("http://a.com/", "readable") is None

    def test_stats(self, tmp_cache_dir):
        c = PageCache(tmp_cache_dir)
        c.put("http://a.com/", "readable", {"content": "x"})
        s = c.stats()
        assert s["entries"] == 1
        assert s["total_bytes"] > 0
        assert "cache_dir" in s
        assert s["ttl"] > 0

    def test_corrupt_cache_file_returns_none(self, tmp_cache_dir):
        c = PageCache(tmp_cache_dir)
        # Put then corrupt the file
        c.put("http://x.com/", "readable", {"content": "hi"})
        # Find the file and corrupt it
        files = list(tmp_cache_dir.glob("*.json"))
        assert len(files) == 1
        files[0].write_text("not valid json {{{")

        # Should return None, not raise
        assert c.get("http://x.com/", "readable") is None
