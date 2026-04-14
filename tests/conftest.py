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


SAMPLE_DOCS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <title>Widgets API Reference</title>
    <meta name="description" content="Reference docs for widget APIs.">
</head>
<body>
    <nav><a href="/docs">Docs</a> <a href="/docs/api">API</a></nav>
    <main>
        <h1>Widgets API Reference</h1>
        <p>Use the Widgets API to create, update, and inspect widgets in your workspace. The reference page is intentionally a little verbose so the readable extractor has enough narrative context to stay in article mode while still looking like developer documentation.</p>
        <h2>Install</h2>
        <p>Install the SDK and configure your API key before using the examples below. The installation notes explain the supported Python versions, the default network timeout behavior, and the environment variables that the SDK expects at runtime.</p>
        <pre><code>pip install widgets-sdk</code></pre>
        <h2>Parameters</h2>
        <p>The client accepts timeout, retries, and base URL options. These parameters are applied consistently across list, create, and update operations so the examples below can focus on the resource semantics instead of the transport plumbing.</p>
        <h2>Example</h2>
        <pre><code>client.widgets.create(name="alpha")</code></pre>
    </main>
</body>
</html>"""


SAMPLE_LISTING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <title>Search Results</title>
</head>
<body>
    <nav><a href="/">Home</a> <a href="/search">Search</a> <a href="/categories">Categories</a></nav>
    <main>
        <h1>Search Results</h1>
        <form action="/search">
            <input type="search" name="q" value="widgets">
            <button type="submit">Search</button>
        </form>
        <section>
            <h2>Results</h2>
            <ul>
                <li><a href="/items/1">Widget Alpha</a> <a href="/items/1/reviews">Reviews</a> <a href="/items/1/buy">Buy</a></li>
                <li><a href="/items/2">Widget Beta</a> <a href="/items/2/reviews">Reviews</a> <a href="/items/2/buy">Buy</a></li>
                <li><a href="/items/3">Widget Gamma</a> <a href="/items/3/reviews">Reviews</a> <a href="/items/3/buy">Buy</a></li>
                <li><a href="/items/4">Widget Delta</a> <a href="/items/4/reviews">Reviews</a> <a href="/items/4/buy">Buy</a></li>
                <li><a href="/items/5">Widget Epsilon</a> <a href="/items/5/reviews">Reviews</a> <a href="/items/5/buy">Buy</a></li>
                <li><a href="/items/6">Widget Zeta</a> <a href="/items/6/reviews">Reviews</a> <a href="/items/6/buy">Buy</a></li>
                <li><a href="/items/7">Widget Eta</a> <a href="/items/7/reviews">Reviews</a> <a href="/items/7/buy">Buy</a></li>
                <li><a href="/items/8">Widget Theta</a> <a href="/items/8/reviews">Reviews</a> <a href="/items/8/buy">Buy</a></li>
                <li><a href="/items/9">Widget Iota</a> <a href="/items/9/reviews">Reviews</a> <a href="/items/9/buy">Buy</a></li>
            </ul>
        </section>
        <nav aria-label="Pagination">
            <a href="/search?page=1">1</a>
            <a href="/search?page=2">2</a>
            <a href="/search?page=3">3</a>
            <a href="/search?page=2">Next</a>
        </nav>
    </main>
</body>
</html>"""


SAMPLE_APP_SHELL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <title>Workspace Dashboard</title>
</head>
<body>
    <nav><a href="/home">Home</a> <a href="/projects">Projects</a> <a href="/settings">Settings</a></nav>
    <aside>
        <a href="/workspace">Workspace</a>
        <a href="/workspace/members">Members</a>
        <a href="/workspace/billing">Billing</a>
    </aside>
    <main>
        <h1>Workspace Dashboard</h1>
        <form action="/search">
            <input type="search" name="q" value="widgets">
            <button type="submit">Search</button>
        </form>
        <form action="/filters">
            <input type="text" name="owner" value="team">
            <input type="text" name="status" value="active">
            <button type="submit">Apply</button>
            <button type="reset">Reset</button>
        </form>
        <button>New project</button>
        <button>Invite user</button>
        <button>Export</button>
        <p>Status</p>
        <p>Overview</p>
        <p>Team</p>
    </main>
</body>
</html>"""


SAMPLE_AMBIGUOUS_DOCS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <title>API Helpers</title>
</head>
<body>
    <main>
        <h1>API Helpers</h1>
        <p>Small helper functions for the SDK runtime. This page is short on headings and long on implementation notes so it should land in the ambiguous band that the classifier resolves instead of the high-confidence rules.</p>
        <h2>Example</h2>
        <pre><code>from widgets.helpers import build_client</code></pre>
        <p>Use the helper to construct clients consistently across services. The surrounding prose is intentionally detailed enough to count as readable documentation once extracted, even though the page itself does not have enough headings to trip the deterministic docs rule.</p>
    </main>
</body>
</html>"""


SAMPLE_WEAK_ARTICLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <title>Short Analysis</title>
</head>
<body>
    <nav><a href="/home">Home</a> <a href="/archive">Archive</a></nav>
    <article>
        <h1>Short Analysis</h1>
        <p>This short article makes one point clearly and then stops before it becomes a substantial essay.</p>
        <p>It still reads like prose, but it is intentionally too brief for the high-confidence readable contract.</p>
        <p>The body is long enough to classify as article-like and short enough to trigger the quality fallback.</p>
    </article>
    <footer>Subscribe for updates.</footer>
</body>
</html>"""


SAMPLE_GENERIC_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <title>Welcome</title>
</head>
<body>
    <main>
        <h1>Welcome</h1>
        <p>Overview</p>
        <p>Start here.</p>
    </main>
</body>
</html>"""


@pytest.fixture
def sample_article_html() -> str:
    return SAMPLE_ARTICLE_HTML


@pytest.fixture
def sample_links_html() -> str:
    return SAMPLE_LINKS_HTML


@pytest.fixture
def sample_docs_html() -> str:
    return SAMPLE_DOCS_HTML


@pytest.fixture
def sample_listing_html() -> str:
    return SAMPLE_LISTING_HTML


@pytest.fixture
def sample_app_shell_html() -> str:
    return SAMPLE_APP_SHELL_HTML


@pytest.fixture
def sample_ambiguous_docs_html() -> str:
    return SAMPLE_AMBIGUOUS_DOCS_HTML


@pytest.fixture
def sample_weak_article_html() -> str:
    return SAMPLE_WEAK_ARTICLE_HTML


@pytest.fixture
def sample_generic_html() -> str:
    return SAMPLE_GENERIC_HTML


@pytest.fixture
def tmp_cache_dir(tmp_path):
    d = tmp_path / "cache"
    d.mkdir()
    return d
