#!/usr/bin/env python3
"""Bump version Sayrhazi — aligne tous les porteurs de version (stdlib uniquement).

Source de vérité d'entrée : la section en tête de CHANGELOG.md (`## X.Y.Z`).
Cibles : VERSION, pyproject.toml, package.json, package-lock.json,
template sayrhazi.yaml, bannière README.md.

Usage :
    python engine/bump_version.py [--check] [--root .]
    --check : vérifie l'alignement sans écrire (exit 1 si dérive).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

VERSION_RE = re.compile(r"^## (\d+\.\d+\.\d+)(?:\s+—.*)?$", re.MULTILINE)


def top_version(changelog: str) -> str | None:
    """Version de la première section `## X.Y.Z` du changelog."""
    match = VERSION_RE.search(changelog)
    return match.group(1) if match else None


def _replace_once(text: str, pattern: str, repl: str, path: str) -> str:
    new, count = re.subn(pattern, repl, text, count=1)
    if count != 1:
        raise ValueError(f"motif introuvable une fois dans {path}")
    return new


def apply_version(root: Path, version: str) -> list[str]:
    """Écrit `version` dans tous les porteurs. Retourne les fichiers modifiés."""
    changed: list[str] = []

    def _write(path: Path, new_text: str, old_text: str) -> None:
        if new_text != old_text:
            path.write_text(new_text, encoding="utf-8")
            changed.append(str(path.relative_to(root)))

    # VERSION
    p = root / "VERSION"
    _write(p, version + "\n", p.read_text(encoding="utf-8"))

    # pyproject.toml
    p = root / "pyproject.toml"
    text = p.read_text(encoding="utf-8")
    _write(p, _replace_once(text, r'(?m)^version\s*=\s*"\d+\.\d+\.\d+"$',
                            f'version = "{version}"', "pyproject.toml"), text)

    # package.json
    p = root / "package.json"
    text = p.read_text(encoding="utf-8")
    _write(p, _replace_once(text, r'"version":\s*"\d+\.\d+\.\d+"',
                            f'"version": "{version}"', "package.json"), text)

    # package-lock.json
    p = root / "package-lock.json"
    text = p.read_text(encoding="utf-8")
    _write(p, _replace_once(text, r'(?m)^\s*"version"\s*:\s*"\d+\.\d+\.\d+"',
                            f'  "version": "{version}"', "package-lock.json"), text)

    # template sayrhazi.yaml
    p = root / "runtimes" / "opencode" / "templates" / "sayrhazi.yaml"
    text = p.read_text(encoding="utf-8")

    def _tmpl_repl(match: "re.Match") -> str:
        return match.group(1) + '"' + version + '"'

    new_text, count = re.subn(r'(?m)^(\s*version:\s*)"\d+\.\d+\.\d+"', _tmpl_repl, text, count=1)
    if count != 1:
        raise ValueError("motif introuvable une fois dans sayrhazi.yaml")
    _write(p, new_text, text)

    # bannière README.md (largeur de boîte conservée)
    p = root / "README.md"
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        match = re.search(r"v\d+\.\d+\.\d+ - sayrhazi", line)
        if match:
            newline = "\n" if line.endswith("\n") else ""
            body = line.rstrip("\n")
            new_body = body[: match.start()] + f"v{version} - sayrhazi" + body[match.end():]
            if len(new_body) != len(body) and new_body.endswith("║"):
                pad = len(body) - len(new_body)
                new_body = new_body[:-1] + (" " * pad + "║" if pad > 0 else "║")
            lines[i] = new_body + newline
            break
    else:
        raise ValueError("bannière version introuvable dans README.md")
    _write(p, "".join(lines), p.read_text(encoding="utf-8"))
    return changed


def check_alignment(root: Path) -> list[str]:
    """Retourne les dérives `fichier: attendu vs trouvé` (vide si aligné)."""
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    version = top_version(changelog)
    if not version:
        return ["CHANGELOG.md: aucune section `## X.Y.Z` en tête"]
    drifts: list[str] = []
    checks = [
        ("VERSION", (root / "VERSION").read_text(encoding="utf-8").strip(), version),
        ("pyproject.toml", _grab(root / "pyproject.toml", r'(?m)^version\s*=\s*"(\d+\.\d+\.\d+)"'), version),
        ("package.json", _grab(root / "package.json", r'"version":\s*"(\d+\.\d+\.\d+)"'), version),
        ("package-lock.json", _grab(root / "package-lock.json", r'(?m)^\s*"version"\s*:\s*"(\d+\.\d+\.\d+)"'), version),
        ("sayrhazi.yaml", _grab(root / "runtimes" / "opencode" / "templates" / "sayrhazi.yaml",
                                r'(?m)^\s*version:\s*"(\d+\.\d+\.\d+)"'), version),
        ("README.md", _grab(root / "README.md", r"v(\d+\.\d+\.\d+) - sayrhazi"), version),
    ]
    for name, found, want in checks:
        if found != want:
            drifts.append(f"{name}: {found!r} au lieu de {want!r}")
    return drifts


def _grab(path: Path, pattern: str):
    try:
        match = re.search(pattern, path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(1) if match else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Aligne les versions sur le CHANGELOG")
    parser.add_argument("--check", action="store_true", help="vérifie sans écrire")
    parser.add_argument("--root", default=".", help="racine du dépôt")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    if args.check:
        drifts = check_alignment(root)
        for drift in drifts:
            print("DÉRIVE : " + drift)
        if drifts:
            print(f"{len(drifts)} fichier(s) désaligné(s).")
            return 1
        print("versions alignées.")
        return 0

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    version = top_version(changelog)
    if not version:
        print("ERREUR : aucune section `## X.Y.Z` en tête de CHANGELOG.md.")
        return 2
    try:
        changed = apply_version(root, version)
    except ValueError as exc:
        print(f"ERREUR : {exc}")
        return 2
    if changed:
        print(f"version {version} appliquée à : " + ", ".join(changed))
    else:
        print(f"version {version} déjà alignée partout.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
