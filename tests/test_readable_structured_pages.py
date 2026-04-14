from __future__ import annotations

import base64
from pathlib import Path

import httpx

from pulldown import Detail, fetch
from pulldown.core import _extract_readable


FIXTURE_DIR = Path(__file__).parent / "fixtures"
COMPRESSED_HTML = base64.b64decode(
    "G5sAAMTcVqoLSbGPC//l44c6cPyE+ROdGxy4hGkkAQcLDrjYDIvsAZcisQ5JCBAYDyodnCNdccdcW8/RTq982htW2ekAPGX8YGuM1Rp44VH3oPmk87UjfdAD+RY7y3ZSqLBKWrWcDg=="
)


def _fixture_text(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _mock_transport(*, content: bytes, headers: dict[str, str] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, headers=headers)

    return httpx.MockTransport(handler)


def _patch_async_client(monkeypatch, transport: httpx.MockTransport) -> None:
    orig = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig(*a, **{**kw, "transport": transport}),
    )


class TestReadableStructuredPages:
    async def test_readable_preserves_structured_sections(self, monkeypatch):
        html = _fixture_text("making_minds_landing.html")
        transport = _mock_transport(content=html.encode("utf-8"))
        _patch_async_client(monkeypatch, transport)

        result = await fetch("https://making-minds.ai/", detail=Detail.readable)

        assert result.ok
        assert result.title == "Making Minds — Applied AI by Anthony D. Maio"
        assert "## Writing" in result.content
        assert "### Medium" in result.content
        assert "### Hugging Face" in result.content
        assert "### The Checkpoint Newsletter" in result.content
        assert "- [The Agentic Coding Shift]" in result.content
        assert "\n- [The REKKI Case Study]" in result.content
        assert "\n- [Llama 4 Running Locally]" in result.content
        assert "- HDCS — Heterogeneous Divergence-Convergence Swarm." in result.content
        assert "- CMED — Cross-Model Epistemic Divergence." in result.content
        assert "![" not in result.content
        assert "[ ](" not in result.content
        assert "Seeking" not in result.content
        assert "ORCID" not in result.content
        assert "Google Scholar" not in result.content
        assert "ResearchGate" not in result.content

    async def test_brotli_transport_is_decoded_for_raw_and_readable(self, monkeypatch):
        transport = _mock_transport(
            content=COMPRESSED_HTML,
            headers={
                "Content-Encoding": "br",
                "Content-Type": "text/html; charset=utf-8",
            },
        )
        _patch_async_client(monkeypatch, transport)

        raw_result = await fetch("https://example.com/compressed", detail=Detail.raw)
        readable_result = await fetch("https://example.com/compressed", detail=Detail.readable)

        assert raw_result.ok
        assert raw_result.title == "Compressed Page"
        assert "<title>Compressed Page</title>" in raw_result.content
        assert readable_result.ok
        assert readable_result.title == "Compressed Page"
        assert "Compressed Page" in readable_result.content
        assert "Hello from Brotli transport." in readable_result.content

    def test_readable_falls_back_to_cleaned_main_when_article_extraction_is_weak(self, monkeypatch):
        import trafilatura

        html = _fixture_text("making_minds_landing.html")
        monkeypatch.setattr(trafilatura, "extract", lambda *a, **kw: "short")

        content, title, meta = _extract_readable(html, "https://making-minds.ai/")

        assert title == "Making Minds — Applied AI by Anthony D. Maio"
        assert "## Flagship" in content
        assert "## Writing" in content
        assert "### Medium" in content
        assert "- HDCS — Heterogeneous Divergence-Convergence Swarm." in content
        assert "Seeking" not in content
        assert "ORCID" not in content
