"""Regression tests for packaging metadata drift."""
from __future__ import annotations

from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _PROJECT_ROOT / "pyproject.toml"


def test_pyproject_uses_current_spdx_license_metadata():
    text = _PYPROJECT.read_text()

    assert 'license = "MIT"' in text
    assert 'license-files = ["LICENSE"]' in text
    assert "license = {" not in text
    assert "License :: OSI Approved :: MIT License" not in text


def test_build_backend_floor_supports_spdx_license_field():
    text = _PYPROJECT.read_text()

    assert 'requires = ["setuptools>=77", "wheel"]' in text
