#!/usr/bin/env python3
"""Tests core Sayrhazi : parser YAML, schema, renderer, state. Stdlib uniquement.

Usage : python tests/test_core.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "cli" / "commands"))

from sayrhazi_config import load_schema, parse_simple_yaml, validate_against_schema  # noqa: E402


class TestVersionConsistency(unittest.TestCase):
    def test_version_partout(self):
        import re
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{version}"', pyproject)
        template = (ROOT / "runtimes" / "opencode" / "templates" / "sayrhazi.yaml").read_text(
            encoding="utf-8")
        self.assertIn(f'version: "{version}"', template)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"v{version} - sayrhazi", readme)
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertTrue(changelog.startswith(f"## {version} ")
                        or f"\n## {version} " in changelog)
        package = (ROOT / "package.json").read_text(encoding="utf-8")
        self.assertIn(f'"version": "{version}"', package)


class TestParser(unittest.TestCase):
    def test_template_se_parse(self):
        text = (ROOT / "runtimes" / "opencode" / "templates" / "sayrhazi.yaml").read_text(encoding="utf-8")
        data, err = parse_simple_yaml(text)
        self.assertIsNone(err, err)
        for key in ("workflow", "project", "agents", "technical", "commands", "runtime", "quality", "integration"):
            self.assertIn(key, data)

    def test_scalaires(self):
        data, err = parse_simple_yaml("a: 1\nb: true\nc: null\nd: [x, y]\ne: \"0.2.5\"\n")
        self.assertIsNone(err, err)
        self.assertEqual(data, {"a": 1, "b": True, "c": None, "d": ["x", "y"], "e": "0.2.5"})

    def test_erreurs(self):
        for bad in ("cle sans deux points\n", "- item\n", "a:\n   b: 1\n"):
            _, err = parse_simple_yaml(bad)
            self.assertIsNotNone(err, bad)


class TestSchema(unittest.TestCase):
    def test_template_conforme(self):
        schema, err = load_schema(ROOT)
        self.assertIsNone(err, err)
        text = (ROOT / "runtimes" / "opencode" / "templates" / "sayrhazi.yaml").read_text(encoding="utf-8")
        text = text.replace("A_COMPLETER", "test")
        data, perr = parse_simple_yaml(text)
        self.assertIsNone(perr, perr)
        violations = validate_against_schema(data, schema)
        self.assertEqual(violations, [], violations)

    def test_enum_refuse(self):
        schema, _ = load_schema(ROOT)
        data, _ = parse_simple_yaml("workflow:\n  name: Sayrhazi\n  version: x\nproject:\n  name: P\n  type: api\nagents:\n  architect: A\n  designer: D\n  builder: B\n  reviewer: R\n  security: S\n  qa: Q\ntechnical:\n  language: L\n  framework: F\n  database_type: oracle\n  database_driver: none\n  database_name: null\n  package_manager: yarn\ncommands:\n  install: i\n  dev: d\n  build: b\n  test: t\n  lint: l\nquality:\n  security_audit_required_by_default: true\n  visual_qa_required: false\n  required_checks: []\n")
        violations = validate_against_schema(data, schema)
        self.assertTrue(any("database_type" in v for v in violations), violations)


class TestRenderer(unittest.TestCase):
    def test_zero_derive(self):
        sys.path.insert(0, str(ROOT / "engine"))
        from renderer import render_one
        for trio in sorted((ROOT / "core" / "agents").iterdir()):
            if trio.is_dir():
                expected = (ROOT / "runtimes" / "opencode" / "agents" / (trio.name + ".md")).read_text(encoding="utf-8")
                self.assertEqual(render_one(trio), expected, trio.name)

    def test_lecture_frugale_partout(self):
        agents = ("lawibrahim", "ali", "bamse", "hadji", "hifadhui", "zawadi")
        for name in agents:
            core = (ROOT / "core" / "agents" / name / "instructions.md").read_text(encoding="utf-8")
            self.assertIn("## Lecture frugale", core, name)
            self.assertIn("git status", core, name)
            self.assertIn("Proportionne ton effort", core, name)
            rendu = (ROOT / "runtimes" / "opencode" / "agents" / (name + ".md")).read_text(encoding="utf-8")
            self.assertIn("## Lecture frugale", rendu, name)

    def test_cahier_des_charges_d_abord(self):
        for base in ("core/agents/lawibrahim/instructions.md",
                     "runtimes/opencode/agents/lawibrahim.md"):
            text = (ROOT / base).read_text(encoding="utf-8")
            self.assertIn("## Cahier des charges d'abord", text, base)
            self.assertIn("cahier-des-charges.md", text, base)
            self.assertIn("sans lui, pas de `plan.txt`", text, base)


class TestReport(unittest.TestCase):
    def test_templates_valides(self):
        from report import parse_report, validate_report, validate_task
        task = parse_report((ROOT / "core" / "templates" / "task.md").read_text(encoding="utf-8"))
        self.assertEqual(validate_task(task), [], validate_task(task))
        rep = parse_report((ROOT / "core" / "templates" / "report.md").read_text(encoding="utf-8"))
        self.assertEqual(validate_report(rep), [], validate_report(rep))

    def test_champs_requis(self):
        from report import parse_report, validate_report
        rep = parse_report("task_id: FEATURE-001\nagent: build-back\nstatus: TERMINÉ\ncompleted_at: \"2026-09-26T10:00:00+04:00\"\n")
        self.assertTrue(any("summary" in v for v in validate_report(rep)), validate_report(rep))

    def test_statut_par_role(self):
        from report import parse_report, validate_report
        ok_review = parse_report("task_id: FEATURE-001\nagent: reviewer\nstatus: VALIDÉ\ncompleted_at: \"2026-09-26T10:00:00+04:00\"\nsummary: OK.\n")
        self.assertEqual(validate_report(ok_review), [])
        bad_build = parse_report("task_id: FEATURE-001\nagent: build-back\nstatus: VALIDÉ\ncompleted_at: \"2026-09-26T10:00:00+04:00\"\nsummary: Fait.\n")
        self.assertTrue(any("status" in v for v in validate_report(bad_build)))
        bad_qa = parse_report("task_id: FEATURE-001\nagent: build-back\nstatus: À COMPLÉTER\ncompleted_at: \"2026-09-26T10:00:00+04:00\"\nsummary: Fait.\n")
        self.assertTrue(any("status" in v for v in validate_report(bad_qa)))
        ok_qa = parse_report("task_id: FEATURE-001\nagent: qa\nstatus: À COMPLÉTER\ncompleted_at: \"2026-09-26T10:00:00+04:00\"\nsummary: En attente.\n")
        self.assertEqual(validate_report(ok_qa), [])
        underscored = parse_report("task_id: FEATURE-001\nagent: reviewer\nstatus: VALIDÉ_AVEC_RÉSERVES\ncompleted_at: \"2026-09-26T10:00:00+04:00\"\nsummary: OK.\n")
        self.assertEqual(validate_report(underscored), [])

    def test_version_horodatage_refusee(self):
        from report import parse_report, validate_report
        rep = parse_report("task_id: FEATURE-001\nagent: build-back\nstatus: TERMINÉ\nversion: \"2026-09-23T19:30:00+04:00\"\nsummary: Fait.\n")
        self.assertTrue(any("version" in v for v in validate_report(rep)))

    def test_bom_ignore(self):
        from report import parse_report, validate_report
        rep = parse_report("\ufeff" + "task_id: FEATURE-001\nagent: build-back\nstatus: TERMINÉ\n"
                           'completed_at: "2026-09-26T10:00:00+04:00"\nsummary: Fait.\n')
        self.assertEqual(rep.get("task_id"), "FEATURE-001")
        self.assertEqual(validate_report(rep), [])

    def test_task_id_formats(self):
        from report import parse_report, validate_report
        for tid in ("FEATURE-001", "MPANGO-2026-014"):
            rep = parse_report(f"task_id: {tid}\nagent: build-back\nstatus: TERMINÉ\ncompleted_at: \"2026-09-26T10:00:00+04:00\"\nsummary: Fait.\n")
            self.assertEqual(validate_report(rep), [], tid)
        for tid in ("AB", "a b"):
            rep = parse_report(f"task_id: {tid}\nagent: build-back\nstatus: TERMINÉ\ncompleted_at: \"2026-09-26T10:00:00+04:00\"\nsummary: Fait.\n")
            self.assertTrue(any("task_id" in v for v in validate_report(rep)), tid)

    def test_coherence_indicateurs(self):
        from report import parse_report, validate_task
        incoherent = parse_report("task_id: FEATURE-001\ntitle: T\nstatus: À IMPLÉMENTER\nagents_required: [build-back, reviewer]\nsecurity_required: true\n")
        self.assertTrue(any("security" in v for v in validate_task(incoherent)))
        coherent = parse_report("task_id: FEATURE-001\ntitle: T\nstatus: À IMPLÉMENTER\nagents_required: [build-back, reviewer, security]\nsecurity_required: true\n")
        self.assertEqual(validate_task(coherent), [])

    def test_transition(self):
        from report import check_transition, parse_report
        rep = parse_report("task_id: FEATURE-001\nagent: build-back\nstatus: TERMINÉ\ncompleted_at: \"2026-09-26T10:00:00+04:00\"\nsummary: Fait.\n")
        self.assertEqual(check_transition(rep, expected_agent="build-back", expected_task_id="FEATURE-001",
                                          output_text="review FEATURE-001 ok"), [])
        self.assertTrue(check_transition(rep, expected_agent="build-back", expected_task_id="FEATURE-002"))
        self.assertTrue(check_transition(rep, expected_agent="reviewer", expected_task_id="FEATURE-001"))
        stale = parse_report("task_id: FEATURE-001\nagent: build-back\nstatus: EN COURS\ncompleted_at: \"2026-09-26T10:00:00+04:00\"\nsummary: WIP.\n")
        self.assertTrue(check_transition(stale, expected_agent="build-back", expected_task_id="FEATURE-001",
                                         allowed_statuses=("TERMINÉ",)))
        self.assertTrue(check_transition(rep, expected_agent="build-back", expected_task_id="FEATURE-001",
                                         output_text="review sans identifiant"))


class TestScope(unittest.TestCase):
    def _task(self, **fields):
        from report import parse_report
        base = ("task_id: FEATURE-001\ntitle: T\nstatus: À IMPLÉMENTER\n"
                'completed_at: "2026-09-26T10:00:00+04:00"\nsummary: Fait.\n')
        for key, value in fields.items():
            base += f"{key}: {value}\n"
        return parse_report(base)

    def test_scope_valide_frontend(self):
        from report import validate_task
        rep = self._task(scope="frontend", agents_required="[build-front]")
        self.assertEqual(validate_task(rep), [], validate_task(rep))

    def test_scope_valide_backend(self):
        from report import validate_task
        rep = self._task(scope="backend", agents_required="[build-back]")
        self.assertEqual(validate_task(rep), [], validate_task(rep))

    def test_scope_valide_fullstack(self):
        from report import validate_task
        rep = self._task(scope="fullstack", agents_required="[build-front, build-back]")
        self.assertEqual(validate_task(rep), [], validate_task(rep))

    def test_scope_invalide(self):
        from report import validate_task
        rep = self._task(scope="mobile", agents_required="[build-back]")
        self.assertTrue(any("scope" in v for v in validate_task(rep)), validate_task(rep))

    def test_scope_absent_sans_erreur(self):
        from report import validate_task
        rep = self._task(agents_required="[build-back]")
        self.assertEqual(validate_task(rep), [], validate_task(rep))

    def test_scope_frontend_exige_designer(self):
        from report import validate_task
        rep = self._task(scope="frontend", agents_required="[build-back]")
        self.assertTrue(any("build-front" in v for v in validate_task(rep)), validate_task(rep))

    def test_scope_backend_exige_builder(self):
        from report import validate_task
        rep = self._task(scope="backend", agents_required="[build-front]")
        self.assertTrue(any("build-back" in v for v in validate_task(rep)), validate_task(rep))

    def test_scope_fullstack_exige_designer_et_builder(self):
        from report import validate_task
        rep = self._task(scope="fullstack", agents_required="[build-front]")
        erreurs = validate_task(rep)
        self.assertTrue(any("build-back" in v for v in erreurs), erreurs)

    def _base_task(self, **extra):
        from report import parse_report
        base = ("task_id: FEATURE-001\ntitle: T\nstatus: À IMPLÉMENTER\n"
                'completed_at: "2026-09-26T10:00:00+04:00"\nsummary: Fait.\n')
        for key, value in extra.items():
            base += f"{key}: {value}\n"
        return parse_report(base)

    def test_controle_etat_valide(self):
        from report import validate_task
        data = {"task_id": "F-1", "title": "T", "status": "À IMPLÉMENTER",
                "completed_at": "2026-09-26T10:00:00+04:00", "summary": "Fait.",
                "controls": {"review": "OK", "security": "En attente", "visual_qa": "Non requis", "integrated": "KO"}}
        self.assertEqual(validate_task(data), [], validate_task(data))

    def test_controle_etat_invalide(self):
        from report import validate_task
        data = {"task_id": "F-1", "title": "T", "status": "À IMPLÉMENTER",
                "completed_at": "2026-09-26T10:00:00+04:00", "summary": "Fait.",
                "controls": {"review": "peut-etre"}}
        self.assertTrue(any("etat" in v for v in validate_task(data)), validate_task(data))

    def test_controle_requis_en_attente_refuse(self):
        from report import validate_task
        data = {"task_id": "F-1", "title": "T", "status": "À IMPLÉMENTER",
                "completed_at": "2026-09-26T10:00:00+04:00", "summary": "Fait.",
                "controls_required": ["review"],
                "controls": {"review": "En attente"}}
        self.assertTrue(any("requis" in v and "attente" in v for v in validate_task(data)), validate_task(data))

    def test_controle_requis_non_requis_refuse(self):
        from report import validate_task
        data = {"task_id": "F-1", "title": "T", "status": "À IMPLÉMENTER",
                "completed_at": "2026-09-26T10:00:00+04:00", "summary": "Fait.",
                "controls_required": ["security"],
                "controls": {"security": "Non requis"}}
        self.assertTrue(any("Non requis" in v for v in validate_task(data)), validate_task(data))

    def test_controle_requis_ok_valide(self):
        from report import validate_task
        data = {"task_id": "F-1", "title": "T", "status": "À IMPLÉMENTER",
                "completed_at": "2026-09-26T10:00:00+04:00", "summary": "Fait.",
                "controls_required": ["review"],
                "controls": {"review": "OK"}}
        self.assertEqual(validate_task(data), [], validate_task(data))

    def test_controle_inconnu_refuse(self):
        from report import validate_task
        data = {"task_id": "F-1", "title": "T", "status": "À IMPLÉMENTER",
                "completed_at": "2026-09-26T10:00:00+04:00", "summary": "Fait.",
                "controls_required": ["performance"]}
        self.assertTrue(any("inconnu" in v for v in validate_task(data)), validate_task(data))


class TestFullstackWatcher(unittest.TestCase):
    def _watcher(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "watch_work_fs", str(ROOT / "runtimes" / "opencode" / "scripts" / "watch-work.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_fullstack_section_status_frontend_termine(self):
        w = self._watcher()
        import tempfile
        from pathlib import Path as _P
        with tempfile.TemporaryDirectory() as tmp:
            p = _P(tmp) / "fullstack.txt"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                "task_id: F-1\nscope: fullstack\nstatus: EN COURS\n\n"
                "## Étape frontend — Ali\nagent: frontend\nstage_status: TERMINÉ\nsummary: UI faite.\n\n"
                "## Étape backend — Bamse\nagent: backend\nstage_status: EN COURS\nsummary: API en cours.\n",
                encoding="utf-8")
            self.assertEqual(w.fullstack_section_status(p, "frontend"), "TERMINÉ")
            self.assertEqual(w.fullstack_section_status(p, "backend"), "EN COURS")

    def test_fullstack_section_status_absent(self):
        w = self._watcher()
        import tempfile
        from pathlib import Path as _P
        with tempfile.TemporaryDirectory() as tmp:
            p = _P(tmp) / "fullstack.txt"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("task_id: F-1\nscope: fullstack\nstatus: EN COURS\n", encoding="utf-8")
            self.assertIsNone(w.fullstack_section_status(p, "frontend"))

    def test_eligible_backend_bloque_si_frontend_pas_termine(self):
        w = self._watcher()
        cfg = {"stages": [
            {"id": "backend", "agent": "backend", "auto": True,
             "trigger": {"report": "fullstack.txt", "status": "TERMINÉ"}}
        ]}
        triggers = {"fullstack.txt": {"task_id": "F-1", "status": "TERMINÉ", "mtime": 100.0}}
        outputs = {}
        self.assertEqual(w.eligible_transitions(cfg, triggers, {}, outputs, {}), [])
        fs = {"frontend": "TERMINÉ"}
        got = w.eligible_transitions(cfg, triggers, {}, outputs, fs)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][1], "F-1")

    def test_eligible_backend_bloque_si_frontend_en_cours(self):
        w = self._watcher()
        cfg = {"stages": [
            {"id": "backend", "agent": "backend", "auto": True,
             "trigger": {"report": "fullstack.txt", "status": "TERMINÉ"}}
        ]}
        triggers = {"fullstack.txt": {"task_id": "F-1", "status": "TERMINÉ", "mtime": 100.0}}
        outputs = {}
        fs = {"frontend": "EN COURS"}
        self.assertEqual(w.eligible_transitions(cfg, triggers, {}, outputs, fs), [])


class TestArchive(unittest.TestCase):
    def _project(self, fullstack_content=None):
        import tempfile
        tmp = tempfile.mkdtemp()
        resume = Path(tmp) / ".opencode" / "resume"
        resume.mkdir(parents=True)
        history = Path(tmp) / ".opencode" / "history"
        history.mkdir(parents=True)
        if fullstack_content is not None:
            (resume / "fullstack.txt").write_text(fullstack_content, encoding="utf-8")
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        return tmp

    def test_archive_creates_entry(self):
        from archive import archive_fullstack, is_archived, archive_entries
        tmp = self._project(
            "task_id: FEATURE-012\nscope: fullstack\nstatus: TERMINÉ\n\n"
            "## Étape frontend — Ali\nagent: frontend\nstage_status: TERMINÉ\nsummary: UI faite.\n\n"
            "## Étape backend — Bamse\nagent: backend\nstage_status: TERMINÉ\nsummary: API faite.\n")
        ok, msg = archive_fullstack(tmp, "FEATURE-012",
                                    controls={"review": "OK", "security": "OK"},
                                    hadji_verdict="OK")
        self.assertTrue(ok, msg)
        self.assertTrue(is_archived(tmp, "FEATURE-012"))
        self.assertIn("FEATURE-012", archive_entries(tmp))

    def test_archive_idempotent(self):
        from archive import archive_fullstack, archive_entries
        tmp = self._project("task_id: FEATURE-012\nscope: fullstack\nstatus: TERMINÉ\n")
        archive_fullstack(tmp, "FEATURE-012", controls={"review": "OK"}, hadji_verdict="OK")
        n1 = len(archive_entries(tmp))
        archive_fullstack(tmp, "FEATURE-012", controls={"review": "OK"}, hadji_verdict="OK")
        n2 = len(archive_entries(tmp))
        self.assertEqual(n1, n2, "l'archive ne doit pas dupliquer")

    def test_archive_missing_resume(self):
        from archive import archive_fullstack
        tmp = self._project()
        ok, msg = archive_fullstack(tmp, "FEATURE-012",
                                    controls={"review": "OK"}, hadji_verdict="OK")
        self.assertFalse(ok)
        self.assertIn("absent", msg)

    def test_validate_conditions_all_ok(self):
        from archive import validate_archive_conditions
        erreurs = validate_archive_conditions(
            {"review": "OK", "security": "OK"}, "OK", ["review", "security"])
        self.assertEqual(erreurs, [], erreurs)

    def test_validate_conditions_missing_control(self):
        from archive import validate_archive_conditions
        erreurs = validate_archive_conditions(
            {"review": "OK"}, None, ["review", "security"])
        self.assertTrue(any("security" in v for v in erreurs), erreurs)

    def test_validate_conditions_hadji_not_ok(self):
        from archive import validate_archive_conditions
        erreurs = validate_archive_conditions(
            {"review": "OK", "security": "OK"}, "KO", ["review", "security"])
        self.assertTrue(any("Hadji" in v for v in erreurs), erreurs)

    def test_build_entry_structure(self):
        from archive import build_entry
        entry = build_entry("FEATURE-001",
                            "task_id: FEATURE-001\nscope: fullstack\n",
                            controls={"review": "OK"},
                            hadji_verdict="OK")
        self.assertIn("## Tâche FEATURE-001", entry)
        self.assertIn("review: OK", entry)
        self.assertIn("Verdict qualité", entry)

    def test_rapport_frontend_valide(self):
        from report import parse_report, validate_report
        rep = parse_report("task_id: FEATURE-001\nagent: frontend\nstatus: TERMINÉ\ncompleted_at: \"2026-09-26T10:00:00+04:00\"\nsummary: Interface faite.\n")
        self.assertEqual(validate_report(rep), [], validate_report(rep))

    def test_rapport_backend_valide(self):
        from report import parse_report, validate_report
        rep = parse_report("task_id: FEATURE-001\nagent: backend\nstatus: TERMINÉ\ncompleted_at: \"2026-09-26T11:00:00+04:00\"\nsummary: API faite.\n")
        self.assertEqual(validate_report(rep), [], validate_report(rep))

    def test_rapport_frontend_statut_invalide(self):
        from report import parse_report, validate_report
        rep = parse_report("task_id: FEATURE-001\nagent: frontend\nstatus: PROPOSÉ\ncompleted_at: \"2026-09-26T10:00:00+04:00\"\nsummary: Fait.\n")
        self.assertTrue(any("status" in v for v in validate_report(rep)), validate_report(rep))


class TestFullstack(unittest.TestCase):
    def _write(self, tmp, content):
        p = Path(tmp) / ".opencode" / "resume" / "fullstack.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_read_headers_and_sections(self):
        from fullstack import read_fullstack
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write(tmp, "task_id: FEATURE-012\nscope: fullstack\nstatus: EN COURS\n\n## Étape frontend — Ali\nagent: frontend\nstage_status: TERMINÉ\nsummary: UI faite.\n\n## Étape backend — Bamse\nagent: backend\nstage_status: EN COURS\nsummary: API en cours.\n")
            headers, sections = read_fullstack(p)
            self.assertEqual(headers["task_id"], "FEATURE-012")
            self.assertEqual(headers["scope"], "fullstack")
            self.assertEqual(headers["status"], "EN COURS")
            self.assertIn("agent: frontend", "\n".join(sections["frontend"]))
            self.assertIn("stage_status: TERMINÉ", "\n".join(sections["frontend"]))
            self.assertIn("agent: backend", "\n".join(sections["backend"]))
            self.assertIn("stage_status: EN COURS", "\n".join(sections["backend"]))

    def test_update_section_preserves_other(self):
        from fullstack import read_fullstack, update_section, validate_fullstack
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write(tmp, "task_id: FEATURE-012\nscope: fullstack\nstatus: EN COURS\n\n## Étape frontend — Ali\nagent: frontend\nstage_status: TERMINÉ\nsummary: UI faite.\n\n## Étape backend — Bamse\nagent: backend\nstage_status: EN COURS\nsummary: API en cours.\n")
            update_section(p, "backend", ["agent: backend", "stage_status: TERMINÉ", "summary: API faite."])
            headers, sections = read_fullstack(p)
            self.assertIn("stage_status: TERMINÉ", "\n".join(sections["backend"]))
            self.assertIn("summary: UI faite.", "\n".join(sections["frontend"]))
            self.assertEqual(validate_fullstack(headers, sections), [])

    def test_validate_missing_task_id(self):
        from fullstack import validate_fullstack
        self.assertTrue(any("task_id" in v for v in validate_fullstack({}, {})), validate_fullstack({}, {}))

    def test_validate_wrong_scope(self):
        from fullstack import validate_fullstack
        erreurs = validate_fullstack({"task_id": "F-1", "scope": "backend"}, {})
        self.assertTrue(any("scope" in v for v in erreurs), erreurs)

    def test_validate_missing_agent_in_section(self):
        from fullstack import validate_fullstack
        erreurs = validate_fullstack(
            {"task_id": "F-1", "scope": "fullstack"},
            {"frontend": ["stage_status: TERMINÉ"], "backend": ["agent: backend", "stage_status: TERMINÉ"]},
        )
        self.assertTrue(any("agent" in v and "frontend" in v for v in erreurs), erreurs)


class TestRework(unittest.TestCase):
    def test_rework_role_mapping(self):
        from rework import rework_role
        self.assertEqual(rework_role("review"), "architect")
        self.assertEqual(rework_role("security"), "build-back")
        self.assertEqual(rework_role("visual_qa"), "build-front")
        self.assertEqual(rework_role("integrated"), "architect")
        self.assertIsNone(rework_role("inconnu"))

    def test_invalidate_controls_affected(self):
        from rework import invalidate_controls
        controls = {"review": "OK", "security": "OK", "visual_qa": "Non requis"}
        modified = ["src/components/Button.tsx"]
        result = invalidate_controls(controls, modified)
        self.assertEqual(result["review"], "En attente")
        self.assertEqual(result["security"], "En attente")
        self.assertEqual(result["visual_qa"], "Non requis")

    def test_invalidate_controls_not_affected(self):
        from rework import invalidate_controls
        controls = {"review": "OK", "security": "OK"}
        modified = ["docs/readme.md"]
        result = invalidate_controls(controls, modified)
        self.assertEqual(result["review"], "OK")
        self.assertEqual(result["security"], "OK")

    def test_invalidate_controls_already_attente(self):
        from rework import invalidate_controls
        controls = {"review": "En attente", "security": "OK"}
        modified = ["src/api/users.py"]
        result = invalidate_controls(controls, modified)
        self.assertEqual(result["review"], "En attente")
        self.assertEqual(result["security"], "En attente")

    def test_controls_to_replay(self):
        from rework import controls_to_replay
        controls = {"review": "En attente", "security": "OK", "visual_qa": "En attente"}
        self.assertEqual(sorted(controls_to_replay(controls)), ["review", "visual_qa"])

    def test_is_rework_needed(self):
        from rework import is_rework_needed
        self.assertTrue(is_rework_needed({"review": "En attente"}))
        self.assertFalse(is_rework_needed({"review": "OK", "security": "OK"}))
        self.assertFalse(is_rework_needed({}))

    def test_custom_project_scopes(self):
        from rework import invalidate_controls
        controls = {"security": "OK"}
        modified = ["config/auth.ts"]
        result = invalidate_controls(controls, modified,
                                     project_scopes={"security": ("config/",)})
        self.assertEqual(result["security"], "En attente")


class TestAgentContracts(unittest.TestCase):
    def test_bloc_contrat_machine_exige(self):
        import re as _re
        for trio in sorted((ROOT / "core" / "agents").iterdir()):
            if not trio.is_dir():
                continue
            agent_yaml = (trio / "agent.yaml").read_text(encoding="utf-8")
            role = _re.search(r"^role:\s*(.+)$", agent_yaml, _re.M)
            self.assertIsNotNone(role, trio.name)
            body = (trio / "instructions.md").read_text(encoding="utf-8")
            for marker in ("task_id:", "completed_at:", "summary:"):
                self.assertIn(marker, body, f"{trio.name} : `{marker}` absent des instructions")
            self.assertIn(f"agent: {role.group(1).strip()}", body, f"{trio.name} : `agent: {role.group(1).strip()}` absent")
            contract = (trio / "contract.yaml").read_text(encoding="utf-8")
            for marker in ("- task_id", "- agent", "- completed_at", "- summary"):
                self.assertIn(marker, contract, f"{trio.name} : `{marker}` absent du contrat")


class TestAliDirective(unittest.TestCase):
    def test_fondations_et_references_exigees(self):
        body = (ROOT / "core" / "agents" / "ali" / "instructions.md").read_text(encoding="utf-8")
        for marker in ("font-family", "palette", "Playwright", "2 à 3", "project.type", "ne copie jamais",
                       "Cible fournie", "Outils indisponibles"):
            self.assertIn(marker, body, f"Ali : `{marker}` absent des instructions")
        contract = (ROOT / "core" / "agents" / "ali" / "contract.yaml").read_text(encoding="utf-8")
        for marker in ("- references citees", "- palette", "- typographie", "- tokens",
                       "- 2 a 3 references reelles citees", "aucun asset copie",
                       "- cible URL visitee via navigateur avant toute adaptation"):
            self.assertIn(marker, contract, f"Ali : `{marker}` absent du contrat")


class TestWorkflow(unittest.TestCase):
    def _load(self, name):
        from workflow import parse_workflow
        text = (ROOT / name).read_text(encoding="utf-8")
        data, err = parse_workflow(text)
        self.assertIsNone(err, err)
        return data

    def test_reference_conforme(self):
        from workflow import validate_workflow
        data = self._load("core/workflow/workflow.yaml")
        self.assertEqual(validate_workflow(data), [])

    def test_template_egal_reference(self):
        ref = self._load("core/workflow/workflow.yaml")
        tmpl = self._load("runtimes/opencode/templates/workflow.yaml")
        self.assertEqual(tmpl, ref)

    def test_invalides(self):
        from workflow import parse_workflow, validate_workflow
        base = ("workflow:\n  mode: supervised\n  auto_start: false\n  max_parallel_agents: 3\n"
                "stages:\n  - id: review\n    agent: reviewer\n    trigger:\n"
                "      report: backend.txt\n      status: TERMINÉ\n    auto: true\n")
        self.assertEqual(validate_workflow(parse_workflow(base)[0]), [])
        variants = [
            base.replace("agent: reviewer", "agent: nope"),
            base.replace("status: TERMINÉ", "status: BIDON"),
            base.replace("auto: true", "auto: true\n    condition: magie_required"),
            base.replace("- id: review", "- id: review\n  - id: review"),
            base.replace("auto_start: false", "auto_start: true"),
            base.replace("mode: supervised", "mode: autopilote"),
            base.replace("report: backend.txt", "report: inconnu.txt"),
        ]
        for i, text in enumerate(variants):
            data, err = parse_workflow(text)
            problems = [err] if err else validate_workflow(data)
            self.assertTrue(problems, f"variante {i} acceptée à tort")

    def test_coherence_stages(self):
        from workflow import parse_workflow
        data = self._load("core/workflow/workflow.yaml")
        for stage in data["stages"]:
            cand = ROOT / "core" / "workflow" / "stages" / (stage["id"] + ".yaml")
            if not cand.is_file():
                continue
            detail, err = parse_workflow(cand.read_text(encoding="utf-8"))
            self.assertIsNone(err, err)
            self.assertEqual(detail.get("agent"), stage["agent"], stage["id"])
            self.assertEqual(detail.get("condition"), stage.get("condition"), stage["id"])


class TestStateReconcile(unittest.TestCase):
    def _project(self, reports):
        import tempfile
        tmp = tempfile.mkdtemp()
        resume = Path(tmp) / ".opencode" / "resume"
        resume.mkdir(parents=True)
        for name, body in reports.items():
            (resume / name).write_text(body, encoding="utf-8")
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        return tmp

    def _report(self, tid, agent, status):
        return (f"task_id: {tid}\nagent: {agent}\nstatus: {status}\n"
                'completed_at: "2026-09-26T10:00:00+04:00"\nsummary: Fait.\n')

    def test_frais_adopte_tache(self):
        from state import reconcile
        tmp = self._project({"backend.txt": self._report("FEATURE-001", "build-back", "TERMINÉ")})
        state, notes = reconcile(tmp)
        wf = state["workflow"]
        self.assertEqual(wf["task_id"], "FEATURE-001")
        self.assertEqual(wf["status"], "IN_PROGRESS")
        self.assertIn("implementation", wf["completed_stages"])
        self.assertIn("review", wf["current_stages"])
        self.assertIn("design", wf["pending_stages"])

    def test_review_validee(self):
        from state import reconcile
        tmp = self._project({"backend.txt": self._report("FEATURE-001", "build-back", "TERMINÉ"),
                             "review.txt": self._report("FEATURE-001", "reviewer", "VALIDÉ")})
        state, _ = reconcile(tmp)
        self.assertIn("review", state["workflow"]["completed_stages"])

    def test_corrections_rouvrent_implementation(self):
        from state import reconcile
        tmp = self._project({"backend.txt": self._report("FEATURE-001", "build-back", "TERMINÉ"),
                             "review.txt": self._report("FEATURE-001", "reviewer", "CORRECTIONS NÉCESSAIRES")})
        state, notes = reconcile(tmp, "FEATURE-001")
        wf = state["workflow"]
        self.assertIn("review", wf["completed_stages"])
        self.assertNotIn("implementation", wf["completed_stages"])
        self.assertTrue(any("corrections" in n for n in notes), notes)

    def test_qa_a_completer_rouvre_implementation(self):
        from state import reconcile
        tmp = self._project({"backend.txt": self._report("FEATURE-001", "build-back", "TERMINÉ"),
                             "qa.txt": self._report("FEATURE-001", "qa", "À COMPLÉTER")})
        state, notes = reconcile(tmp, "FEATURE-001")
        wf = state["workflow"]
        self.assertNotIn("implementation", wf["completed_stages"])
        self.assertIn("implementation", wf["pending_stages"])
        self.assertTrue(any("compléter" in n for n in notes), notes)

    def test_review_sans_backend_bloquee(self):
        from state import reconcile
        tmp = self._project({"review.txt": self._report("FEATURE-001", "reviewer", "VALIDÉ")})
        state, notes = reconcile(tmp, "FEATURE-001")
        self.assertIn("review", state["workflow"]["blocked_stages"])
        self.assertTrue(any("bloquée" in n for n in notes), notes)

    def test_autre_tache_ignoree(self):
        from state import reconcile, save_state, default_state
        tmp = self._project({"backend.txt": self._report("FEATURE-002", "build-back", "TERMINÉ")})
        st = default_state("FEATURE-001")
        save_state(tmp, st)
        state, notes = reconcile(tmp)
        self.assertEqual(state["workflow"]["task_id"], "FEATURE-001")
        self.assertNotIn("implementation", state["workflow"]["completed_stages"])
        self.assertTrue(any("autre tâche" in n for n in notes), notes)

    def test_vide_rien_a_reconcilier(self):
        from state import reconcile
        tmp = self._project({})
        state, notes = reconcile(tmp)
        self.assertIsNone(state)
        self.assertTrue(notes)

    def test_validate_state(self):
        from state import validate_state
        self.assertTrue(validate_state({"workflow": {"task_id": "", "status": "BIZARRE",
                                                     "current_stages": [], "completed_stages": [],
                                                     "pending_stages": [], "blocked_stages": [],
                                                     "running_agents": []}}))


class TestWatcherEngine(unittest.TestCase):
    @classmethod
    def _watcher(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "watch_work", str(ROOT / "runtimes" / "opencode" / "scripts" / "watch-work.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_config_moteur(self):
        from pathlib import Path as _Path
        w = self._watcher()
        cfg, err = w.parse_workflow_minimal(
            _Path(str(ROOT / "runtimes" / "opencode" / "templates" / "workflow.yaml")))
        self.assertIsNone(err, err)
        usable, notes = w.select_auto_stages(cfg)
        self.assertEqual([s["id"] for s in usable],
                         ["review", "security", "visual_qa", "rework", "rework_qa"])
        self.assertEqual(notes, [])

    def test_eligibilite(self):
        w = self._watcher()
        cfg, _ = w.parse_workflow_minimal(
            __import__("pathlib").Path(str(ROOT / "core" / "workflow" / "workflow.yaml")))
        usable, _ = w.select_auto_stages(cfg)
        cfg = {"stages": usable}
        conds = {"security_required": False, "visual_qa_required": False, "design_required": False}
        ready = {"backend.txt": {"task_id": "F-1", "status": "TERMINÉ", "mtime": 100.0, "hash": "h"}}
        found = w.eligible_transitions(cfg, ready, conds, {})
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1], "F-1")
        stale = {"review.txt": {"task_id": "F-1", "mtime": 200.0}}
        self.assertEqual(w.eligible_transitions(cfg, ready, conds, stale), [])
        fresh = {"review.txt": {"task_id": "F-1", "mtime": 50.0}}
        self.assertEqual(len(w.eligible_transitions(cfg, ready, conds, fresh)), 1)
        encours = {"backend.txt": {"task_id": "F-1", "status": "EN COURS", "mtime": 100.0, "hash": "h"}}
        self.assertEqual(w.eligible_transitions(cfg, encours, conds, {}), [])

    def test_config_cassee_jamais_crash(self):
        import tempfile
        from pathlib import Path as _Path
        w = self._watcher()
        bad = _Path(tempfile.mkdtemp()) / "workflow.yaml"
        bad.write_text("stages:\n  - id: x\n    agent: nope\n    auto: true\n    trigger:\n"
                        "      report: backend.txt\n      status: TERMINÉ\n", encoding="utf-8")
        cfg, err = w.parse_workflow_minimal(bad)
        self.assertIsNone(err, err)
        usable, notes = w.select_auto_stages(cfg)
        self.assertEqual(usable, [])
        self.assertEqual(len(notes), 1)

    def test_bom_review_couverte(self):
        # Cas réel rencontré : review.txt avec BOM plus récente que backend.txt.
        import tempfile
        from pathlib import Path as _Path
        w = self._watcher()
        d = _Path(tempfile.mkdtemp())
        resume = d / ".opencode" / "resume"
        resume.mkdir(parents=True)
        (resume / "backend.txt").write_text(
            "task_id: F-1\nagent: build-back\nstatus: TERMINÉ\n"
            'completed_at: "2026-09-26T10:00:00+04:00"\nsummary: Fait.\n', encoding="utf-8")
        (resume / "review.txt").write_text(
            "\ufefftask_id: F-1\nagent: reviewer\nstatus: VALIDÉ\n"
            'completed_at: "2026-09-26T11:00:00+04:00"\nsummary: OK.\n', encoding="utf-8")
        import os as _os
        import time as _time
        now = _time.time()
        _os.utime(str(resume / "backend.txt"), (now - 100, now - 100))
        _os.utime(str(resume / "review.txt"), (now, now))
        cfg, _ = w.parse_workflow_minimal(
            _Path(str(ROOT / "core" / "workflow" / "workflow.yaml")))
        usable, _ = w.select_auto_stages(cfg)
        triggers, outputs = {}, {}
        for name in ("backend.txt", "review.txt"):
            tid, stat, mtime = w.parse_report_file(resume / name)
            self.assertEqual(tid, "F-1", name)
            triggers[name] = {"task_id": tid, "status": stat, "mtime": mtime, "hash": "h"}
            outputs[name] = {"task_id": tid, "mtime": mtime}
        conds = {"security_required": False, "visual_qa_required": False, "design_required": False}
        self.assertEqual(w.eligible_transitions({"stages": usable}, triggers, conds, outputs), [])

    def test_verrou_pid_vivant(self):
        import os as _os
        import tempfile
        import time as _time
        from pathlib import Path as _Path
        w = self._watcher()
        d = _Path(tempfile.mkdtemp())
        self.assertTrue(w.acquire_lock(d))
        self.assertEqual(w.lock_holder_pid(d), _os.getpid())
        self.assertTrue(w.holder_alive(d))
        # Verrou périmé mais détenteur vivant (long run) : jamais purgé.
        old = _time.time() - 3600
        _os.utime(str(d / ".opencode" / "state" / "watcher.lock"), (old, old))
        self.assertFalse(w.acquire_lock(d))
        self.assertEqual(w.lock_holder_pid(d), _os.getpid())
        # Verrou périmé d'un mort : purgé puis acquis.
        (d / ".opencode" / "state" / "watcher.lock").write_text(
            "2147483647 2020-01-01T00:00:00", encoding="utf-8")
        _os.utime(str(d / ".opencode" / "state" / "watcher.lock"), (old, old))
        self.assertFalse(w.holder_alive(d))
        self.assertTrue(w.acquire_lock(d))
        # Release ne supprime jamais le verrou d'autrui.
        (d / ".opencode" / "state" / "watcher.lock").write_text(
            "2147483647 2020-01-01T00:00:00", encoding="utf-8")
        w.release_lock(d)
        self.assertTrue((d / ".opencode" / "state" / "watcher.lock").is_file())

    def test_verrou_et_etat(self):
        import sys
        import tempfile
        from pathlib import Path as _Path
        w = self._watcher()
        d = _Path(tempfile.mkdtemp())
        self.assertTrue(w.acquire_lock(d))
        self.assertFalse(w.acquire_lock(d))
        w.release_lock(d)
        self.assertTrue(w.acquire_lock(d))
        w.release_lock(d)
        w.journal(d, "transition test")
        self.assertTrue((d / ".opencode" / "history" / "workflow-log.md").is_file())
        w.note_state_running(d, "F-9", "reviewer")
        w.note_state_running(d, "F-9", None)
        sys.path.insert(0, str(ROOT / "engine"))
        from state import load_state, validate_state
        st = load_state(d)
        self.assertEqual(st["workflow"]["task_id"], "F-9")
        self.assertEqual(validate_state(st), [])
        self.assertEqual(st["workflow"]["running_agents"], [])


class TestConditions(unittest.TestCase):
    def _cfg(self):
        from workflow import parse_workflow
        data, err = parse_workflow(
            (ROOT / "core" / "workflow" / "workflow.yaml").read_text(encoding="utf-8"))
        self.assertIsNone(err, err)
        return data

    def _watcher(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "watch_work", str(ROOT / "runtimes" / "opencode" / "scripts" / "watch-work.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_seuls_requis_tournent(self):
        w = self._watcher()
        data = self._cfg()
        usable, _ = w.select_auto_stages(data)
        self.assertEqual([s["id"] for s in usable], ["review", "security", "visual_qa", "rework", "rework_qa"])
        cfg = {"stages": usable}
        build = {"backend.txt": {"task_id": "F-1", "status": "TERMINÉ", "mtime": 100.0, "hash": "h"}}
        off = {"security_required": False, "visual_qa_required": False, "design_required": False}
        self.assertEqual([s["id"] for s, _, _ in w.eligible_transitions(cfg, build, off, {})],
                         ["review"])
        on = {"security_required": True, "visual_qa_required": True, "design_required": False}
        got = sorted(s["id"] for s, _, _ in w.eligible_transitions(cfg, build, on, {}))
        self.assertEqual(got, ["review", "security", "visual_qa"])

    def test_rework_corrections(self):
        w = self._watcher()
        data = self._cfg()
        usable, _ = w.select_auto_stages(data)
        cfg = {"stages": usable}
        off = {"security_required": False, "visual_qa_required": False, "design_required": False}
        trig = {"review.txt": {"task_id": "F-1", "status": "CORRECTIONS NÉCESSAIRES",
                               "mtime": 200.0, "hash": "h"}}
        outs = {"backend.txt": {"task_id": "F-1", "mtime": 100.0}}
        got = [s["id"] for s, _, _ in w.eligible_transitions(cfg, trig, off, outs)]
        self.assertEqual(got, ["rework"])
        # Bamse a déjà re-livré : on ne le rappelle pas, Hadji reprend.
        outs_frais = {"backend.txt": {"task_id": "F-1", "mtime": 300.0}}
        trig2 = {"backend.txt": {"task_id": "F-1", "status": "TERMINÉ", "mtime": 300.0, "hash": "h2"},
                 "review.txt": {"task_id": "F-1", "status": "CORRECTIONS NÉCESSAIRES",
                                "mtime": 200.0, "hash": "h"}}
        got2 = sorted(s["id"] for s, _, _ in w.eligible_transitions(cfg, trig2, off, outs_frais))
        self.assertEqual(got2, ["review"])

    def test_rework_qa_a_completer(self):
        # FEATURE-007 : qa.txt À COMPLÉTER rappelle Bamse (action env
        # attendue : purge cache, relance frontal), sauf build plus récent.
        w = self._watcher()
        data = self._cfg()
        usable, _ = w.select_auto_stages(data)
        cfg = {"stages": usable}
        off = {"security_required": False, "visual_qa_required": False, "design_required": False}
        trig = {"qa.txt": {"task_id": "F-1", "status": "À COMPLÉTER",
                           "mtime": 200.0, "hash": "h"}}
        outs = {"backend.txt": {"task_id": "F-1", "mtime": 100.0}}
        got = [s["id"] for s, _, _ in w.eligible_transitions(cfg, trig, off, outs)]
        self.assertEqual(got, ["rework_qa"])
        # Bamse a déjà re-livré : on ne le rappelle pas.
        outs_frais = {"backend.txt": {"task_id": "F-1", "mtime": 300.0}}
        trig2 = {"backend.txt": {"task_id": "F-1", "status": "TERMINÉ", "mtime": 300.0, "hash": "h2"},
                 "qa.txt": {"task_id": "F-1", "status": "À COMPLÉTER",
                            "mtime": 200.0, "hash": "h"}}
        got2 = sorted(s["id"] for s, _, _ in w.eligible_transitions(cfg, trig2, off, outs_frais))
        self.assertEqual(got2, ["review"])

    def test_summarize_agents(self):
        from workflow import summarize_agents
        rows = dict((sid, state) for sid, _, state in summarize_agents(
            self._cfg(), {"security_required": True, "visual_qa_required": False,
                          "design_required": False}))
        self.assertEqual(rows["review"], "requis")
        self.assertEqual(rows["security"], "requis")
        self.assertTrue(rows["visual_qa"].startswith("inactif"))
        self.assertEqual(rows["buildback"], "manuel")
        self.assertEqual(rows["buildfront"], "manuel")


class TestParallel(unittest.TestCase):
    def _stages(self):
        return [{"id": "review", "agent": "reviewer"},
                {"id": "security", "agent": "security"},
                {"id": "visual_qa", "agent": "qa"}]

    def test_parallelisme_reel(self):
        import importlib.util
        import tempfile
        import threading
        import time as _time
        from pathlib import Path as _Path
        spec = importlib.util.spec_from_file_location(
            "watch_work_p", str(ROOT / "runtimes" / "opencode" / "scripts" / "watch-work.py"))
        w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(w)
        root = _Path(tempfile.mkdtemp())
        lock = threading.Lock()
        current = {"n": 0, "max": 0}

        def stub(_root, stage, _task):
            with lock:
                current["n"] += 1
                current["max"] = max(current["max"], current["n"])
            _time.sleep(0.3)
            with lock:
                current["n"] -= 1
            return True, "ok"

        w.trigger_stage = stub
        items = [(f"k{i}", s, "T", f"{s['id']} prêt (T)") for i, s in enumerate(self._stages())]
        start = _time.time()
        ok, done, failed = w.run_branches(root, items, 3)
        elapsed = _time.time() - start
        self.assertTrue(ok)
        self.assertEqual(len(done), 3)
        self.assertEqual(failed, [])
        self.assertEqual(current["max"], 3)
        self.assertLess(elapsed, 0.9)

    def test_plafond_et_echec_isole(self):
        import importlib.util
        import tempfile
        import threading
        import time as _time
        from pathlib import Path as _Path
        spec = importlib.util.spec_from_file_location(
            "watch_work_q", str(ROOT / "runtimes" / "opencode" / "scripts" / "watch-work.py"))
        w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(w)
        root = _Path(tempfile.mkdtemp())
        lock = threading.Lock()
        current = {"n": 0, "max": 0}

        def stub(_root, stage, _task):
            with lock:
                current["n"] += 1
                current["max"] = max(current["max"], current["n"])
            _time.sleep(0.1)
            with lock:
                current["n"] -= 1
            if stage["id"] != "security":
                return True, "ok"
            return False, "other"

        w.trigger_stage = stub
        items = [(f"k{i}", s, "T", f"{s['id']} prêt (T)") for i, s in enumerate(self._stages())]
        ok, done, failed = w.run_branches(root, items, 1)
        self.assertEqual(current["max"], 1)
        self.assertTrue(ok)
        self.assertEqual(len(done), 2)
        self.assertEqual(len(failed), 1)

    def test_etat_add_remove(self):
        import importlib.util
        import sys
        import tempfile
        from pathlib import Path as _Path
        spec = importlib.util.spec_from_file_location(
            "watch_work_r", str(ROOT / "runtimes" / "opencode" / "scripts" / "watch-work.py"))
        w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(w)
        root = _Path(tempfile.mkdtemp())
        w.state_agents_add(root, "F-1", "reviewer")
        w.state_agents_add(root, "F-1", "reviewer")
        w.state_agents_add(root, "F-1", "security")
        sys.path.insert(0, str(ROOT / "engine"))
        from state import load_state
        running = load_state(root)["workflow"]["running_agents"]
        self.assertEqual(sorted(running), ["reviewer", "security"])
        w.state_agents_remove(root, "F-1", "reviewer")
        w.state_agents_remove(root, "F-1", "reviewer")
        running = load_state(root)["workflow"]["running_agents"]
        self.assertEqual(running, ["security"])

    def test_max_parallel_garde_fou(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "watch_work_s", str(ROOT / "runtimes" / "opencode" / "scripts" / "watch-work.py"))
        w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(w)
        self.assertEqual(w.max_parallel_agents({"workflow": {"max_parallel_agents": 2}}), 2)
        self.assertEqual(w.max_parallel_agents({"workflow": {"max_parallel_agents": 0}}), 3)
        self.assertEqual(w.max_parallel_agents({"workflow": {"max_parallel_agents": True}}), 3)
        self.assertEqual(w.max_parallel_agents({}), 3)
        self.assertEqual(w.max_parallel_agents(None), 3)

    def test_classification_echecs(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "watch_work_c", str(ROOT / "runtimes" / "opencode" / "scripts" / "watch-work.py"))
        w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(w)

        class R:
            stdout = ""
            stderr = ""

        r = R()
        r.stderr = "Error from provider: Rate limit exceeded. Please try again later."
        self.assertEqual(w.classify_failure(r), "quota")
        r.stderr = "429 Too Many Requests: insufficient_quota"
        self.assertEqual(w.classify_failure(r), "quota")
        r.stderr = ""
        r.stdout = "Connection reset by peer, network unreachable"
        self.assertEqual(w.classify_failure(r), "network")
        r.stdout, r.stderr = "", "timed out while waiting for the model"
        self.assertEqual(w.classify_failure(r), "network")
        r.stdout, r.stderr = "Unknown error from agent", ""
        self.assertEqual(w.classify_failure(r), "other")

    def test_quota_circuit_breaker(self):
        import importlib.util
        import tempfile
        from pathlib import Path as _Path
        spec = importlib.util.spec_from_file_location(
            "watch_work_q", str(ROOT / "runtimes" / "opencode" / "scripts" / "watch-work.py"))
        w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(w)
        with tempfile.TemporaryDirectory() as tmp:
            root = _Path(tmp)
            (root / ".opencode" / "resume").mkdir(parents=True)
            (root / ".opencode" / "history").mkdir(parents=True)
            (root / ".opencode" / "state").mkdir(parents=True)
            stage = {"id": "review", "agent": "reviewer",
                     "trigger": {"report": "backend.txt", "status": "TERMINÉ"}}
            real_trigger = w.trigger_stage
            w.trigger_stage = lambda *a, **k: (False, "quota")
            try:
                ok, done, failed = w.run_branches(root, [("k1", stage, "F-1", "test")], 1)
            finally:
                w.trigger_stage = real_trigger
            self.assertFalse(ok)
            self.assertEqual(failed[0][2], "quota")
            log = (root / ".opencode" / "history" / "workflow-log.md").read_text(encoding="utf-8")
            self.assertIn("QUOTA", log)


class TestServices(unittest.TestCase):
    @classmethod
    def _services(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "services_mod", str(ROOT / "runtimes" / "opencode" / "scripts" / "services.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _free_port():
        import socket
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def test_cycle_complet(self):
        import sys
        import tempfile
        from pathlib import Path as _Path
        svc = self._services()
        tmp = _Path(tempfile.mkdtemp())
        (_opencode := tmp / ".opencode").mkdir()
        (_opencode / "sayrhazi.yaml").write_text("workflow:\n  name: Sayrhazi\n", encoding="utf-8")
        port = self._free_port()
        url = f"http://localhost:{port}/"
        ok, message = svc.start_service(tmp, "web", f"{sys.executable} -m http.server {port}",
                                        ".", port, url, wait=15)
        self.assertTrue(ok, message)
        rows = svc.service_status(tmp, "web")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["alive"])
        self.assertTrue(rows[0]["port_busy"])
        text, err = svc.tail_logs(tmp, "web")
        self.assertEqual(err, "")
        self.assertTrue(text.strip())
        ok, message = svc.stop_service(tmp, "web")
        self.assertTrue(ok, message)
        self.assertFalse(svc.port_busy(port))
        self.assertFalse((tmp / ".opencode" / "state" / "services-web.pid").is_file())

    def test_port_occupe_refuse(self):
        import socket
        import tempfile
        from pathlib import Path as _Path
        svc = self._services()
        tmp = _Path(tempfile.mkdtemp())
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        try:
            ok, message = svc.start_service(tmp, "web", "dummy", ".", port, None, wait=1)
            self.assertFalse(ok)
            self.assertIn("occupé", message)
        finally:
            sock.close()

    def test_pidfile_perime(self):
        import tempfile
        from pathlib import Path as _Path
        svc = self._services()
        tmp = _Path(tempfile.mkdtemp())
        state = tmp / ".opencode" / "state"
        state.mkdir(parents=True)
        import json
        (state / "services-web.pid").write_text(
            json.dumps({"pid": 2147483647, "port": None}), encoding="utf-8")
        rows = svc.service_status(tmp, "web")
        self.assertFalse(rows[0]["alive"])
        ok, _ = svc.stop_service(tmp, "web")
        self.assertTrue(ok)
        self.assertFalse((state / "services-web.pid").is_file())

    def test_regle_serveurs_instructions(self):
        for agent in ("bamse", "zawadi"):
            body = (ROOT / "core" / "agents" / agent / "instructions.md").read_text(encoding="utf-8")
            self.assertIn("ne tue jamais un processus", body, agent)


class TestState(unittest.TestCase):
    def test_roundtrip(self):
        from state import default_state, load_state, save_state
        with tempfile.TemporaryDirectory() as tmp:
            st = default_state("FEATURE-001")
            st["workflow"]["pending_stages"] = ["review", "visual_qa"]
            save_state(tmp, st)
            back = load_state(tmp)
            self.assertEqual(back["workflow"]["task_id"], "FEATURE-001")
            self.assertEqual(back["workflow"]["pending_stages"], ["review", "visual_qa"])

    def test_absent(self):
        from state import load_state
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_state(tmp))


class TestSession(unittest.TestCase):
    """Appels sans reprise de session : le watcher ne fait pas le travail
    d'OpenCode. On verrouille les invariants qui remplacent l'ancien suivi."""

    @classmethod
    def _watcher(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "watch_work", str(ROOT / "runtimes" / "opencode" / "scripts" / "watch-work.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @classmethod
    def _tui(cls, name="watch_tui"):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            name, str(ROOT / "runtimes" / "opencode" / "scripts" / "watch-tui.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_aucune_reprise_de_session(self):
        # Plus de --session : chaque appel ouvre une session neuve et laisse
        # OpenCode gerer contexte, cache et compaction.
        w = self._watcher()
        cmd = w.build_command("hadji", "prompt",
                              "Sayrhazi F-1 hadji 20261015-120000-1a2b", "opencode/m")
        self.assertNotIn("--session", cmd)
        self.assertIn("--title", cmd)
        self.assertEqual(cmd[:4], ["opencode", "run", "--agent", "hadji"])
        self.assertEqual(cmd[-1], "prompt")

    def test_run_id_unique_et_triable(self):
        w = self._watcher()
        premier = w.build_run_id(1792000000.0)
        second = w.build_run_id(1792000060.0)
        self.assertLess(premier, second)
        self.assertTrue(w.RUN_ID_RE.match(premier), premier)
        self.assertTrue(w.RUN_ID_RE.match(second), second)
        self.assertNotEqual(premier, second)

    def test_titre_session_stable(self):
        w = self._watcher()
        titre = w.session_title("FEATURE-007", "hadji", "20261015-120000-1a2b")
        self.assertEqual(titre, "Sayrhazi FEATURE-007 hadji 20261015-120000-1a2b")
        self.assertTrue(titre.startswith("Sayrhazi FEATURE-007 hadji "))

    def test_etat_sans_bloc_session(self):
        import tempfile
        from pathlib import Path as _Path
        w = self._watcher()
        with tempfile.TemporaryDirectory() as tmp:
            root = _Path(tmp)
            (root / ".opencode" / "state").mkdir(parents=True)
            w.write_state_minimal(root, {"task_id": "F-1", "status": "IN_PROGRESS"})
            texte = (root / ".opencode" / "state" / "workflow-state.yaml").read_text(encoding="utf-8")
            self.assertNotIn("session:", texte)
            self.assertEqual(w.read_state_minimal(root)["task_id"], "F-1")

    def test_dashboard_formule_native(self):
        # Reproduit le panneau Context d'OpenCode : cache.read compte.
        t = self._tui()
        payload = {"role": "assistant", "providerID": "opencode", "modelID": "muse",
                   "tokens": {"input": 394, "output": 141, "reasoning": 50,
                              "cache": {"read": 47217, "write": 0}}}
        self.assertEqual(t.message_context_tokens(payload), 47802)
        self.assertEqual(t.message_model_key(payload), "opencode/muse")

    def test_dashboard_registre_modeles(self):
        t = self._tui()
        limites = t.load_model_limits(ROOT)
        self.assertTrue(limites, "registre core/runtime/models.yaml vide ou introuvable")
        attendus = {
            "opencode/longcat-2.5-preview-free": 1000000,
            "opencode/muse-spark-1.3-contributor-free": 1048576,
            "opencode/nemotron-3-ultra-free": 1000000,
            "opencode/nemotron-3.5-super-vl-free": 1048576,
            "opencode/space-bunny-free": 1048576,
            "opencode/step-5-preview-free": 1000000,
        }
        for cle, valeur in attendus.items():
            self.assertEqual(limites.get(cle), valeur, cle)
        # Modele absent du registre : pas de limite inventee.
        self.assertIsNone(limites.get("inconnu/inexistant"))

    def test_dashboard_aucun_repli_silencieux(self):
        # Base absente : aucune session, ni auto ni manuelle.
        t = self._tui()
        inexistant = "chemin/inexistant.db"
        self.assertEqual(t.find_sessions(inexistant, "/tmp", "hadji",
                                         "Sayrhazi F-1 hadji "), [])

    def test_dashboard_voit_les_agents_manuels(self):
        # Un agent appelle a la main (titre libre) doit apparaitre aussi :
        # seuls les appels du watcher portent le prefixe Sayrhazi.
        t = self._tui()
        db, path = self._db()
        self._session(db, "ses_manual", "Ali : maquette du tableau", "ali",
                      "/proj", 1000)
        self._session(db, "ses_auto", "Sayrhazi F-1 ali 20261015-120000-1a2b",
                      "ali", "/proj", 2000)
        db.commit()
        db.close()
        trouvees = t.find_sessions(str(path), "/proj", "ali",
                                   "Sayrhazi F-1 ali ")
        self.assertEqual(len(trouvees), 2, "session manuelle ignoree")
        auto = t.latest_auto_session(trouvees)
        manuel = t.latest_manual_session(trouvees)
        self.assertEqual(auto["id"], "ses_auto")
        self.assertEqual(manuel["id"], "ses_manual")
        # La plus active des deux est celle affichee.
        courante = max((auto, manuel),
                        key=lambda s: s.get("time_updated") or s["time_created"])
        self.assertEqual(courante["id"], "ses_auto")

    def test_dashboard_session_par_activite_pas_par_creation(self):
        # Regression : comparer sur time_created affichait la mauvaise session.
        # Une session manuelle creee avant une session automatique peut rester
        # celle que l'utilisateur regarde (terminus ouvert) ; c'est la derniere
        # ACTIVITE qui fait foi, pas la date de creation.
        t = self._tui()
        db, path = self._db()
        self._session(db, "ses_manuelle", "Ali : travail en cours", "ali",
                      "/proj", 1000, updated=9000)
        self._session(db, "ses_auto", "Sayrhazi F-1 ali run-1", "ali",
                      "/proj", 2000, updated=3000)
        db.commit()
        db.close()
        trouvees = t.find_sessions(str(path), "/proj", "ali", "Sayrhazi F-1 ali ")
        courante = max((s for s in trouvees), key=lambda s: s["time_updated"])
        self.assertEqual(courante["id"], "ses_manuelle",
                         "session la plus active selectionnee par time_updated")

    def test_dashboard_barre_sans_limite(self):
        t = self._tui()
        self.assertEqual(set(t.bar(None)), {"·"})
        self.assertTrue(t.bar(0.5).startswith("█"))

    def test_dashboard_une_valeur_par_colonne(self):
        # Regression : la jauge et le nombre de tokens etaient concatenes
        # dans une seule colonne, si bien que l'en-tete compte une case de
        # moins que la ligne et que tout le tableau se decale.
        t = self._tui()

        class _R:
            agent_file, role = "hadji", "reviewer"
            usage, context_tokens, usage_pct = 0.04, 45215, "4 %"
            calls, manual_calls = 2, 7
            cost_session, cost_projet = "$0.01", "$0.42"
            sid = "ebe4cb9c9f"
            origine, report_txt, etat = "auto", "VALIDÉ", "VALIDÉ"

        cellules = t.row_cells(_R())
        self.assertEqual(len(cellules), len(t.COLUMNS),
                         f"{len(cellules)} cellules pour {len(t.COLUMNS)} colonnes")
        # La jauge seule dans sa colonne, les tokens seuls dans la leur.
        jauge = t.COLUMNS.index("Jauge")
        self.assertEqual(len(cellules[jauge]), 10)
        self.assertTrue(set(cellules[jauge]) <= {"█", "░", "·"})
        self.assertEqual(cellules[t.COLUMNS.index("Tokens")], "45 215")

    def test_dashboard_separe_session_et_projet(self):
        # Les deux blocs ne doivent jamais laisser croire qu'ils mesurent la
        # meme chose : `Coût sess.` est celui de la session affichee,
        # `Coût proj.` cumule toutes les sessions de l'agent sur le projet.
        t = self._tui()

        class _R:
            agent_file, role = "hifadhui", "security"
            usage, context_tokens, usage_pct = 0.05, 55467, "5 %"
            calls, manual_calls = 1, 5
            cost_session, cost_projet = "$0.03", "$0.99"
            sid = "ebddd8569f"
            origine, report_txt, etat = "auto", "VALIDÉ", "VALIDÉ AVEC RÉSERVES"

        cellules = t.row_cells(_R())
        sess = cellules[t.COLUMNS.index("Coût sess.")]
        proj = cellules[t.COLUMNS.index("Coût proj.")]
        self.assertEqual(sess, "$0.03", "le cout de session affiche l'historique")
        self.assertEqual(proj, "$0.99")
        self.assertNotEqual(sess, proj)
        # Les deux blocs sont separes par une colonne qui ne porte aucune valeur.
        seps = [i for i, nom in enumerate(t.COLUMNS) if nom == t.SEP]
        self.assertTrue(seps, "aucune separation de blocs")
        for i in seps:
            self.assertEqual(cellules[i], f"[dim]{t.SEP}[/]",
                             "la colonne de separation porte une valeur")

    def test_dashboard_detail_decoupe_le_contexte(self):
        # Le detail doit decomposer le contexte comme OpenCode l'a compte,
        # parce que `cache.read` domine et qu'une jauge seule ne dit rien.
        t = self._tui()
        payload = {"role": "assistant", "finish": "stop", "providerID": "p",
                   "modelID": "m",
                   "tokens": {"input": 715, "output": 420, "reasoning": 0,
                              "cache": {"read": 570993, "write": 0}}}
        part = t.message_token_split(payload)
        self.assertEqual(part, {"input": 715, "cache.read": 570993,
                                "cache.write": 0, "output": 420, "reasoning": 0})
        self.assertEqual(sum(part.values()), t.message_context_tokens(payload),
                         "le detail ne recompte pas la formule")

    def test_dashboard_detail_n_invente_rien(self):
        # Agent jamais appele : le detail doit dire n/d, pas fabriquer un
        # contexte, une duree ou un cout. On instancie le vrai AgentRow avec
        # une base absente : c'est le chemin reel d'un agent sans session.
        t = self._tui()
        import tempfile as _tf
        from pathlib import Path as _P
        racine = _P(_tf.mkdtemp())
        ligne = t.AgentRow("qa", {}, racine / "resume", "", project_root=None,
                            model_limits={})
        lignes = ligne.detail_lines()
        self.assertTrue(lignes)
        self.assertTrue(any("n/d" in l for l in lignes),
                        f"aucune mention n/d dans {lignes}")
        self.assertFalse(any("%" in l for l in lignes),
                         "un pourcentage a ete invente sans mesure")
        self.assertIsNone(ligne.context_tokens)
        self.assertIsNone(ligne.cost_session)

    def test_dashboard_detail_agence_token_absente(self):
        # `tokens` present mais vide ou aberrant : jamais de division par zero
        # ni de valeur negative.
        t = self._tui()
        for tokens in ({}, {"input": None}, {"cache": None}, {"input": "beaucoup"}):
            with self.subTest(tokens=tokens):
                part = t.message_token_split({"tokens": tokens})
                self.assertTrue(all(v >= 0 for v in part.values()))

    def test_dashboard_selection_preservee_au_rafraichissement(self):
        # Regression : `table.clear()` renvoyait le curseur sur la premiere
        # ligne a chaque tick, donc le clic etait perdu en une seconde.
        t = self._tui()
        self.assertIn("build-back", t.ROLE_ORDER)
        # La restauration doit se baser sur l'identite du role, pas sur
        # l'index : l'index depend de l'ordre courant des lignes.
        self.assertGreater(t.ROLE_ORDER.index("reviewer"),
                           t.ROLE_ORDER.index("build-back"))

    def test_dashboard_horodatages_en_millisecondes(self):
        # Regression : OpenCode stocke time_created/time_updated en
        # millisecondes. Passes tels quels a format_age, le delta devenait
        # negatif, donc le detail affichait « creee 0s · active 0s » partout.
        t = self._tui()

        class _R:
            session_row = {"time_created": 1_000_000_000_000,
                           "time_updated": 1_000_000_000_000 + 90_000}

        act = t.session_activity(_R())
        self.assertAlmostEqual(act["created"], 1_000_000_000.0, places=1)
        self.assertEqual(act["duree"], 90.0)
        self.assertEqual(t.format_span(act["duree"]), "1m30s")

    def test_dashboard_etat_du_detail_non_reinitialise(self):
        # Regression : la valeur par defaut « — » etait reappliquee apres le
        # calcul, si bien que le detail affichait « — » pour tout agent alors
        # que la colonne Etat montrait bien « attente ».
        t = self._tui()
        import tempfile as _tf
        from pathlib import Path as _P
        racine = _P(_tf.mkdtemp())
        resume = racine / "resume"
        resume.mkdir(parents=True)
        (resume / "backend.txt").write_text(
            "task_id: FEATURE-007\nstatus: TERMINÉ\n", encoding="utf-8")
        ligne = t.AgentRow("build-back", {}, resume, "FEATURE-007",
                           project_root=None, model_limits={})
        # task_id identique au rapport -> l'etat doit etre celui du rapport.
        self.assertEqual(ligne.etat_plain, "TERMINÉ")
        autre = t.AgentRow("build-back", {}, resume, "FEATURE-006",
                           project_root=None, model_limits={})
        self.assertEqual(autre.etat_plain, "attente")

    def test_dashboard_format_span_au_dela_des_heures(self):
        t = self._tui()
        self.assertEqual(t.format_span(45), "45s")
        self.assertEqual(t.format_span(90), "1m30s")
        self.assertEqual(t.format_span(3600), "1h00m")
        self.assertEqual(t.format_span(2 * 86400 + 3 * 3600 + 14 * 60), "2j 03h")
        self.assertEqual(t.format_span(-5), "0s")

    def test_dashboard_sortie_texte_sans_balise(self):
        # La sortie `--once` est du texte brut : le balisage Textual qui y
        # fuirait rendrait la table illisible et fausserait l'alignement des
        # colonnes (les balises comptent dans la largeur).
        t = self._tui()
        for brut, attendu in (("[dim]n/d[/]", "n/d"),
                              ("[/]", ""),
                              ("[dim]│[/]", "│"),
                              ("[yellow]manuel[/]", "manuel"),
                              ("watcher", "watcher")):
            with self.subTest(balise=brut):
                self.assertEqual(t.plain(brut), attendu)


# --- Tests de robustesse 0.13.0 (sessions jetables, rapport = mémoire) ---

    def _db(self):
        """Base SQLite temporaire au schéma OpenCode (session + message)."""
        import sqlite3
        import tempfile
        from pathlib import Path as _Path
        path = _Path(tempfile.mkdtemp()) / "opencode.db"
        self.addCleanup(__import__("shutil").rmtree, path.parent, True)
        db = sqlite3.connect(str(path))
        db.execute("CREATE TABLE session (id TEXT, title TEXT, agent TEXT,"
                   " directory TEXT, cost REAL, time_created INTEGER,"
                   " time_updated INTEGER)")
        db.execute("CREATE TABLE message (id TEXT, session_id TEXT,"
                   " time_created INTEGER, data TEXT)")
        db.commit()
        return db, path

    def _session(self, db, sid, title, agent, directory, created, cost=0.0,
                 updated=None):
        db.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?)",
                   (sid, title, agent, directory, cost, created,
                    created if updated is None else updated))

    def _message(self, db, sid, created, payload):
        import json
        db.execute("INSERT INTO message VALUES (?,?,?,?)",
                   (f"msg{sid}{created}", sid, created, json.dumps(payload)))

    def test_deux_appels_simultanes_meme_agent(self):
        # Deux appels du meme agent dans la meme seconde : titres distincts,
        # sessions distinctes, aucune confusion d'identite.
        import importlib.util
        import threading
        spec = importlib.util.spec_from_file_location(
            "watch_work_c", str(ROOT / "runtimes" / "opencode" / "scripts" / "watch-work.py"))
        w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(w)
        ids = []
        lock = threading.Lock()

        def worker():
            for _ in range(60):
                run_id = w.build_run_id(1792000000.0)   # meme seconde
                titre = w.session_title("F-1", "hadji", run_id)
                with lock:
                    ids.append(titre)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(ids), 360)
        self.assertEqual(len(set(ids)), 360, "titres identiques : collision de run_id")

    def test_base_inaccessible_etat_explicite(self):
        # Base absente / illisible : aucune session etrangere selectionnee.
        t = self._tui()
        vide = t.find_sessions("absent.db", "/projet", "hadji", "Sayrhazi F-1 hadji ")
        self.assertEqual(vide, [])
        self.assertIsNone(t.last_assistant_message("absent.db", "ses_xyz"))

    def test_message_sans_mesure_ne_remet_pas_le_contexte_a_zero(self):
        # Deux formes relevees dans la base d'OpenCode pour un message qui ne
        # mesure rien : le message ouvert au debut d'un run (finish absent) et
        # un tour interrompu (finish "unknown", chaine non vide donc truthy).
        # Aucune des deux ne doit faire retomber la jauge a 0.
        t = self._tui()
        for finish in (None, "unknown"):
            with self.subTest(finish=finish):
                db, path = self._db()
                self._session(db, "ses_x", "Sayrhazi F-1 hadji run-1",
                              "hadji", "/proj", 1000)
                termine = {"role": "assistant", "finish": "stop", "providerID": "p",
                           "modelID": "m", "tokens": {"input": 10, "output": 5,
                                                      "reasoning": 0,
                                                      "cache": {"read": 47217, "write": 0}}}
                vide = {"role": "assistant", "finish": finish, "providerID": "p",
                        "modelID": "m", "tokens": {"input": 0, "output": 0,
                                                   "reasoning": 0,
                                                   "cache": {"read": 0, "write": 0}}}
                self._message(db, "ses_x", 2000, termine)
                self._message(db, "ses_x", 3000, vide)
                db.commit()
                db.close()
                payload = t.last_assistant_message(str(path), "ses_x")
                self.assertEqual(t.message_context_tokens(payload), 47232,
                                 "contexte remis a zero par un message sans mesure")

    def test_compaction_non_concernee_par_le_contexte(self):
        # OpenCode marque une compaction par un part `compaction` attache a un
        # message USER, jamais a un message assistant. Le dashboard ne doit
        # donc rien changer : il se contente de lire la baisse de contexte du
        # message assistant suivant.
        t = self._tui()
        db, path = self._db()
        self._session(db, "ses_c", "Travail a faire", "build", "/proj", 1000)
        avant = {"role": "assistant", "finish": "stop", "providerID": "p",
                 "modelID": "m", "tokens": {"input": 505540, "output": 100,
                                            "reasoning": 0,
                                            "cache": {"read": 0, "write": 0}}}
        apres = {"role": "assistant", "finish": "stop", "providerID": "p",
                 "modelID": "m", "tokens": {"input": 16176, "output": 50,
                                            "reasoning": 0,
                                            "cache": {"read": 0, "write": 0}}}
        self._message(db, "ses_c", 2000, avant)
        self._message(db, "ses_c", 3000, {"role": "user", "parts": [
            {"type": "compaction", "auto": True, "overflow": True}]})
        self._message(db, "ses_c", 4000, apres)
        db.commit()
        db.close()
        payload = t.last_assistant_message(str(path), "ses_c")
        # Apres compaction, la jauge suit la nouvelle valeur : pas de gel sur
        # l'ancienne, pas de retour a zero.
        self.assertEqual(t.message_context_tokens(payload), 16226)

    def test_message_sans_mesure_utilise_si_aucune_autre_mesure(self):
        # Aucune session n'a encore de tour mesurant : le message restant,
        # meme partiel, reste affiche plutot qu'une ligne vide.
        t = self._tui()
        db, path = self._db()
        self._session(db, "ses_y", "Sayrhazi F-1 ali run-1", "ali", "/proj", 1000)
        partiel = {"role": "assistant", "finish": "tool-calls", "providerID": "p",
                   "modelID": "m", "tokens": {"input": 0, "output": 7,
                                              "reasoning": 0,
                                              "cache": {"read": 0, "write": 0}}}
        self._message(db, "ses_y", 2000, partiel)
        db.commit()
        db.close()
        payload = t.last_assistant_message(str(path), "ses_y")
        self.assertEqual(t.message_context_tokens(payload), 7)

    def test_session_sans_mesure_aucune_il_existe(self):
        # Seuls des messages a zero : aucune mesure. On ne fabrique rien.
        t = self._tui()
        db, path = self._db()
        self._session(db, "ses_z", "Sayrhazi F-1 zawadi run-1", "zawadi",
                      "/proj", 1000)
        self._message(db, "ses_z", 2000, {"role": "assistant", "finish": "unknown",
                                          "tokens": {"input": 0, "output": 0,
                                                     "reasoning": 0,
                                                     "cache": {"read": 0, "write": 0}}})
        db.commit()
        db.close()
        self.assertIsNone(t.last_assistant_message(str(path), "ses_z"))

    def test_modele_absent_registre_pas_de_pourcentage(self):
        # Modele inconnu : n/d. Pas de pourcentage invente, tokens presents.
        t = self._tui()
        limites = {}
        payload = {"role": "assistant", "providerID": "x", "modelID": "inconnu",
                   "tokens": {"input": 10, "output": 5, "reasoning": 0,
                              "cache": {"read": 0, "write": 0}}}
        contexte = t.message_context_tokens(payload)
        self.assertEqual(contexte, 15)
        self.assertNotIn("x/inconnu", limites)      # inconnu -> pas de limite
        self.assertIsNone(limites.get("x/inconnu"))  # pas de repli par defaut

    def test_cache_dominant_conforme_panneau_opencode(self):
        # Cache.read enorme : il compte dans le contexte (c'etait le bug du
        # seuil 0.12.x, qui l'excluait).
        t = self._tui()
        payload = {"role": "assistant", "providerID": "opencode", "modelID": "m",
                   "tokens": {"input": 394, "output": 141, "reasoning": 50,
                              "cache": {"read": 47217, "write": 0}}}
        contexte = t.message_context_tokens(payload)
        self.assertEqual(contexte, 47802)
        limite = 1048576
        attendu = round(contexte / limite * 100)
        self.assertEqual(attendu, 5)   # 4.56 % arrondi

    def test_mise_a_jour_ancienne_installation_avec_session(self):
        # Une config ancienne (bloc session:) reste valide et inerte.
        import sys as _sys
        import json as _json
        _sys.path.insert(0, str(ROOT / "engine"))
        import sayrhazi_config as sc
        schema = _json.loads(
            (ROOT / "core" / "schemas" / "config.schema.json").read_text(encoding="utf-8"))
        base = sc.parse_simple_yaml(
            (ROOT / "runtimes" / "opencode" / "templates" / "sayrhazi.yaml")
            .read_text(encoding="utf-8"))[0]
        base = dict(base)
        base["session"] = {"max_usage": 0.95, "default_limit": 1048576}
        problemes = sc.validate_against_schema(base, schema)
        # Seul le A_COMPLETER attendu du template reste.
        self.assertTrue(all("session" not in p for p in problemes), problemes)

    def test_reprise_apres_arret_brutal_via_rapports(self):
        # Regression centrale : la session est jetable, le travail ne l'est pas.
        # Apres un arret brutal, le watcher repart des RAPPORTS, jamais d'une
        # session precedente : un rapport produit suffit a finir la transition.
        import importlib.util
        import tempfile
        from pathlib import Path as _Path
        spec = importlib.util.spec_from_file_location(
            "watch_work_r", str(ROOT / "runtimes" / "opencode" / "scripts" / "watch-work.py"))
        w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(w)
        with tempfile.TemporaryDirectory() as tmp:
            root = _Path(tmp)
            resume = root / ".opencode" / "resume"
            resume.mkdir(parents=True)
            (resume / "backend.txt").write_text(
                "task_id: F-1\nagent: build-back\nstatus: TERMINÉ\n"
                'completed_at: "2026-10-15T10:00:00+04:00"\nsummary: Fait.\n',
                encoding="utf-8")
            # Aucun rapport de review : l'etape est rejouable depuis le rapport.
            config = {"stages": [{"id": "review", "agent": "reviewer", "auto": True,
                                  "trigger": {"report": "backend.txt", "status": "TERMINÉ"}}]}
            task, statut, mtime = w.parse_report_file(resume / "backend.txt")
            triggers = {"backend.txt": {"task_id": task, "status": statut,
                                      "mtime": mtime, "hash": "h"}}
            eligible = w.eligible_transitions(config, triggers, {}, {})
            self.assertEqual([s["id"] for s, _t, _w in eligible], ["review"])
            # Le rapport d'entree suffit : aucune session n'est relue.
            cmd = w.build_command("hadji", "p", w.session_title("F-1", "hadji",
                                                                w.build_run_id()), "m")
            self.assertNotIn("--session", cmd)


if __name__ == "__main__":
    unittest.main(verbosity=1)

if __name__ == "__main__":
    unittest.main(verbosity=1)
