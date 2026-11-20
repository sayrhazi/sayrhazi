#!/usr/bin/env python3
"""Resolver Sayrhazi - compatibilite noyau / projet installe (stdlib uniquement).

Compare workflow.version du projet a celle du noyau et rend un plan :
up-to-date | update-available | unknown. Utilise par le CLI (info) et les tests.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

VERSION_RE = re.compile(r'^\s*version:\s*"([^"]+)"', re.MULTILINE)


def core_root() -> Path | None:
    try:
        root = Path(__file__).resolve().parent.parent
    except NameError:
        return None
    if (root / "runtimes" / "opencode" / "templates" / "sayrhazi.yaml").is_file():
        return root
    return None


def read_version(yaml_path: Path) -> str | None:
    try:
        m = VERSION_RE.search(yaml_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return m.group(1).strip() if m else None


def resolve(project: Path, root: Path | None = None) -> dict:
    root = root or core_root()
    if root is None:
        return {"installed": None, "latest": None, "status": "unknown"}
    latest = read_version(root / "runtimes" / "opencode" / "templates" / "sayrhazi.yaml")
    installed = read_version(project / ".opencode" / "sayrhazi.yaml")
    if installed is None or latest is None:
        status = "unknown"
    elif installed == latest:
        status = "up-to-date"
    else:
        status = "update-available"
    return {"installed": installed, "latest": latest, "status": status}


if __name__ == "__main__":
    info = resolve(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("."))
    print(info)
