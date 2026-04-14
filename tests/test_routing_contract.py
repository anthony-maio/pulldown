from __future__ import annotations

import importlib
import json
import math
import sys
from pathlib import Path

import httpx
from click.testing import CliRunner

from pulldown import Detail, FetchResult, PageCache, crawl, fetch
from pulldown.cli import main as cli_main


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _fixture_text(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _patch_async_client(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
    )


def _serve_html(html: str):
    return lambda request: httpx.Response(200, html=html)


def _assert_routing_contract(routing: dict):
    assert set(routing) == {
        "page_type",
        "source",
        "confidence",
        "abstained",
        "strategy_used",
        "quality_grade",
        "render_recommended",
    }
    assert routing["page_type"] in {
        "article",
        "docs",
        "listing",
        "table_heavy",
        "app_shell",
        "generic",
    }
    assert routing["source"] in {"rules", "classifier", "hybrid", "fallback"}
    assert 0.0 <= routing["confidence"] <= 1.0
    assert isinstance(routing["abstained"], bool)
    assert routing["strategy_used"] in {"article", "minimal", "structured", "full", "raw"}
    assert routing["quality_grade"] in {"low", "medium", "high"}
    assert isinstance(routing["render_recommended"], bool)


class TestRoutingContract:
    async def test_fetch_nests_routing_metadata(self, sample_article_html, monkeypatch):
        _patch_async_client(monkeypatch, _serve_html(sample_article_html))

        result = await fetch("https://example.com/article", detail=Detail.readable)

        assert result.ok
        assert "routing" in result.meta
        assert "page_type" not in result.meta
        _assert_routing_contract(result.meta["routing"])
        assert result.meta["routing"]["page_type"] == "article"
        assert result.meta["routing"]["source"] == "rules"
        assert result.meta["routing"]["strategy_used"] == "article"

    async def test_docs_rule_routes_to_article(self, sample_docs_html, monkeypatch):
        _patch_async_client(monkeypatch, _serve_html(sample_docs_html))

        result = await fetch("https://example.com/docs/api/widgets", detail=Detail.readable)

        routing = result.meta["routing"]
        assert result.ok
        _assert_routing_contract(routing)
        assert routing["page_type"] == "docs"
        assert routing["source"] == "rules"
        assert routing["strategy_used"] == "article"

    async def test_listing_rule_routes_to_structured(self, sample_listing_html, monkeypatch):
        _patch_async_client(monkeypatch, _serve_html(sample_listing_html))

        result = await fetch("https://example.com/search?q=widgets", detail=Detail.readable)

        routing = result.meta["routing"]
        assert result.ok
        assert routing["page_type"] == "listing"
        assert routing["source"] == "rules"
        assert routing["strategy_used"] == "structured"
        assert "## Results" in result.content or "## Search Results" in result.content

    async def test_table_heavy_rule_routes_to_structured(self, monkeypatch):
        html = _fixture_text("parameter_golf_dashboard.html")
        _patch_async_client(monkeypatch, _serve_html(html))

        result = await fetch("https://example.com/leaderboard", detail=Detail.readable)

        routing = result.meta["routing"]
        assert result.ok
        assert routing["page_type"] == "table_heavy"
        assert routing["source"] == "rules"
        assert routing["strategy_used"] == "structured"

    async def test_app_shell_rule_routes_to_full(self, sample_app_shell_html, monkeypatch):
        _patch_async_client(monkeypatch, _serve_html(sample_app_shell_html))

        result = await fetch("https://example.com/workspace", detail=Detail.readable)

        routing = result.meta["routing"]
        assert result.ok
        assert routing["page_type"] == "app_shell"
        assert routing["source"] == "rules"
        assert routing["strategy_used"] == "full"
        assert routing["render_recommended"] is True

    async def test_classifier_decides_ambiguous_docs(self, sample_ambiguous_docs_html, monkeypatch):
        _patch_async_client(monkeypatch, _serve_html(sample_ambiguous_docs_html))

        result = await fetch("https://example.com/sdk/helpers", detail=Detail.readable)

        routing = result.meta["routing"]
        assert result.ok
        assert routing["page_type"] == "docs"
        assert routing["source"] == "classifier"
        assert routing["abstained"] is False
        assert routing["strategy_used"] == "article"

    async def test_classifier_abstains_and_uses_hybrid_full(
        self, sample_generic_html, monkeypatch
    ):
        _patch_async_client(monkeypatch, _serve_html(sample_generic_html))

        import pulldown.routing as routing

        monkeypatch.setattr(
            routing,
            "_predict_probabilities",
            lambda *_args, **_kwargs: {
                "article": 0.2,
                "docs": 0.18,
                "listing": 0.16,
                "table_heavy": 0.16,
                "app_shell": 0.15,
                "generic": 0.15,
            },
        )

        result = await fetch("https://example.com/welcome", detail=Detail.readable)

        routing_info = result.meta["routing"]
        assert result.ok
        assert routing_info["source"] == "hybrid"
        assert routing_info["abstained"] is True
        assert routing_info["strategy_used"] == "full"

    async def test_low_quality_article_falls_back_to_full(
        self, sample_weak_article_html, monkeypatch
    ):
        _patch_async_client(monkeypatch, _serve_html(sample_weak_article_html))

        result = await fetch("https://example.com/brief-analysis", detail=Detail.readable)

        routing = result.meta["routing"]
        assert result.ok
        assert routing["page_type"] == "article"
        assert routing["source"] == "fallback"
        assert routing["strategy_used"] == "full"
        assert routing["quality_grade"] in {"medium", "high"}

    async def test_fetch_writes_opt_in_routing_log(
        self, sample_article_html, monkeypatch, tmp_path
    ):
        _patch_async_client(monkeypatch, _serve_html(sample_article_html))
        log_path = tmp_path / "routing.jsonl"

        result = await fetch(
            "https://example.com/article",
            detail=Detail.readable,
            routing_log_path=str(log_path),
        )

        assert result.ok
        payload = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert payload["requested_detail"] == "readable"
        assert payload["chosen_label"] == "article"
        assert payload["strategy_final"] == "article"
        assert payload["features"]
        assert "classifier_probabilities" in payload

    async def test_cache_hit_preserves_nested_routing_meta(
        self, sample_docs_html, monkeypatch, tmp_cache_dir
    ):
        _patch_async_client(monkeypatch, _serve_html(sample_docs_html))
        cache = PageCache(tmp_cache_dir, ttl=3600)

        first = await fetch("https://example.com/docs/api/widgets", cache=cache)
        second = await fetch("https://example.com/docs/api/widgets", cache=cache)

        assert first.ok and second.ok
        assert second.from_cache is True
        assert second.meta["routing"]["page_type"] == "docs"
        assert second.meta["routing"]["strategy_used"] == "article"

    async def test_crawl_pages_preserve_routing_metadata(self, sample_docs_html, monkeypatch):
        site = {
            "/": (
                "<html><head><title>Home</title></head><body><main><h1>Home</h1>"
                "<p>Docs live here.</p><a href='/docs/api/widgets'>Docs</a></main></body></html>"
            ),
            "/docs/api/widgets": sample_docs_html,
            "/robots.txt": "User-agent: *\nAllow: /\n",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            body = site.get(request.url.path)
            if body is None:
                return httpx.Response(404)
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text=body)
            return httpx.Response(200, html=body)

        _patch_async_client(monkeypatch, handler)

        result = await crawl("https://example.com/", max_pages=3, max_depth=2, concurrency=1)

        assert result.pages
        assert all("routing" in page.meta for page in result.pages if page.ok)


class TestRoutingInterfaces:
    def test_cli_json_meta_uses_nested_routing(self, monkeypatch):
        async def fake_fetch(*_args, **_kwargs):
            return FetchResult(
                url="https://example.com",
                status_code=200,
                title="Example",
                content="content",
                meta={
                    "author": "Jane Doe",
                    "routing": {
                        "page_type": "article",
                        "source": "rules",
                        "confidence": 0.99,
                        "abstained": False,
                        "strategy_used": "article",
                        "quality_grade": "high",
                        "render_recommended": False,
                    },
                },
            )

        import pulldown.core as core

        monkeypatch.setattr(core, "fetch", fake_fetch)
        runner = CliRunner()

        invoked = runner.invoke(cli_main, ["get", "https://example.com", "--json-output", "--meta"])

        assert invoked.exit_code == 0
        payload = json.loads(invoked.output)
        assert payload["meta"]["routing"]["page_type"] == "article"
        assert "page_type" not in payload["meta"]

    def test_cli_routing_log_option_passes_through(self, monkeypatch, tmp_path):
        called: dict[str, str] = {}

        async def fake_fetch(*_args, **kwargs):
            called["routing_log_path"] = kwargs.get("routing_log_path")
            return FetchResult(url="https://example.com", status_code=200, content="ok")

        import pulldown.core as core

        monkeypatch.setattr(core, "fetch", fake_fetch)
        runner = CliRunner()
        log_path = tmp_path / "route.jsonl"

        invoked = runner.invoke(cli_main, ["get", "https://example.com", "--routing-log", str(log_path)])

        assert invoked.exit_code == 0
        assert called["routing_log_path"] == str(log_path)

    async def test_mcp_include_meta_returns_json(self, monkeypatch):
        import pulldown.mcp_server as mcp_server

        async def fake_fetch(*_args, **_kwargs):
            return FetchResult(
                url="https://example.com",
                status_code=200,
                title="Example",
                content="body",
                meta={
                    "routing": {
                        "page_type": "article",
                        "source": "rules",
                        "confidence": 0.98,
                        "abstained": False,
                        "strategy_used": "article",
                        "quality_grade": "high",
                        "render_recommended": False,
                    }
                },
            )

        monkeypatch.setattr(mcp_server, "_fetch", fake_fetch)

        payload = await mcp_server.pulldown("https://example.com", include_meta=True)
        data = json.loads(payload)

        assert data["meta"]["routing"]["page_type"] == "article"
        assert data["content"] == "body"


class TestRoutingModel:
    def test_bundled_model_scores_without_sklearn(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "sklearn", None)
        import pulldown.routing as routing

        routing = importlib.reload(routing)
        features = {name: 0.0 for name in routing.FEATURE_ORDER}
        probs = routing._predict_probabilities(features)

        assert set(probs) == set(routing.LABEL_ORDER)
        assert math.isclose(sum(probs.values()), 1.0, rel_tol=1e-6)
