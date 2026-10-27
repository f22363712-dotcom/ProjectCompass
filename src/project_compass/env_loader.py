from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from pathlib import Path


_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_env_file(
    path: Path,
    environment: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Load a small, predictable .env file without overriding the process."""
    if not path.is_file():
        return {}

    target = environment if environment is not None else os.environ
    loaded: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = (part.strip() for part in line.split("=", 1))
        if not _NAME.fullmatch(name):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        loaded[name] = value
        target.setdefault(name, value)
    return loaded
