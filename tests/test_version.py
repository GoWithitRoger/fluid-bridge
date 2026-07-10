from __future__ import annotations

import tomllib
from pathlib import Path

from fluid_bridge import __version__


def test_package_versions_match() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert __version__ == pyproject["project"]["version"]
