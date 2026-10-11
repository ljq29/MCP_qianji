from __future__ import annotations

from pathlib import Path

from qianji import __version__
from qianji.server.app import app

ROOT = Path(__file__).resolve().parents[1]
IMAGE_REF = f"mcp_qianji:{__version__}"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_runtime_version_uses_single_source():
    assert __version__ == "0.0.30"
    assert app.version == __version__


def test_pyproject_reads_version_from_package_source():
    pyproject = _read("pyproject.toml")

    assert 'dynamic = ["version"]' in pyproject
    assert 'path = "qianji/version.py"' in pyproject
    assert '\nversion = "' not in pyproject


def test_release_version_defaults_are_in_sync():
    dockerfile = _read("Dockerfile")
    compose = _read("docker-compose.yml")
    readme = _read("README.md")
    env_example = _read(".env.example")

    assert f"ARG APP_VERSION={__version__}" in dockerfile
    assert f"ARG IMAGE_REF={IMAGE_REF}" in dockerfile
    assert f"QIANJI_IMAGE_TAG:-{__version__}" in compose
    assert f"QIANJI_IMAGE_TAG={__version__}" in env_example
    assert IMAGE_REF in readme
    assert f"当前统一版本：`{__version__}`。" in readme
    assert "0.1.1" not in dockerfile + compose + readme


def test_changelog_documents_current_version():
    changelog = _read("CHANGELOG.md")

    assert f"## [{__version__}]" in changelog
