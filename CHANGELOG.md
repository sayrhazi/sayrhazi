## 0.16.0 - 2026-11-14

- Sayrhazi GUI : mentions `@fichier.ext[#1-20]` dans le composer (autocomplétion confinée au répertoire racine, coloration `--accent`), bouton `+` pour joindre des fichiers/répertoires comme parts `file` OpenCode, tooltip au survol dans « Sélectionner un modèle » (Entrées / Raisonnement).
- Sayrhazi GUI : bandeau d'erreur upstream (retry ambre avec countdown, erreur définitive rouge avec modal `(i)`), compaction silencieuse (badge « Compacting… »), questions/permissions de l'agent (bandeau multi-onglets + modal).
- Dashboard chat : bouton d'envoi à état explicite (■ en attente / ↑ au repos), interruption via `POST /api/chat/session/:id/abort`, foot « Interrompu » à l'arrêt.
- Dashboard chat : revert natif d'OpenCode (bouton ↩, modal Sayrhazi, bandeau « N message(s) reverted » au-dessus du composer, liste des fichiers avec `+/-`).
- Sayrhazi update : `permission.question: "ask"` et `permission.external_directory: "ask"` ajoutés dans `.opencode/opencode.json` (avec backup).
- Tests : `tests/chat_frontend_sim.js` et `tests/test_core.py` étendus (mentions, erreurs upstream, compaction, relais SSE).

## 0.15.0 - 2026-11-13

- Serveur unifié `sayrhazi-serveur.py` (dashboard + watcher en un processus) et nouvelle sous-commande `sayrhazi server` ; routes web `/dashboard`, `/chat`, `/agents/<agent>` ; contrôle du watcher depuis l'interface web.
- Dashboard chat refondu : création et suppression de discussions via l'API native OpenCode (sessionID réel, renommage automatique du titre par l'agent natif `title`), menu contextuel de suppression, flux SSE `session.updated`/`session.deleted` synchronisé avec la barre latérale, streaming token par token, indicateur « L'agent est en cours… », bouton d'envoi à état explicite (■ en attente, ↑ au repos), tableau Markdown avec bordures, bouton `copy` sur la bulle utilisateur, plus de discussions dupliquées dans la sidebar.
- Dashboard chat : durée des réponses = durée du tour (comme la TUI), pieds de message posés sur le message le plus récent du tour, streaming fluide en direct, rechargement global supprimé en fin de tour, ouverture d'une discussion existante en bas de page.
- Suppression de l'ancien dashboard terminal (`watch-tui.py`) et du dashboard web autonome (`watch-web.py`) : la seule interface Sayrhazi est désormais le dashboard web servi par `sayrhazi-serveur.py` (port 8363, `sayrhazi server`). La dépendance `textual` n'est plus nécessaire.
- Nouveau module interne `sayrhazi-modules.py` (corps partagé : lecture OpenCode + `Handler` web). `sayrhazi-serveur.py` ne dépend plus de `watch-tui.py` ni de `watch-web.py`. `watch-work.py` (moteur du bouton Marche/Arrêt) reste en place.
- Installation / mise à jour : `engine/installer.py` et `engine/updater.py` copient désormais `sayrhazi-modules.py` + serveur + statiques ; les installateurs shell et PowerShell font de même (parité avec l'updater) ; `~/.opencode/agent` synchronisé avec le projet (OpenCode y lit le modèle au lancement). Le binaire npm `sayrhazi-tui` est supprimé.
- Documentation : `docs/chat-streaming.md` ajouté. `README.md`, `docs/architecture.md`, `docs/installation.md`, `docs/automation.md`, `AGENTS.md`, `pyproject.toml`, `package.json` alignés : le TUI n'est plus documenté ni distribué.

## 0.14.2 - 2026-10-04
