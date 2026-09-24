"""Repository-level completeness contracts."""
from __future__ import annotations

import re
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read(relative: str) -> str:
    return (_PROJECT_ROOT / relative).read_text(encoding="utf-8")


def test_required_project_files_exist():
    for relative in (
        "README.md",
        "README.zh-CN.md",
        "CHANGELOG.md",
        "LICENSE",
        "pyproject.toml",
        ".github/workflows/ci.yml",
        ".github/workflows/codeql.yml",
    ):
        assert (_PROJECT_ROOT / relative).is_file(), f"missing {relative}"


def test_readmes_link_release_history_license_and_downloads():
    readme = _read("README.md")
    readme_zh = _read("README.zh-CN.md")

    assert "[Release history](CHANGELOG.md)" in readme
    assert "[MIT license](LICENSE)" in readme
    assert "https://github.com/zhuhroscar-tech/enospc-doctor/releases" in readme
    assert "SHA256SUMS.txt" in readme

    assert "[发布历史](CHANGELOG.md)" in readme_zh
    assert "[MIT 许可证](LICENSE)" in readme_zh
    assert "https://github.com/zhuhroscar-tech/enospc-doctor/releases" in readme_zh
    assert "SHA256SUMS.txt" in readme_zh


def test_changelog_documents_current_release():
    pyproject = _read("pyproject.toml")
    version_match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert version_match, "missing project version"

    changelog = _read("CHANGELOG.md")
    version = version_match.group(1)
    assert f"## v{version} - " in changelog
    assert "## v0.1.9 - 2026-09-24" in changelog
    assert "## v0.1.0 - 2026-09-10" in changelog


def test_ci_covers_tests_build_pyz_and_release_assets():
    ci = _read(".github/workflows/ci.yml")

    assert "python -m pytest -v" in ci
    assert "python -m build" in ci
    assert "python -m zipapp" in ci
    assert "dist/enospc-doctor.pyz" in ci
    assert "sha256sum * > SHA256SUMS.txt" in ci
    assert "actions/upload-artifact@v4" in ci


def test_codeql_workflow_is_configured_for_python():
    codeql = _read(".github/workflows/codeql.yml")

    assert "github/codeql-action/init@v3" in codeql
    assert "github/codeql-action/analyze@v3" in codeql
    assert "languages: python" in codeql
