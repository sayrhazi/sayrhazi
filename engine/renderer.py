#!/usr/bin/env python3
"""Renderer Sayrhazi - genere les .md OpenCode depuis core/agents (trio).

Source de verite : core/agents/<id>/{agent.yaml, instructions.md}.
Sortie : runtimes/opencode/agents/<id>.md (frontmatter + corps).
Le rendu doit etre byte-identique aux fichiers generes : toute derive
signifie trio ou renderer a corriger, jamais l'adaptateur a la main.

Usage :
    python engine/renderer.py [--check]

--check : verifie seulement (exit 1 si derive).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def render_one(trio_dir: Path) -> str:
    agent = (trio_dir / "agent.yaml").read_text(encoding="utf-8")
    body = (trio_dir / "instructions.md").read_text(encoding="utf-8")

    def field(key: str) -> str | None:
        m = re.search(r"^" + re.escape(key) + r":\s*(.+)$", agent, re.M)
        return m.group(1).strip() if m else None

    lines = [
        "---",
        "description: " + (field("description") or ""),
        "mode: " + (field("mode") or ""),
        "model: " + (field("model") or ""),
        "color: " + (field("color") or ""),
    ]
    perm: list[str] = []
    in_perm = False
    for line in agent.splitlines():
        if re.match(r"^permission:\s*$", line):
            in_perm = True
            continue
        if in_perm:
            if re.match(r"^  \S", line):
                perm.append(line)
            else:
                break
    if perm:
        lines.append("permission:")
        lines.extend(perm)
    lines.append("---")
    return "\n".join(lines) + body if body.startswith("\n") else "\n".join(lines) + "\n" + body


def main() -> int:
    parser = argparse.ArgumentParser(description="Renderer Sayrhazi core -> opencode")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    try:
        root = Path(__file__).resolve().parent.parent
    except NameError:
        print("ERREUR : racine introuvable.")
        return 2
    trio_root = root / "core" / "agents"
    out_root = root / "runtimes" / "opencode" / "agents"
    drift = 0
    for trio in sorted(trio_root.iterdir()):
        if not trio.is_dir():
            continue
        rendered = render_one(trio)
        target = out_root / (trio.name + ".md")
        current = target.read_text(encoding="utf-8") if target.is_file() else None
        if args.check:
            if current != rendered:
                print("DERIVE : " + trio.name)
                drift += 1
        else:
            out_root.mkdir(parents=True, exist_ok=True)
            # LF explicite : write_text convertirait \n en CRLF sous Windows
            # alors que les adaptateurs versionnes sont en LF.
            with open(target, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(rendered)
            print(("regenere" if current != rendered else "identique") + " : " + trio.name)
    if args.check and drift:
        print(f"{drift} adaptateur(s) en derive.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
