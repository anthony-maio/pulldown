from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_ci_workflow_is_slimmed_for_pushes_and_guarded_by_concurrency():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "concurrency:" in workflow
    assert "paths-ignore:" in workflow
    assert "website/**" in workflow
    assert "docs/**" in workflow
    assert "if: github.event_name != 'pull_request'" in workflow
    assert "if: github.event_name == 'pull_request'" in workflow
    assert "matrix:" in workflow
    assert "include:" in workflow
    assert 'python-version: "3.12"' in workflow


def test_pages_workflow_only_deploys_via_actions_artifact_path():
    workflow = (ROOT / ".github" / "workflows" / "deploy-pages.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "website/**" in workflow
    assert ".github/workflows/deploy-pages.yml" in workflow
    assert "upload-pages-artifact" in workflow
    assert "path: website/" in workflow
