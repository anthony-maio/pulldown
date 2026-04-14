from __future__ import annotations

from pathlib import Path

import httpx

from pulldown import Detail, fetch


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _fixture_text(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _patch_async_client(monkeypatch, html: str) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, html=html))
    orig = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
    )


class TestStructuredRouting:
    async def test_structured_detail_extracts_dashboard_hierarchy(self, monkeypatch):
        html = _fixture_text("parameter_golf_dashboard.html")
        _patch_async_client(monkeypatch, html)

        result = await fetch("https://matotezitanka.github.io/parameter-golf/", detail="structured")

        assert result.ok
        assert result.meta["page_type"] == "dashboard"
        assert result.meta["strategy_used"] == "structured"
        assert result.meta["readerable"] is False
        assert "## Leaderboard Snapshot" in result.content
        assert "- Table columns: Status | Rank | PR | Author | BPB | Size" in result.content
        assert "- Showing first 8 of 12 rows" in result.content
        assert "- 1 | #42 | @chonchiog | 0.0005 | 16.0 MB" in result.content
        assert "GitHub" not in result.content

    async def test_readable_auto_routes_dashboard_pages_to_structured(self, monkeypatch):
        html = _fixture_text("parameter_golf_dashboard.html")
        _patch_async_client(monkeypatch, html)

        result = await fetch("https://matotezitanka.github.io/parameter-golf/", detail=Detail.readable)

        assert result.ok
        assert result.meta["page_type"] == "dashboard"
        assert result.meta["strategy_used"] == "structured"
        assert result.meta["extraction_quality"] in {"medium", "high"}
        assert "## Leaderboard Snapshot" in result.content
        assert len(result.content) < 5000
        assert "| ALIVE | 1 |" not in result.content
        assert "552 PRs · 260 people" in result.content

    async def test_structured_routing_uses_broader_container_for_card_dashboards(self, monkeypatch):
        html = """<!DOCTYPE html>
<html lang="en">
<head><title>Community Dashboard</title></head>
<body>
    <nav><a href="/">Home</a></nav>
    <div class="container">
        <section id="techniques">
            <h2>Technique Map</h2>
            <div class="technique-grid">
                <article class="technique-card">
                    <div class="technique-card-head"><h3>TTT</h3><span>LEGAL</span></div>
                    <p>554 PRs · 261 people</p>
                    <div class="technique-metrics">
                        <div>Best ALIVE #1376 · 0.7094</div>
                        <div>Alive / Dead 124 / 179</div>
                    </div>
                </article>
                <article class="technique-card">
                    <div class="technique-card-head"><h3>EMA</h3><span>LEGAL</span></div>
                    <p>438 PRs · 239 people</p>
                    <div class="technique-metrics">
                        <div>Best ALIVE #1319 · 0.6951</div>
                        <div>Alive / Dead 106 / 125</div>
                    </div>
                </article>
            </div>
            <table>
                <thead>
                    <tr><th>Rank</th><th>PR</th><th>Author</th></tr>
                </thead>
                <tbody>
                    <tr><td>1</td><td>#42</td><td>@chonchiog</td></tr>
                    <tr><td>2</td><td>#675</td><td>@ChideraIbe123</td></tr>
                    <tr><td>3</td><td>#1319</td><td>@canivel</td></tr>
                    <tr><td>4</td><td>#1376</td><td>@stukenov</td></tr>
                    <tr><td>5</td><td>#1324</td><td>@yahya010</td></tr>
                    <tr><td>6</td><td>#1321</td><td>@anthony-maio</td></tr>
                    <tr><td>7</td><td>#1278</td><td>@GitGeeks</td></tr>
                    <tr><td>8</td><td>#1488</td><td>@ndokutovich</td></tr>
                </tbody>
            </table>
        </section>
    </div>
</body>
</html>"""
        _patch_async_client(monkeypatch, html)

        result = await fetch("https://example.com/dashboard", detail=Detail.readable)

        assert result.ok
        assert result.meta["page_type"] == "dashboard"
        assert result.meta["strategy_used"] == "structured"
        assert "## Technique Map" in result.content
        assert "### TTT" in result.content
        assert "554 PRs · 261 people" in result.content
        assert "Best ALIVE #1376 · 0.7094" in result.content
        assert "- Table columns: Rank | PR | Author" in result.content

    async def test_article_pages_remain_article_routed(self, sample_article_html, monkeypatch):
        _patch_async_client(monkeypatch, sample_article_html)

        result = await fetch("http://example.com/article", detail=Detail.readable)

        assert result.ok
        assert result.meta["page_type"] == "article"
        assert result.meta["strategy_used"] == "article"
        assert result.meta["readerable"] is True
        assert "first paragraph" in result.content
