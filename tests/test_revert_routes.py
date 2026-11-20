# -*- coding: utf-8 -*-
"""Routes du dashboard chat Sayrhazi (pass-through OpenCode).

Le backend Sayrhazi ne touche jamais à SQLite ni aux snapshots : les routes
relais relaient l'API OpenCode et propagent l'id de session, l'erreur éventuelle
et l'état `session.revert` / `session.summary`. OpenCode est stubé (aucun
serveur réel impliqué) ; le handler HTTP tourne pour de vrai sur un port
éphémère.
"""

import importlib.util
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "runtimes" / "opencode" / "scripts"
sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("sayrhazi_serveur", SCRIPTS / "sayrhazi-serveur.py")
sayrhazi_serveur = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sayrhazi_serveur)


class FakeOpenCode:
    """Double de test : rejoue le contrat OpenCode 1.19 (revert/unrevert)."""

    def __init__(self):
        self.calls = []

    def revert_session(self, session_id, message_id, directory="", part_id=""):
        self.calls.append({"op": "revert", "session_id": session_id,
                           "message_id": message_id, "part_id": part_id})
        return 200, {
            "id": session_id,
            "revert": {"messageID": message_id, "snapshot": "snap1", "diff": "diff --git a/f b/f"},
            "summary": {"additions": 2, "deletions": 1, "files": 1},
        }

    def unrevert_session(self, session_id, directory=""):
        self.calls.append({"op": "unrevert", "session_id": session_id})
        return 200, {"id": session_id, "revert": None}

    def get_session(self, session_id, directory=""):
        self.calls.append({"op": "get_session", "session_id": session_id})
        return 200, {
            "id": session_id,
            "title": "Discussion",
            "agent": "build",
            "parentID": "",
            "directory": "C:/projet",
            "revert": {"messageID": "msg_42", "snapshot": "snap1", "diff": "d"},
            "summary": {"additions": 5, "deletions": 2, "files": 2},
        }


class BusyOpenCode(FakeOpenCode):
    def revert_session(self, session_id, message_id, directory="", part_id=""):
        self.calls.append({"op": "revert", "session_id": session_id,
                           "message_id": message_id, "part_id": part_id})
        return 409, {"error": "SessionBusy"}


class RenameOpenCode(FakeOpenCode):
    """Double de test : rejoue le contrat OpenCode 1.19 (PATCH /session/{id})."""

    def rename_session(self, session_id, title, directory=""):
        self.calls.append({"op": "rename", "session_id": session_id, "title": title})
        return 200, {"id": session_id, "title": title}


class NotFoundOpenCode(RenameOpenCode):
    def rename_session(self, session_id, title, directory=""):
        self.calls.append({"op": "rename", "session_id": session_id, "title": title})
        return 404, {"message": "NotFound"}


class FakePromptOpenCode(FakeOpenCode):
    def __init__(self):
        super().__init__()
        self.prompt_payloads = []

    def prompt_async(self, session_id, message, agent="", directory="", model=None, attachments=None, root=None):
        self.prompt_payloads.append({"session_id": session_id, "message": message, "agent": agent, "model": model, "attachments": attachments, "root": root})
        return 204, {}


class TestRevertRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        cls._root = Path(cls._tmp)
        sayrhazi_serveur.ServerHandler.project_root = cls._root
        sayrhazi_serveur.ServerHandler.engine_config = None
        sayrhazi_serveur.ServerHandler.engine_usable = []
        cls.fake = FakeOpenCode()
        cls._previous_opencode = getattr(sayrhazi_serveur, "opencode_server", None)
        sayrhazi_serveur.opencode_server = cls.fake
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), sayrhazi_serveur.ServerHandler)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        sayrhazi_serveur.opencode_server = cls._previous_opencode

    def _post(self, path, payload=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.base + path, data=body, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
            finally:
                exc.close()

    def _get(self, path):
        req = urllib.request.Request(self.base + path, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")

    def test_revert_transmet_messageid_et_retourne_etat(self):
        self.fake.calls.clear()
        status, data = self._post("/api/chat/session/ses_test/revert",
                                  {"messageID": "msg_123"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["revert"]["messageID"], "msg_123")
        self.assertEqual(data["summary"]["files"], 1)
        self.assertEqual(self.fake.calls[0]["op"], "revert")
        self.assertEqual(self.fake.calls[0]["session_id"], "ses_test")
        self.assertEqual(self.fake.calls[0]["message_id"], "msg_123")

    def test_revert_part_id_transmis(self):
        self.fake.calls.clear()
        status, data = self._post("/api/chat/session/ses_test/revert",
                                  {"messageID": "msg_123", "partID": "prt_9"})
        self.assertEqual(status, 200)
        self.assertEqual(self.fake.calls[0]["part_id"], "prt_9")

    def test_revert_messageid_manquant_renvoie_400(self):
        status, data = self._post("/api/chat/session/ses_test/revert", {})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_revert_session_occupee_renvoie_409(self):
        previous = sayrhazi_serveur.opencode_server
        sayrhazi_serveur.opencode_server = BusyOpenCode()
        try:
            status, data = self._post("/api/chat/session/ses_test/revert",
                                      {"messageID": "msg_123"})
        finally:
            sayrhazi_serveur.opencode_server = previous
        self.assertEqual(status, 409)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "SessionBusy")

    def test_unrevert_transmet_et_retourne_etat_vide(self):
        self.fake.calls.clear()
        status, data = self._post("/api/chat/session/ses_test/unrevert")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIsNone(data["revert"])
        self.assertEqual(self.fake.calls[0], {"op": "unrevert", "session_id": "ses_test"})

    def test_revert_preview_restitution_sans_effet(self):
        """L'aperçu : revert puis unrevert, sans laisser de marqueur."""
        self.fake.calls.clear()
        status, data = self._post("/api/chat/session/ses_test/revert/preview",
                                  {"messageID": "msg_123"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIn("diff --git", data["diff"])
        # Les deux appels revert + unrevert doivent avoir été faits.
        self.assertEqual([c["op"] for c in self.fake.calls], ["revert", "unrevert"])

    def test_revert_preview_messageid_manquant_renvoie_400(self):
        status, data = self._post("/api/chat/session/ses_test/revert/preview", {})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_info_expose_revert_et_summary(self):
        status, data = self._get("/api/chat/session/ses_test/info")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["revert"]["messageID"], "msg_42")
        self.assertEqual(data["summary"]["files"], 2)

    def test_rename_transmet_title_et_renvoie_titre(self):
        previous = sayrhazi_serveur.opencode_server
        fake = RenameOpenCode()
        sayrhazi_serveur.opencode_server = fake
        try:
            status, data = self._post("/api/chat/session/ses_test/title", {"title": "Ma discussion"})
        finally:
            sayrhazi_serveur.opencode_server = previous
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["title"], "Ma discussion")
        self.assertEqual(fake.calls, [{"op": "rename", "session_id": "ses_test", "title": "Ma discussion"}])

    def test_rename_titre_vide_renvoie_400(self):
        status, data = self._post("/api/chat/session/ses_test/title", {"title": "   "})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_rename_titre_absent_renvoie_400(self):
        status, data = self._post("/api/chat/session/ses_test/title", {})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_rename_titre_trop_long_renvoie_400(self):
        status, data = self._post("/api/chat/session/ses_test/title", {"title": "x" * 201})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_rename_opencode_404_propage_404(self):
        previous = sayrhazi_serveur.opencode_server
        sayrhazi_serveur.opencode_server = NotFoundOpenCode()
        try:
            status, data = self._post("/api/chat/session/ses_test/title", {"title": "abc"})
        finally:
            sayrhazi_serveur.opencode_server = previous
        self.assertEqual(status, 404)
        self.assertFalse(data["ok"])

    def test_providers_transmet_le_catalogue_opencode(self):
        previous = sayrhazi_serveur._provider_catalog
        sayrhazi_serveur._provider_catalog = lambda: [
            {"id": "opencode", "name": "OpenCode", "models": [
                {"id": "big-pickle", "name": "Big Pickle", "free": True, "releaseDate": 20251017},
            ]},
            {"id": "google", "name": "Google", "models": [
                {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "free": False, "releaseDate": 20250617},
            ]},
        ]
        try:
            status, data = self._get("/api/chat/providers")
        finally:
            sayrhazi_serveur._provider_catalog = previous
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual([p["id"] for p in data["providers"]], ["opencode", "google"])

    def _patch_agent_model(self, full):
        previous = sayrhazi_serveur.agent_model
        sayrhazi_serveur.agent_model = lambda root, agent_file: (full, full.split("/")[-1])
        return previous

    def _patch_catalog(self, catalog):
        previous = sayrhazi_serveur._provider_catalog
        sayrhazi_serveur._provider_catalog = lambda: catalog
        return previous

    def test_current_model_google_payant(self):
        prev_am = self._patch_agent_model("google/gemini-2.5-pro-preview-tts")
        prev_cat = self._patch_catalog([
            {"id": "google", "name": "Google", "models": [
                {"id": "gemini-2.5-pro-preview-tts", "name": "Gemini 2.5 Pro Preview TTS", "free": False, "releaseDate": 20250501},
            ]},
        ])
        try:
            status, data = self._get("/api/chat/current-model?agent=bamse")
        finally:
            sayrhazi_serveur.agent_model = prev_am
            sayrhazi_serveur._provider_catalog = prev_cat
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["model"]["providerID"], "google")
        self.assertEqual(data["model"]["modelID"], "gemini-2.5-pro-preview-tts")
        self.assertEqual(data["model"]["name"], "Gemini 2.5 Pro Preview TTS")
        self.assertEqual(data["model"]["providerName"], "Google")
        self.assertFalse(data["model"]["free"])

    def test_current_model_opencode_free(self):
        prev_am = self._patch_agent_model("opencode/big-pickle")
        prev_cat = self._patch_catalog([
            {"id": "opencode", "name": "OpenCode", "models": [
                {"id": "big-pickle", "name": "Big Pickle", "free": True, "releaseDate": 20251017},
            ]},
        ])
        try:
            status, data = self._get("/api/chat/current-model?agent=bamse")
        finally:
            sayrhazi_serveur.agent_model = prev_am
            sayrhazi_serveur._provider_catalog = prev_cat
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["model"]["name"], "Big Pickle")
        self.assertEqual(data["model"]["providerName"], "OpenCode")
        self.assertTrue(data["model"]["free"])

    def test_current_model_agent_traversal_renvoie_400(self):
        req = urllib.request.Request(self.base + "/api/chat/current-model?agent=../../etc", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status, body = resp.status, json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            try:
                status, body = exc.code, json.loads(exc.read().decode("utf-8") or "{}")
            finally:
                exc.close()
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_prompt_async_model_transmis_tel_quel(self):
        previous = sayrhazi_serveur.opencode_server
        fake = FakePromptOpenCode()
        sayrhazi_serveur.opencode_server = fake
        try:
            status, data = self._post("/api/chat/session/ses_test/prompt_async", {
                "message": "bonjour",
                "agent": "bamse",
                "model": {"providerID": "google", "modelID": "gemini-2.5-pro"},
            })
        finally:
            sayrhazi_serveur.opencode_server = previous
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(fake.prompt_payloads[0]["model"], {"providerID": "google", "modelID": "gemini-2.5-pro"})
        self.assertEqual(fake.prompt_payloads[0]["agent"], "bamse")

    def test_prompt_async_sans_model_le_model_est_absent(self):
        previous = sayrhazi_serveur.opencode_server
        fake = FakePromptOpenCode()
        sayrhazi_serveur.opencode_server = fake
        try:
            status, data = self._post("/api/chat/session/ses_test/prompt_async", {
                "message": "bonjour", "agent": "bamse",
            })
        finally:
            sayrhazi_serveur.opencode_server = previous
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIsNone(fake.prompt_payloads[0]["model"])

    def test_catalog_shape_free_first_opencode_first(self):
        previous_urlopen = sayrhazi_serveur.urllib.request.urlopen

        class _FakeResp:
            def __init__(self, payload):
                self._payload = json.dumps(payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return self._payload

        payload = {
            "providers": [
                {"id": "google", "name": "Google", "models": {
                    "gemini-x": {"name": "Gemini X", "cost": {"input": 1}, "status": "active", "release_date": "2025-01-01"},
                    "gemma-y": {"name": "Gemma Y", "cost": {"input": 0}, "status": "active", "release_date": "2025-01-02"},
                }},
                {"id": "opencode", "name": "OpenCode", "models": {
                    "paid-mini": {"name": "Paid Mini", "cost": {"input": 0.5}, "status": "active", "release_date": "2025-03-01"},
                    "free-big": {"name": "Free Big", "cost": {"input": 0}, "status": "active", "release_date": "2025-02-01"},
                    "old-deprecated": {"name": "Old", "cost": {"input": 0}, "status": "deprecated", "release_date": "2020-01-01"},
                }},
            ],
        }
        sayrhazi_serveur.urllib.request.urlopen = lambda *a, **kw: _FakeResp(payload)
        sayrhazi_serveur._provider_catalog.cache_clear() if hasattr(sayrhazi_serveur._provider_catalog, "cache_clear") else None
        sayrhazi_serveur._PROVIDER_CATALOG_CACHE = None
        sayrhazi_serveur._PROVIDER_CATALOG_AT = 0.0
        try:
            catalog = sayrhazi_serveur._provider_catalog()
        finally:
            sayrhazi_serveur.urllib.request.urlopen = previous_urlopen
            sayrhazi_serveur._PROVIDER_CATALOG_CACHE = None
            sayrhazi_serveur._PROVIDER_CATALOG_AT = 0.0

        self.assertEqual(catalog[0]["id"], "opencode")
        self.assertEqual([m["id"] for m in catalog[0]["models"]], ["free-big", "paid-mini"])
        self.assertEqual(catalog[0]["models"][0]["free"], True)
        self.assertEqual(catalog[0]["models"][1]["free"], False)
        google = next(p for p in catalog if p["id"] == "google")
        # google : aucun modèle free (cost.input 0), triés par date décroissante
        self.assertEqual([m["id"] for m in google["models"]], ["gemma-y", "gemini-x"])
        self.assertEqual(google["models"][0]["free"], False)


if __name__ == "__main__":
    unittest.main()
