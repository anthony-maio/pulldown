from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parent.parent


def test_core_runtime_dependencies_are_declared_directly():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = data["project"]["dependencies"]

    assert any(dep.startswith("brotli") for dep in dependencies)
    assert any(dep.startswith("lxml_html_clean") for dep in dependencies)


def test_repo_skill_covers_install_and_sandbox_usage():
    skill = (ROOT / "skills" / "pulldown" / "SKILL.md").read_text(encoding="utf-8")

    assert "pip install pulldown" in skill
    assert "pulldown[render]" in skill
    assert "pulldown[mcp]" in skill
    assert "--no-verify" in skill
    assert "Default to `readable`" in skill
    assert "0.3.1" in skill
    assert "detail=`structured`" in skill
    assert "page type" in skill


def test_readme_documents_structured_mode():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Five detail levels" in readme
    assert "`structured`" in readme
    assert "Hierarchy-preserving Markdown" in readme
    assert "detected page type" in readme
