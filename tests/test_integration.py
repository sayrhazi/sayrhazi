#!/usr/bin/env python3
"""Tests integration Sayrhazi : install -> check -> update -> remove. Stdlib.

Usage : python tests/test_integration.py
"""
from __future__ import annotations

import sys
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "cli" / "commands"))

from installer import install_project  # noqa: E402
from updater import migrate_config  # noqa: E402
from report import parse_report, validate_report  # noqa: E402
from fullstack import read_fullstack, update_section, validate_fullstack  # noqa: E402
from archive import archive_fullstack, is_archived, archive_entries, validate_archive_conditions  # noqa: E402
from rework import invalidate_controls, rework_role, controls_to_replay, is_rework_needed  # noqa: E402


def fill_config(p: Path) -> None:
    text = p.read_text(encoding="utf-8")
    for key in ("name: A_COMPLETER", "description: A_COMPLETER", "language: A_COMPLETER",
                "framework: A_COMPLETER", "build: A_COMPLETER", "test: A_COMPLETER", "lint: A_COMPLETER"):
        text = text.replace(key, key.replace("A_COMPLETER", "test"))
    p.write_text(text, encoding="utf-8")


class TestIntegration(unittest.TestCase):
    def test_cycle_complet(self):
        import re as _re
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            install_project(project, ROOT)
            self.assertTrue((project / ".opencode" / "agent" / "bamse.md").is_file())
            self.assertTrue((project / ".opencode" / "watch-work.py").is_file())
            self.assertTrue((project / ".opencode" / "services.py").is_file())
            self.assertEqual(len(list((project / ".opencode" / "agent").glob("*.md"))), 6)
            # tunda-equivalent : cles presentes ?
            text = (project / ".opencode" / "sayrhazi.yaml").read_text(encoding="utf-8")
            for key in ("workflow:", "project:", "agents:", "technical:", "commands:", "quality:"):
                self.assertIn(key, text)
            fill_config(project / ".opencode" / "sayrhazi.yaml")
            (project / "AGENTS.md").write_text("# R\n- Cle\n", encoding="utf-8")

    def test_state_dir_cree(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            install_project(project, ROOT)
            self.assertTrue((project / ".opencode" / "state").is_dir())

    def test_backup_dir_et_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            install_project(project, ROOT)
            self.assertTrue((project / ".opencode" / "backup").is_dir())
            cfg = project / ".opencode" / "sayrhazi.yaml"
            text = cfg.read_text(encoding="utf-8")
            text = text.replace("  designer: Ali\n", "")
            cfg.write_text(text, encoding="utf-8")
            added, backup, _warns = migrate_config(
                cfg, ROOT / "runtimes" / "opencode" / "templates" / "sayrhazi.yaml",
                project / ".opencode" / "backup")
            self.assertIsNotNone(backup)
            self.assertTrue(str(backup).replace("\\", "/").startswith(
                str(project / ".opencode" / "backup").replace("\\", "/")))

    def test_workflow_cree_jamais_ecrase(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            install_project(project, ROOT)
            wf = project / ".opencode" / "workflow.yaml"
            self.assertTrue(wf.is_file())
            wf.write_text("workflow:\n  mode: supervised\n  auto_start: false\n  max_parallel_agents: 1\nstages: []\n",
                          encoding="utf-8")
            install_project(project, ROOT)
            self.assertIn("max_parallel_agents: 1", wf.read_text(encoding="utf-8"))

    def test_migration_douce(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            install_project(project, ROOT)
            cfg = project / ".opencode" / "sayrhazi.yaml"
            text = cfg.read_text(encoding="utf-8")
            text = text.replace("  designer: Ali\n", "")
            text = text.replace("  database_type: none", "  database: matest")
            text = re.sub(r'  version: "[^"]+"', '  version: "0.1.0"', text)
            cfg.write_text(text, encoding="utf-8")
            added, backup, warns = migrate_config(cfg, ROOT / "runtimes" / "opencode" / "templates" / "sayrhazi.yaml",
                                                  project / ".opencode" / "history")
            self.assertTrue(any("designer" in a for a in added), added)
            self.assertTrue(any("database_type" in a for a in added), added)
            self.assertIsNotNone(backup)
            after = cfg.read_text(encoding="utf-8")
            self.assertIn("database_name: matest", after)


class TestFullstackFlow(unittest.TestCase):
    """Parcours fullstack bout en bout : plan -> sections -> rework -> archivage."""

    def _make_project(self, tmp):
        project = Path(tmp)
        install_project(project, ROOT)
        resume = project / ".opencode" / "resume"
        history = project / ".opencode" / "history"
        resume.mkdir(parents=True, exist_ok=True)
        history.mkdir(parents=True, exist_ok=True)
        return project

    def _write_plan(self, project, scope="fullstack", controls=None):
        lines = [
            "task_id: FEATURE-001",
            "title: Test fullstack",
            "status: À IMPLÉMENTER",
            "agent: architect",
            "agents_required: [build-back, build-front]",
            f"scope: {scope}",
        ]
        if controls:
            lines.append("controls:")
            for k, v in controls.items():
                lines.append(f"  {k}: {v}")
        lines.append('completed_at: "2026-10-22T10:00:00+04:00"')
        lines.append("summary: Plan valide.")
        (project / ".opencode" / "resume" / "plan.txt").write_text(
            "\n".join(lines), encoding="utf-8")

    def _write_fullstack(self, project, frontend_status="TERMINÉ", backend_status="TERMINÉ"):
        content = (
            "task_id: FEATURE-001\n"
            "scope: fullstack\n"
            "status: EN COURS\n\n"
            "## Étape frontend — Ali\n"
            "agent: frontend\n"
            f"stage_status: {frontend_status}\n"
            'completed_at: "2026-10-22T10:00:00+04:00"\n'
            "summary: UI faite.\n\n"
            "## Étape backend — Bamse\n"
            "agent: backend\n"
            f"stage_status: {backend_status}\n"
            'completed_at: "2026-10-22T11:00:00+04:00"\n'
            "summary: API faite.\n"
        )
        (project / ".opencode" / "resume" / "fullstack.txt").write_text(content, encoding="utf-8")

    def test_plan_scope_fullstack(self):
        """Le plan déclare scope fullstack avec build-front+build-back."""
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            self._write_plan(project, scope="fullstack")
            data = parse_report((project / ".opencode" / "resume" / "plan.txt").read_text(encoding="utf-8"))
            self.assertEqual(data["scope"], "fullstack")
            self.assertIn("build-front", data["agents_required"])
            self.assertIn("build-back", data["agents_required"])
            self.assertEqual(validate_report(data), [], validate_report(data))

    def test_fullstack_sections_non_destructive(self):
        """Mettre à jour la section backend préserve la section frontend."""
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            self._write_fullstack(project)
            headers, sections = read_fullstack(project / ".opencode" / "resume" / "fullstack.txt")
            self.assertEqual(headers["scope"], "fullstack")
            self.assertIn("stage_status: TERMINÉ", "\n".join(sections["frontend"]))
            self.assertIn("stage_status: TERMINÉ", "\n".join(sections["backend"]))

            # Mise à jour backend uniquement
            update_section(project / ".opencode" / "resume" / "fullstack.txt",
                           "backend",
                           ["agent: backend", "stage_status: BLOQUÉ", "summary: Erreur API."])
            headers2, sections2 = read_fullstack(project / ".opencode" / "resume" / "fullstack.txt")
            self.assertIn("stage_status: BLOQUÉ", "\n".join(sections2["backend"]))
            self.assertIn("summary: UI faite.", "\n".join(sections2["frontend"]))
            self.assertEqual(validate_fullstack(headers2, sections2), [], validate_fullstack(headers2, sections2))

    def test_fullstack_archivage_idempotent(self):
        """L'archivage est idempotent : pas de doublon après redémarrage."""
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            self._write_fullstack(project)
            ok, _msg = archive_fullstack(project, "FEATURE-001",
                                         controls={"review": "OK", "security": "OK"},
                                         hadji_verdict="OK")
            self.assertTrue(ok)
            self.assertTrue(is_archived(project, "FEATURE-001"))
            n1 = len(archive_entries(project))
            # Deuxième appel : idempotent
            ok2, _msg2 = archive_fullstack(project, "FEATURE-001",
                                           controls={"review": "OK", "security": "OK"},
                                           hadji_verdict="OK")
            self.assertTrue(ok2)
            n2 = len(archive_entries(project))
            self.assertEqual(n1, n2, "l'archive ne doit pas dupliquer")

    def test_fullstack_archivage_bloque_si_ko(self):
        """L'archivage est bloqué si un contrôle requis est KO."""
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            self._write_fullstack(project)
            erreurs = validate_archive_conditions(
                {"review": "KO", "security": "OK"}, "OK", ["review", "security"])
            self.assertTrue(any("review" in v for v in erreurs), erreurs)

    def test_fullstack_archivage_bloque_si_hadji_pas_ok(self):
        """L'archivage est bloqué si le verdict Hadji n'est pas OK."""
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            self._write_fullstack(project)
            erreurs = validate_archive_conditions(
                {"review": "OK", "security": "OK"}, "KO", ["review", "security"])
            self.assertTrue(any("Hadji" in v for v in erreurs), erreurs)

    def test_rework_invalide_apres_modification_frontend(self):
        """Une modification de fichier frontend invalide le contrôle visual_qa."""
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            controls = {"review": "OK", "security": "OK", "visual_qa": "OK", "integrated": "OK"}
            modified = ["public/styles.css"]
            result = invalidate_controls(controls, modified)
            self.assertEqual(result["visual_qa"], "En attente")
            self.assertEqual(result["review"], "OK")
            self.assertEqual(result["security"], "OK")
            self.assertEqual(result["integrated"], "OK")

    def test_rework_role_routing(self):
        """Le rework est routé vers le bon agent selon le contrôle."""
        self.assertEqual(rework_role("review"), "architect")
        self.assertEqual(rework_role("security"), "build-back")
        self.assertEqual(rework_role("visual_qa"), "build-front")
        self.assertEqual(rework_role("integrated"), "architect")

    def test_rework_a_rejouer(self):
        """Les contrôles En attente et KO doivent être rejoués."""
        controls = {"review": "En attente", "security": "OK", "visual_qa": "KO"}
        self.assertEqual(sorted(controls_to_replay(controls)), ["review", "visual_qa"])
        self.assertTrue(is_rework_needed(controls))
        self.assertFalse(is_rework_needed({"review": "OK", "security": "OK"}))


if __name__ == "__main__":
    unittest.main(verbosity=1)
