"""Compatibility checks for supported HTML converter result formats."""

from types import SimpleNamespace
from pathlib import Path

import pytest

from pulldown.core import _extract_cleaned_landmark_markdown, _extract_full, _extract_readable


HTML = "<html><head><title>Service notes</title></head><body><main><h1>Service notes</h1><p>The export service is ready for use.</p></main></body></html>"


def test_full_preserves_fixture_navigation_and_footer_while_readable_removes_them():
    fixture = Path(__file__).parents[1] / "website" / "examples" / "field-notes.html"
    html = fixture.read_text(encoding="utf-8")

    full, _, _ = _extract_full(html, "https://example.com/field-notes.html")
    readable, _, _ = _extract_readable(html, "https://example.com/field-notes.html")

    for text in ("Example gallery", "pulldown home", "DEMO NAVIGATION", "DEMO FOOTER"):
        assert text in full
        assert text not in readable
    assert "Start with the installed artifact" in full
    assert "Start with the installed artifact" in readable


@pytest.mark.parametrize("extract", [_extract_cleaned_landmark_markdown, _extract_full])
def test_installed_converter_returns_markdown_content(extract):
    result = extract(HTML, "https://example.com/notes")
    markdown = result[0] if isinstance(result, tuple) else result

    assert "# Service notes" in markdown
    assert "The export service is ready for use." in markdown
    assert "ConversionResult object" not in markdown


@pytest.mark.parametrize("kind", ["string", "mapping", "object"])
def test_converter_result_formats_preserve_content(monkeypatch, kind):
    import html_to_markdown

    markdown = "# Converted notes\n\nThe export service is ready."
    document = {"title": "Converted notes", "description": "Export notes"}
    if kind == "mapping":
        converted = {"content": markdown, "metadata": {"document": document}}
    elif kind == "object":
        converted = SimpleNamespace(
            content=markdown,
            metadata=SimpleNamespace(document=SimpleNamespace(**document)),
        )
    else:
        converted = markdown
    monkeypatch.setattr(html_to_markdown, "convert", lambda html, **kwargs: converted)

    assert _extract_cleaned_landmark_markdown(HTML, "https://example.com/notes") == markdown
    content, title, metadata = _extract_full(HTML, "https://example.com/notes")
    assert content == markdown
    if kind != "string":
        assert title == "Converted notes"
        assert metadata["description"] == "Export notes"
