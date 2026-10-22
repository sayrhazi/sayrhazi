#!/usr/bin/env python3
"""Invalidation des verdicts périmés et rework ciblé (Lot 7).

Lorsqu'un fichier examiné par un contrôle est modifié, le verdict associé
devient périmé jusqu'à sa nouvelle exécution. Le rework est routé vers
l'agent responsable du périmètre :
- défaut frontend -> Ali (build-front)
- défaut backend -> Bamse (build-back)
- défaut contrat / parcours intégré -> Lawibrahim (architect)

Stdlib uniquement.
"""
from __future__ import annotations

# Nom de contrôle -> agent responsable du rework (rôle stable).
REWORK_ROLE = {
    "review": "architect",
    "security": "build-back",
    "visual_qa": "build-front",
    "integrated": "architect",
}

# Nom de contrôle -> périmètres de fichiers qu'il examine (préfixes ou noms exacts).
# À adapter par projet dans sayrhazi.yaml si nécessaire.
CONTROL_SCOPES = {
    "review": ("src/", "lib/", "app/", "pages/", "components/"),
    "security": ("src/", "lib/", "app/", "config/", "routes/"),
    "visual_qa": ("src/", "public/", "assets/", "styles/"),
    "integrated": ("src/", "lib/", "app/", "routes/", "api/"),
}

# Etat d'un controle après invalidation.
INVALIDATED = "En attente"


def invalidate_controls(controls: dict, modified_files: list[str],
                        project_scopes: dict | None = None) -> dict:
    """Invalide les contrôles dont le périmètre a été modifié.

    `controls` : {nom_controle: etat} (OK/KO/Non requis/En attente).
    `modified_files` : liste des chemins modifiés depuis la dernière exécution.
    `project_scopes` : surcharge optionnelle des périmètres par contrôle.

    Retourne un nouveau dict {nom_controle: nouvel_etat}. Les contrôles non
    affectés conservent leur état. Les contrôles déjà "En attente" ou
    "Non requis" ne sont pas modifiés.
    """
    scopes = project_scopes or CONTROL_SCOPES
    result: dict = {}
    for name, etat in (controls or {}).items():
        etat = str(etat).strip()
        if etat in ("En attente", "Non requis"):
            result[name] = etat
            continue
        scopes_for = scopes.get(name, ())
        if not scopes_for:
            result[name] = etat
            continue
        affected = any(
            any(f.startswith(prefix) for prefix in scopes_for)
            for f in modified_files
        )
        result[name] = INVALIDATED if affected else etat
    return result


def rework_role(control_name: str) -> str | None:
    """Retourne le rôle responsable du rework pour un contrôle donné."""
    return REWORK_ROLE.get(control_name)


def controls_to_replay(controls: dict) -> list[str]:
    """Retourne la liste des contrôles à rejouer (état En attente, invalide ou KO)."""
    return [name for name, etat in (controls or {}).items()
            if str(etat).strip() in ("En attente", INVALIDATED, "KO")]


def is_rework_needed(controls: dict) -> bool:
    """Vrai si au moins un contrôle doit être rejoué."""
    return len(controls_to_replay(controls)) > 0


def main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Calcule les contrôles à rejouer et le rôle de rework")
    parser.add_argument("project", help="racine du projet (non utilisé, réservé)")
    parser.add_argument("--controls", default=None,
                        help="etats au format nom:etat,nom:etat")
    parser.add_argument("--modified", default=None,
                        help="fichiers modifiés au format f1,f2,f3")
    parser.add_argument("--rework-role", default=None,
                        help="nom du contrôle pour connaître le rôle de rework")
    args = parser.parse_args()

    controls = None
    if args.controls:
        controls = {}
        for item in args.controls.split(","):
            if ":" in item:
                name, etat = item.split(":", 1)
                controls[name.strip()] = etat.strip()

    modified = [f.strip() for f in (args.modified or "").split(",") if f.strip()]

    if args.rework_role:
        role = rework_role(args.rework_role)
        print(role or "inconnu")
        return 0

    if controls is None:
        print("Aucun contrôle fourni.")
        return 1

    new_controls = invalidate_controls(controls, modified)
    replay = controls_to_replay(new_controls)
    print("Controles à rejouer :", ", ".join(replay) if replay else "aucun")
    for name in replay:
        role = rework_role(name)
        print(f"  {name} -> rework vers {role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
