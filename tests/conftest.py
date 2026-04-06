"""Shared test fixtures."""

from __future__ import annotations

import pytest

SAMPLE_ARTICLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <title>A Fine Article</title>
    <meta name="author" content="Jane Doe">
    <meta name="description" content="A thoughtful piece on testing.">
</head>
<body>
    <nav><a href="/home">Home</a> <a href="/about">About</a></nav>
    <article>
        <h1>A Fine Article</h1>
        <p>This is the <strong>first paragraph</strong> of the article. It contains
        several sentences to give trafilatura something meaningful to extract. We want
        the body to be long enough that boilerplate detection works correctly.</p>
        <p>This is the second paragraph. It also has <a href="/linked">some text</a>
        that links elsewhere. The content here is substantive enough that the article
        body should be clearly distinguished from navigation chrome.</p>
        <p>A third paragraph to round things out and ensure the extractor has enough
        material to work with. Without enough body text, trafilatura may return None
        and force fallback behavior.</p>
    </article>
    <footer>Copyright 2026 Example Inc.</footer>
</body>
</html>"""


SAMPLE_LINKS_HTML = """<!DOCTYPE html>
<html><head><title>Links Page</title></head>
<body>
    <a href="/page1">Page 1</a>
    <a href="/page2">Page 2</a>
    <a href="https://other.example.com/page3">External</a>
    <a href="#anchor">Anchor</a>
    <a href="mailto:foo@example.com">Email</a>
    <a href="javascript:void(0)">JS</a>
    <a href="/file.pdf">PDF</a>
    <a href="/page1#frag">Page 1 with fragment</a>
</body>
</html>"""


@pytest.fixture
def sample_article_html() -> str:
    return SAMPLE_ARTICLE_HTML


@pytest.fixture
def sample_links_html() -> str:
    return SAMPLE_LINKS_HTML


@pytest.fixture
def tmp_cache_dir(tmp_path):
    d = tmp_path / "cache"
    d.mkdir()
    return d
