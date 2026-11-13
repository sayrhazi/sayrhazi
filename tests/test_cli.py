#!/usr/bin/env python3
"""Tests CLI Sayrhazi : dispatch main.py, info, remove prudent. Stdlib.

Usage : python tests/test_cli.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = [sys.executable, str(ROOT / "cli" / "main.py")]


class TestCli(unittest.TestCase):
    def test_commande_inconnue(self):
        r = subprocess.run(MAIN + ["nope"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_install_info_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(MAIN + ["install", "--project", tmp], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = subprocess.run(MAIN + ["info", "--project", tmp], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("statut", r.stdout)
            r = subprocess.run(MAIN + ["remove", "--project", tmp], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Simulation", r.stdout)
            # noyau installs ? agent copies ?
            self.assertTrue((Path(tmp) / ".opencode" / "sayrhazi-serveur.py").is_file())
            self.assertTrue((Path(tmp) / ".opencode" / "static").is_dir())
            r = subprocess.run(MAIN + ["remove", "--project", tmp, "--yes"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            # Sayrhazi noyau retire
            self.assertFalse((Path(tmp) / ".opencode" / "sayrhazi-serveur.py").exists())
            self.assertFalse((Path(tmp) / ".opencode" / "sayrhazi-modules.py").exists())
            self.assertFalse((Path(tmp) / ".opencode" / "watch-work.py").exists())
            self.assertFalse((Path(tmp) / ".opencode" / "services.py").exists())
            self.assertFalse((Path(tmp) / ".opencode" / "static").exists())
            self.assertFalse((Path(tmp) / ".opencode" / "state").exists())
            # traces volontaires conservees
            self.assertTrue((Path(tmp) / ".opencode" / "agent" / "bamse.md").is_file())
            self.assertTrue((Path(tmp) / ".opencode" / "sayrhazi.yaml").is_file())
            self.assertTrue((Path(tmp) / ".opencode" / "workflow.yaml").is_file())
            self.assertTrue((Path(tmp) / ".opencode" / "models.yaml").is_file())

    def test_check_non_installe(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(MAIN + ["check", "--project", tmp], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertIn("NON INSTALLE", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)
