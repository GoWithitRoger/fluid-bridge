from __future__ import annotations

import re
from pathlib import Path

from fluid_bridge.capabilities import UPSTREAM_COMMANDS


def test_capability_matrix_covers_every_pinned_command_once() -> None:
    matrix = Path("docs/CAPABILITIES.md").read_text(encoding="utf-8")
    documented = re.findall(r"^\| `([^`]+)` \|", matrix, flags=re.MULTILINE)

    assert len(documented) == len(set(documented))
    assert set(documented) == set(UPSTREAM_COMMANDS)
