import tomllib
from pathlib import Path

from app import __version__

ROOT = Path(__file__).resolve().parents[2]


def test_app_version_matches_pyproject() -> None:
    """A v1.1.0 saiu com `/health` respondendo 1.0.0: as duas versões andam juntas."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert __version__ == project["version"]


def test_release_notes_exist_for_current_version() -> None:
    assert (ROOT / "docs" / f"release-v{__version__}.md").is_file()
