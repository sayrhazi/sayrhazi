## 0.13.2 — 2026-10-16

- Documentation : le dashboard exige `textual`, absent d'une installation fraîche, installé via un venv pour ne pas heurter au Python système (Ubuntu 23.10+ applique PEP 668) ; `info` et `update` sont des commandes système sous Ubuntu, donc `info sayrhazi` répond « aucune entrée de menu » tant que le profil bash n'est pas sourcé — le binaire global n'a pas ce conflit. `docs/automation.md` : panneau de détail, et rappel que la sélection survit au rafraîchissement.
- Dashboard : le panneau de détail était invisible alors qu'il était écrit. `#details` et `#logs` partageaient `dock: bottom` sans conteneur : deux widgets ancrés au même bord se superposent au lieu de s'empiler, et le journal peint par-dessus masquait entièrement le détail. Les deux panneaux sont désormais dans un `Vertical` commun (`#bottom`). Vérifié en headless sur trois tailles de terminal (120×40, 100×30, 80×24) : plus de chevauchement, le tableau garde de la hauteur et la navigation clavier fonctionne.
- Dashboard : les horaires du détail affichaient `créée 0s · active 0s`. OpenCode stocke `time_created`/`time_updated` en millisecondes ; passés tels quels à `format_age`, le delta devenait négatif et `max(0, …)` le réduisait à zéro. Conversion en secondes à la lecture.
- Dashboard : le détail affichait `—` comme état pour tout agent. La valeur par défaut `—` du bloc d'initialisation était réappliquée **après** le calcul. 3 nouveaux tests.
- Dashboard : une session ouverte depuis plus de 24 h affichait `2903m11s`. La durée se lit maintenant en `2j 00h` et s'appelle `ouverte` : `time_updated - time_created` mesure le temps pendant lequel la session est restée ouverte, pas un temps de travail.
- Dashboard : la ligne sélectionnée ne revient plus à la première ligne au rafraîchissement. `refresh_data` reconstruisait le tableau (`table.clear()`) à chaque tick, ce qui réinitialisait le curseur. L'agent sous le curseur est mémorisé par son rôle et restauré après reconstruction — mémorisé par identité, pas par index, pour survivre à tout réordonnancement des lignes.
- Dashboard : panneau de détail sous le tableau, suivant la ligne sélectionnée (flèches ou clic, aucune touche supplémentaire). Il décompose le contexte exactement comme OpenCode l'a compté — `input`, `cache.read`, `cache.write`, `output`, `reasoning` — et affiche modèle, fenêtre de contexte, coût de session et horodatages. La décomposition est ce que la jauge seule cachait : sur les sessions réelles de Sayrhazi-web, `cache.read` représente 99 % du contexte de Bamse (570 993 sur 572 128) et 98 % de celui de Hadji (51 441 sur 52 282). Lire « 55 % de fenêtre » sans cette ligne laisse croire que l'agent a produit 572 K tokens, alors qu'il n'a quasi rien produit et relu le projet. Un agent sans mesure affiche `n/d` partout — aucun pourcentage, aucune durée, aucun coût inventés. 4 nouveaux tests.
- `tunda sayrhazi` vérifie enfin le dashboard : présence de `watch-tui.py`, disponibilité de la dépendance `textual` et présence du registre `models.yaml`. Sans cela, `sayrhazi-tui` échouait sur une erreur d'import opaque, jamais annoncée. Le message nomme la commande d'installation adaptée à la plateforme (`pip` / `pip3`).
- `engine/installer.py` installe désormais `.opencode/models.yaml`. L'updater et les deux installeurs shell/PowerShell le faisaient déjà ; le chemin d'installation Python, lui, ne le faisait pas — une installation par la commande npm laissait donc le dashboard en `n/d` sur le contexte sans aucun avertissement. Jamais écrasé, comme ailleurs. Bug trouvé par le nouveau contrôle de `tunda`.

## 0.13.1 — 2026-10-16

- Nouveau binaire npm `sayrhazi-tui` : lance le dashboard sans avoir à se souvenir de `python .opencode/watch-tui.py`, et **sans dépendre d'un profil de terminal**. Une fonction de profil n'existe que dans la session qui l'a définie : `sayrhazi-tui` en profil était introuvable en `CommandNotFoundException` même après une mise à jour réussie, jusqu'au rechargement manuel du profil ou l'ouverture d'un nouveau terminal. Installé sur le PATH par `npm i -g sayrhazi`, il marche immédiatement et pour toujours. Un rechargement automatique depuis `update` a été essayé puis retiré : PowerShell ne permet pas de pousser une fonction vers la session appelante depuis une fonction, le message promettait donc un résultat qu'il ne pouvait pas tenir. `update sayrhazi` rappelle désormais les deux sorties réelles quand la commande est absente.
- `sayrhazi-tui` (profil PowerShell et bash) : premier argument = point de départ, racine cherchée en remontant jusqu'à `.opencode/watch-tui.py` — rester dans `.opencode` chercherait sinon `.opencode/.opencode`. Le dashboard résout sa racine depuis le répertoire courant : la commande change donc de dossier le temps du lancement puis revient, sans quoi elle analysait le dossier du terminal (Sayrhazi lui-même si on part du dépôt). Seul le premier argument peut être le projet, sinon la valeur d'un flag (`--lines 0`) passait pour un chemin. Flags après le chemin transmis tels quels (`--once`, `--interval`, `--lines`).
- Dashboard : la ligne est scindée en deux blocs explicitement séparés, parce qu'elle mélangeait deux portées sans le dire. `Session`, `Origine`, `Jauge`, `Tokens`, `%` et `Coût sess.` décrivent **uniquement la session affichée** ; `Auto`, `Manuel` et `Coût proj.` agrègent **toutes** les sessions de l'agent sur le projet, tâches confondues. Un `Auto = 2` placé à côté d'une session donnée laissait croire que ce 2 décrivait cette session — il compte les appels du watcher depuis le début du projet. Séparateur `│` dans les deux modes (TUI et `--once`), libellés renommés (`Coût sess.` / `Coût proj.`) et légende affichée dans l'en-tête. Le coût cumulé du projet occupait jusqu'ici la colonne `Coût` unique, à côté du contexte d'une seule session.
- Dashboard : la sortie `--once` ne laisse plus fuir le balisage Textual (`[dim]…[/]`), qui rendait la sortie brute illisible et faussait l'alignement des colonnes (les balises comptent dans la largeur). 3 nouveaux tests.

## 0.13.0 — 2026-10-15

- Installateurs : `.opencode/models.yaml` n'est plus écrasé. L'installateur le recopiait en `-Force` alors que le contrat annonce un registre surchargeable par projet et que l'updater le préserve — réinstaller détruisait donc les fenêtres déclarées par le projet. Les deux installeurs (PowerShell, shell) ne créent le registre que s'il est absent, comme l'updater.
- Dashboard : la session affichée est la plus récemment **active** (`session.time_updated`), plus la plus récemment **créée**. La ligne affichait toujours la mauvaise session sinon (reproduit : `Travail à faire`, session manuelle créée avant une session automatique mais toujours ouverte, 470 424 tokens / 45 % côté OpenCode contre 41 727 / 4 % côté dashboard). Le dashboard ne montre plus une session automatique périmée alors que l'utilisateur regarde un terminal manuel.
- Dashboard : un message assistant dont le contexte vaut zéro n'est pas une mesure et ne peut plus faire tomber la jauge à 0. Deux formes relevées dans la base d'OpenCode : le message ouvert au début d'un run (`finish` absent) et un tour interrompu (`finish: "unknown"`, chaîne non vide donc truthy — un test sur la présence de `finish` ne l'écarte pas). Le dashboard saute ces messages et affiche la dernière valeur réelle. Constaté : la ligne Lawibrahim tombait à 0 alors que la session mesurait 160 408 tokens / 15 %.
- Dashboard : la compaction d'OpenCode est vérifiée sans effet particulier — elle est marquée par un part `compaction` attaché à un message **user**, jamais à un message assistant ; le dashboard ne lit donc qu'une baisse de contexte normale (relevé : 505 540 → 431 570 → 16 176 tokens). 3 nouveaux tests, dont un sur la compaction.
- Le watcher ne fait plus le travail de session d'OpenCode : plus de `--session` (une session neuve par appel), plus de lecture de sa base, plus de seuil `session.max_usage`, plus de rotation ni de compaction maison, plus de baselines. Chaque appel titre sa session de façon unique et déterministe (`Sayrhazi <task_id> <agent> <run_id>`) ; `run_id` = horodatage + suffixe. Raison : le seuil estimait la saturation avec des compteurs cumulés en excluant `tokens_cache_read` alors que cette relecture est la majeure partie du contexte renvoyé — la jauge affichait ~0 % pendant que le panneau Context d'OpenCode affichait la vraie valeur. Le bloc `session:` disparaît de `workflow-state.yaml` (un état plus ancien le contient encore : accepté, jamais exigé, jamais réécrit) et `session.max_usage` / `session.default_limit` ne sont plus lus. La mémoire d'un agent reste dans ses rapports.
- Dashboard `watch-tui.py` : les métriques sont lues chez OpenCode au lieu d'être estimées — formule du panneau Context d'OpenCode sur le dernier message assistant (`input + output + reasoning + cache.read + cache.write` / fenêtre du modèle), contexte et coût séparés, aucun pourcentage cumulatif. Les six agents sont affichés, y compris ceux appelés **manuellement** (Lawibrahim, Bamse désactivé, Ali, tout appel direct) : une session est dite `auto` si son titre porte le préfixe `Sayrhazi <task_id> <agent> ` (lancée par le watcher), `manuel` sinon ; deux compteurs séparés, la ligne montre la session la plus récente de l'agent. Le filtre reste strict sur le projet et l'agent — jamais la session d'un autre agent. 1 nouveau test.
- Nouveau registre `core/runtime/models.yaml` (installé dans `.opencode/models.yaml`, surchargeable par projet) : OpenCode ne stocke nulle part la fenêtre de contexte d'un modèle. Six modèles relevés le 2026-10-15 (`longcat-2.5-preview-free` 1 000 000, `muse-spark-1.3-contributor-free` 1 048 576, `nemotron-3-ultra-free` 1 000 000, `nemotron-3.5-super-vl-free` 1 048 576, `space-bunny-free` 1 048 576, `step-5-preview-free` 1 000 000). C'est un relevé ponctuel, pas un catalogue : un fournisseur peut retirer un modèle ou le rendre payant, une présence dans le fichier ne signifie donc jamais qu'un modèle est gratuit. Modèle absent → `n/d` affiché plutôt qu'un pourcentage silencieusement faux ; tokens et coût restent affichés.
- Modèle par agent documenté (`docs/agents.md`) : chaque agent porte son `model:` dans sa fiche, ce qui permet d'affecter un modèle différent à chaque agent, payant compris ; la fenêtre du modèle se déclare dans le registre.
- `session:` retiré du template `sayrhazi.yaml` et du schéma : le mécanisme de seuil/rotation n'existe plus. Un bloc `session:` résiduel dans une config existante reste accepté (sans effet) pour ne pas casser les installations en place.
- Fiches agents : « en session partagée, tout est déjà relu par tous » remplacé par le modèle réel (session neuve par appel, mémoire dans les rapports). Adaptateurs régénérés. `docs/automation.md` refait (sessions, dashboard).
- Robustesse du `run_id` : le suffixe ne dépend plus du seul PID (identique pour tous les threads d'une vague) mais d'un compteur de séquence sous verrou et d'un sel d'instance — deux appels du même agent dans la même seconde donnent deux titres distincts (vérifié : 360 appels concurrents, 360 titres uniques).
- Registre : une fenêtre `context_tokens` invalide (absente, non numérique, nulle ou négative) est traitée comme un modèle inconnu (`n/d`) au lieu de produire un pourcentage absurde.
- Dashboard : le libellé du pourcentage précise qu'il s'agit de l'occupation du contexte au dernier message, pas d'une capacité restante (le budget effectif dépend du fournisseur, de la compaction et des limites du runtime).
- `core/rules/reporting.md` : le rapport est l'unique mémoire d'un agent (session jetable). Exigences sur une tâche longue — décisions, hypothèses retenues et écartées, fichiers modifiés, problèmes non résolus, prochaines actions ; un rapport qui ne permet pas de reprendre sans relire tout le code n'est pas un rapport.
- Dashboard : jauge et nombre de tokens séparés en deux colonnes. Ils étaient concaténés dans une seule cellule, si bien que l'en-tête comptait une case de moins que la ligne et que le tableau se décalait. 1 nouveau test qui verrouille l'alignement (une valeur par colonne).
- 6 nouveaux tests : deux appels simultanés du même agent, base OpenCode inaccessible, modèle absent du registre, cache dominant conforme au panneau Context d'OpenCode, mise à jour d'une installation ancienne avec `session:`, reprise après arrêt brutal pilotée par les rapports.

## 0.12.3 — 2026-10-14

- Lawibrahim exige un cahier des charges d'abord : sur projet neuf, cherche `cahier-des-charges.md`/`SPEC.md`/`spec.md`, le demande s'il est absent (sans lui, pas de `plan.txt`), le lit une fois puis seulement en cas d'ambiguïté. 1 nouveau test.
- npm : le `postinstall` indique comment sourcer les fonctions terminal (`Sayrhazi/tunda/update/info`) depuis le paquet installé.
- Pendant bash (`Sayrhazi-Profile.sh`) : `Sayrhazi/tunda/update/info/remove` pour Ubuntu, `Sayrhazi` et `sayrhazi` coexistent (casse sensible).
- CLI harmonisé : `sayrhazi` nu installe + contrôle dans `--project` (comme la fonction de profil), `--version` affiche la version.

## 0.12.2 — 2026-10-14

- Profil versionné (`Sayrhazi-Profile.ps1`) : fonctions `Sayrhazi/tunda/update/info/remove` sourcées depuis le dépôt, flags (`--version`) routés vers le CLI au lieu de l'installeur.
- Zéro chemin machine en dur : `README.md` et `docs/installation.md` passent à npm/clone génériques (vérifié par recherche).

## 0.12.1 — 2026-10-14

- CLI : `sayrhazi --version` affiche la version (et invocation sans commande) au lieu d'une erreur argparse.

## 0.12.0 — 2026-10-12

- Reprise après interruption (`--continue`) : marqueur d'arrêt propre au Ctrl+C au repos, diagnostic au redémarrage (marqueur, agents en cours purgés, appels sans terminaison au journal, pause quota, sessions reprises), puis reprise du travail inachevé. 3 nouveaux tests.

- Dashboard TUI `watch-tui.py` (`textual`) : vraie interface (alternate screen, raw mode, tableau 6 agents, logs, refresh auto) + mode `--once` ; copié par install/update vers `.opencode/`. Repli base locale : sans bloc suivi (agents manuels Lawibrahim/Ali/Bamse), affiche la dernière session DB (`~` = cumulé, borne haute).
- Nouveau stage `rework_qa` : `qa.txt À COMPLÉTER` rappelle Bamse automatiquement (Zawadi attend une action — purge cache, relance frontal, fix env — puis recontrôle ; garde anti-boucle : pas de rappel si `build.txt` plus récent). Contrat (`core/workflow/workflow.yaml`, template), réconciliation (`engine/state.py` rouvre l'implémentation), docs (`automation.md`, `reports.md`). Les `workflow.yaml` existants (jamais écrasés) doivent ajouter le stage manuellement. 2 nouveaux tests.
- Fix sessions : baselines à 0 à l'adoption d'une session neuve (tout son contenu est neuf) — corrige l'usage/tokens à 0 dans l'état et le TUI alors que le journal montrait les vraies valeurs (constaté FEATURE-007 : `usage 0 % · 0 tokens` vs `usage 5 % · 53 983 tokens`). 1 nouveau test.
- Circuit breaker quota : dès qu'un agent renvoie quota/rate-limit, la vague entière est mise en pause (plus de retry infini), alerte journalisée, reprise manuelle après réinitialisation du quota. 1 nouveau test.

## 0.11.1 — 2026-10-10

- Sessions OpenCode : une session par (tâche, agent) au lieu d'une par tâche — une session partagée entre agents casse l'identité des rôles (`zawadi` répondant en Hadji constaté). Attribution déterministe par titre (`Sayrhazi <task> <agent>`), mapping par agent dans `workflow-state.yaml` (réinitialisé au changement de tâche), modèle du frontmatter imposé à chaque appel (`--model`, l'état ne fait que constater), usage affiché avec le nombre de tokens neufs et le coût (`usage 6 % · 12 029 tokens · $0.00`), snapshot daté persisté dans l'état (`usage`, `used_tokens`, `cost`, `observed_at`), rotation suivie par agent.

- Cloisonnement d'écriture en session partagée : chaque appel exige que l'agent ne produise QUE son rapport (lecture des autres autorisée, écriture interdite, archivage vers `history/` autorisé) — corrige les trois rapports réécrits par chaque agent.
- Rotation préventive : session déjà au-delà du seuil au déclenchement → on pivote AVANT l'appel (constaté : au-delà de 100 %, le modèle boucle en répétitions).
- Timeout lucide : rapport produit malgré le timeout (processus bloqué après écriture) → succès acté, sans retry inutile ; retries à un seul coup et ligne `État :` seulement en cas de changement (fini le spam toutes les 3 s). 1 nouveau test.
- Lecture frugale : nouvelle section dans les six fiches agents (`core/agents/*/instructions.md`, adaptateurs régénérés) — relire son précédent rapport d'abord, ne lire que les fichiers modifiés, traiter les rapports des autres comme des acquis, réserver le lourd aux fichiers changés, proportionner l'effort à l'ampleur du changement (doute → complet). 1 nouveau test (`TestRenderer.test_lecture_frugale_partout`).

## 0.11.0 — 2026-10-06

- Bannière `info sayrhazi` en bleu `#269CD8` (ANSI truecolor, terminal uniquement, sorties redirigées inchangées).
- Sessions OpenCode partagées : une session par tâche (`--session`, persistée dans `workflow-state.yaml`), usage réel lu dans la base locale (contenus nouveaux `input + output + cache_write` depuis l'adoption, relectures de cache exclues ; fenêtre 1 048 576 pour Muse Spark), rotation à `session.max_usage` (défaut 0.95, configurable) ou après compaction, adoption unique en vague parallèle, nouvel essai unique si l'id est inconnu. Console colorée (horodatage vert, agents, `task_id`, usage, erreurs) avec modèle affiché par agent ; lisibilité : vague de déclenchements encadrée (`#`) et sortie de chaque agent encadrée (`AGENT · … / FIN · …`), respiration par lignes vides, session reprise affichée au démarrage ; cloisonnement d'écriture en session partagée (chaque agent ne produit QUE son rapport) ; journal en texte brut. 14 nouveaux tests (`TestSession`).
- Dossier `.opencode/backup/` dédié aux backups de config horodatés (créé par install/update, `history/` réservé aux journaux ; anciens backups laissés en place). 1 nouveau test d'intégration.

## 0.10.1 — 2026-10-05

- Classification des échecs dans le watcher : quota/rate limit => messages explicites et aucun retry automatique (jamais de boucle de quota) ; réseau/timeout => retry avec backoff (30s, 90s, 270s, max 3) ; autres erreurs => échec normal journalisé. Message final explicite pour chaque catégorie.

## 0.10.0 — 2026-10-05

- Lot 7 services séparés : règle serveurs dans les instructions (Bamse, Zawadi) — URL utilisée telle quelle, serveur absent = limite signalée, jamais de (re)démarrage ni kill de port. Nouveau `services.py` (`start/stop/restart/status/logs`, détaché multi-plateforme, santé HTTP, logs capturés, arrêt signalé, redémarrage jamais auto). Câblé à install/update/`tunda`. 4 nouveaux tests (`TestServices`, cycle réel sur `http.server`).

## 0.9.0 — 2026-10-05

- Lot 6 parallélisme : étapes éligibles ensemble lancées en parallèle (`ThreadPoolExecutor` stdlib), bornées par `workflow.max_parallel_agents` (défaut 3, garde-fou machine), convergence journalisée (`Convergence : … (n/n)`), échec d'une branche isolé (les autres continuent, réévaluation au prochain changement). `running_agents` add/remove thread-safe, séquentiel si une seule étape, repli historique inchangé. 4 nouveaux tests (`TestParallel`, déclencheurs simulés).

## 0.8.0 — 2026-10-05

- Lot 5 conditions : `security` et `visual_qa` automatiques sous condition (`security_required`, `visual_qa_required` lus dans `sayrhazi.yaml`, sécurité vraie par défaut), nouvelle étape `rework` (retour vers Bamse sur `CORRECTIONS NÉCESSAIRES`, sans rappel si `build.txt` plus récent), Ali toujours manuel (`design_required` ajouté au schéma et au modèle, défaut faux). Nouveau `summarize_agents()` + section « agents requis » dans `tunda` (requis / manuel / inactif). 4 nouveaux tests (`TestConditions`).
- Verrou durci : heartbeat (jamais périmé à tort pendant un long run), purge du périmé seulement si PID mort (vérifié sans `os.kill`, bloquant sur Windows), `release` limité à son propre PID.
- BOM UTF-8 toléré partout (cas Sarhi : `review.txt` avec BOM rendait son `task_id` invisible au watcher qui relançait Hadji) : `watch-work.py`, `engine/report.py`, `engine/workflow.py`, `engine/sayrhazi_config.py`. 2 nouveaux tests BOM.

## 0.7.0 — 2026-10-04

- Lot 4 watcher généralisé : `watch-work.py` devenu moteur piloté par `workflow.yaml` (étapes `auto: true`, conditions depuis `sayrhazi.yaml`, garde sortie-existante généralisée, verrou d'instance, journal `workflow-log.md`, état minimal compatible `engine/state.py`, repli historique sans config). Déclenchement générique par rôle, `eligible_transitions()` pure et testée. 4 nouveaux tests (`TestWatcherEngine`), produit du nom conservé pour compatibilité.

## 0.6.0 — 2026-09-26

- Licence : `LICENSE` MIT remplacée par tous droits réservés (ne vaut que pour l'avenir, les versions déjà diffusées restent MIT).
- Lot 3 état et reprise : `.opencode/state/workflow-state.yaml` (vue de coordination, local, à ignorer par git), `engine/state.py` complété (`reconcile()` en 7 points, `validate_state()`, CLI `--reconcile`). Les rapports invalides ou d'une autre tâche sont ignorés et signalés, une étape terminée n'est jamais relancée (sauf retour `CORRECTIONS NÉCESSAIRES`), toute incohérence est signalée. Install/update créent `state/`, `tunda`/`checker` contrôlent en WARN. 8 nouveaux tests (`TestStateReconcile` + création du dossier).

## 0.5.0 — 2026-09-26

- Lot 2 config déclarative : `.opencode/workflow.yaml` versionné (parcours lisible, `trigger: {report, status}` + `condition` + `auto`, seule `review` automatique). Référence `core/workflow/workflow.yaml` (cohérence testée avec `stages/`, `qa.yaml` renommé `visual_qa.yaml`), nouveau `engine/workflow.py` (parseur tolérant listes `- `, validation chaînée aux statuts Lot 1). Install/update créent si absent sans jamais écraser, `tunda`/`checker` contrôlent en WARN. 5 nouveaux tests (`TestWorkflow` + install non-destructif).

## 0.4.2 — 2026-09-26

- Directive design Ali : méthode en 4 temps (cadrer selon `project.type`, chercher 2 à 3 références réelles citées avec retenu et rejeté, fonder palette/typographie/tokens avant de maquetter, maquetter sans plagier avec sources créditées). Correspondance type → attendus (`application` → dashboard, `website` → landing, `api` → portail docs, `library` → docs et playground). `contract.yaml` aligné, `docs/agents.md` à jour, nouveau test `TestAliDirective`. Protocole de recherche obligatoire : cible URL fournie visitée via Playwright avant toute adaptation (structure, palette, typo analysées puis adaptées aux contenus du projet, jamais copiées), 2 à 3 références sinon, indisponibilité outils déclarée. MCP galerie en évaluation manuelle.

## 0.4.1 — 2026-09-26

- Fix `task_id` : les instructions des six agents exigent désormais le bloc contrat machine en tête de rapport (`task_id:`, `agent:`, `status:` du rôle, `completed_at:`, `summary:`, sans gras ni puces) — sans lui le watcher refusait (cas Bamse/Sarhi). Contrats `contract.yaml` alignés, coquille `A IMPLÉMENTER` corrigée, `VALIDÉ` ajouté aux verdicts plan. Nouveau test `TestAgentContracts`. `renderer.py` force le LF (corrige la régénération CRLF sous Windows). `watch-work.py` : minuteur visible (tick toutes les 30 s pendant l'appel, durée `5s`/`1m`/`2m14s` à la fin) + ligne `En attente d'une autre tâche...` après chaque transition réussie (fini l'impression de freeze) + point 7 du contrat (un `review.txt` plus récent que `build.txt` pour le même `task_id` bloque tout redéclenchement après redémarrage, sans bloquer le retour après corrections).

## 0.4.0 — 2026-09-26

- Lot 1 contrats : statuts figés (`TERMINÉ` = achèvement, `VALIDÉ` = verdict, `VALIDÉ` ajouté pour `plan.txt`), `completed_at` ISO remplace `version` comme horodatage (refusé avec message), `task_id` stable (`FEATURE-001` ou `MPANGO-2026-014`), `agents_required` + indicateurs `security/visual_qa/design_required` avec règle de cohérence.
- Nouveau `engine/report.py` (stdlib) : `parse_report`, `validate_report`, `validate_task`, `check_transition` (points 3/4/5/7). `tunda` et `checker.py` signalent les rapports non conformes en `[WARN]`/notes, jamais bloquant (anciens rapports sans `task_id` ignorés).
- Docs : `docs/reports.md` étendu (contrat, statuts, tâche, validation 8 points), `docs/automation.md` et `core/rules/reporting.md` alignés, template projet `AGENTS.md` §3 à jour. 7 nouveaux tests (`TestReport`).

## 0.3.0 — 2026-09-25

- Présentation : uniquement à l'ouverture ou sur demande, jamais à chaque réponse ; contexte utilisé sans être réaffiché, interdiction de commencer par « En tant que ... pour le projet ... ».
- Typographie française exigée dans chaque fichier agent (accents, apostrophes, cédilles).

- `remove sayrhazi` interactif : simulation + confirmation `yes`, `--yes` direct pour scripts.

- Banner ASCII affiché par `sayrhazi` et `info sayrhazi` (`engine/banner.py`, version lue depuis `VERSION`, autres sorties gardées analysables).

- Nouvelle arborescence : `core/` (agents en trios + workflow + schemas + rules + templates), `runtimes/opencode/` (adaptateur : agents .md, templates, scripts), `engine/` (installer, checker, updater, renderer, resolver, state), `cli/` (install, check, update, remove, info), `tests/`, `pyproject.toml`, `VERSION`. `engine/renderer.py` régénère les adaptateurs depuis les trios (vérifié byte-identique, `--check`). 13 tests stdlib (`tests/test_core.py`, `test_cli.py`, `test_integration.py`). Recâblage complet des chemins ; install/update/check validés en non-régression.

## 0.2.4 — 2026-09-22

- Fix validation : `technical:` ajouté aux clés explicites de `validate` et `tunda` (une config sans section `technical` passait silencieusement).
- Doc : « 5 agents » → 6, et distinction explicite workflow prescrit vs transitions automatiques (`automation.md`, `README`).

- MCP Playwright pré-câblé dans `template/opencode.json` (local via `npx`, `enabled`, `deny` global conservé — seuls Ali et Zawadi en `allow`). Nouveaux projets QA-ready sans chemin machine en dur (`--executable-path` reste une surcharge locale, ex. Mpango).

- Nouvel agent **Ali** (UI/UX, `agents/ali.md`, bleu `#3B82F6`, vision + Playwright observation) : maquettes, écrans, composants, responsive, thèmes → `design.txt` (`PROPOSÉ`/`VALIDÉ` + `design-log.md`). Il pose les briques visuelles neuves, Bamse solidifie sans re-maquetter. Flux si visuel : `Lawibrahim → Ali → Bamse`, précédé de `Bamse (squelette)` sur projet vierge. Nom configurable via `agents.designer` (schéma, modèle, validateurs, migration auto).

## 0.2.3 — 2026-09-21

- Schéma fonctionnel sans dépendance : nouveau `scripts/sayrhazi_config.py` (mini-parseur YAML + vérificateur JSON-Schema, stdlib uniquement), branché sur `validate` et `tunda` (`[OK] schema conforme` ou violations `schema : $.chemin : ...`). `schemas/sayrhazi.schema.json` aligné sur le template (8 sections typées).

## 0.2.2 — 2026-09-20

- Scaffolding initial : Lawibrahim propose une structure type dans un projet vide (défaut `frontend/` + `backend/` pour une `application` TypeScript, variantes selon `project.type`, préférences demandées) ; Bamse ne scaffold qu'après validation dans `plan.txt`.
- Identité visuelle : couleur hexadécimale en frontmatter (`Lawibrahim #8B5CF6`, `Bamse #22C55E`, `Hadji #F59E0B`, `Hifadhui #EF4444`, `Zawadi #14B8A6`, format exigé par OpenCode), reprise dans `docs/agents.md` et le `README`.
- Config BDD clarifiée : `technical.database` scindé en `database_type` (moteur), `database_driver` (librairie d'accès) et `database_name` (nom ou `null`). Ton exemple : `postgresql` + `sequelize` + `matinma`.
- Règle français/emojis désormais explicite dans chaque fichier agent : français toujours, emojis proscrits dans le travail mais tolérés en blague (Bamse en affichait un en présentation malgré la règle commune).
- Nouveau `scripts/update-sayrhazi.py` : la mise à jour devient un script versionné et multi-plateforme (agents + watcher, config préservée, orphelins signalés, exit 2 si non installé). La fonction `update sayrhazi` délègue dessus.
- Migration douce : `update` ajoute les clés manquantes du template (`agents:`, `database:` → `database_type/driver/name` avec reprise de l'ancien nom), backup horodaté dans `history/`, valeurs existantes jamais écrasées.

## 0.2.1 — 2026-09-20

- Nouvelle commande `update sayrhazi` (fonction de profil) : réinstalle agents + watcher, affiche `version projet -> noyau`, signale les `agent/*.md` orphelins, relance le check. Config, rapports et historique préservés. Documentée dans `docs/installation.md`.
- `tunda sayrhazi` compare la version installée (`workflow.version`) au noyau : `[OK] version a jour` ou simple invitation `[WARN] ... lance 'update sayrhazi'`, jamais bloquant.

## 0.2.0 — 2026-09-19

- Système de nomination des agents : noms d'affichage configurables par projet via `agents:` (`architect`, `builder`, `reviewer`, `security`, `qa`) avec défauts publics ; fichiers `agent/*.md` et rôles stables, références croisées par rôle. `agents` désormais requis (schéma, modèle, validateurs).

## 0.1.8 — 2026-09-19

- `tunda sayrhazi` vérifie d'abord si Sayrhazi est initialisé (porte d'entrée : `sayrhazi.yaml` + `agent/` + `AGENTS.md`). Sinon, verdict `NON INSTALLE` (exit 2) avec consigne d'intégrer via `sayrhazi`, au lieu de noyer l'utilisateur dans la checklist.

## 0.1.7 — 2026-09-19

- `validate-sayrhazi.py` distingue échec d'installation (`Installation Sayrhazi echouee`) et configuration incomplète (`Sayrhazi installe avec succes. Configuration incomplete : N valeur(s)...`) vs succès complet (`Sayrhazi installe et configure avec succes`).

## 0.1.6 — 2026-09-19

- Règle commune : réponses et rapports en français (sauf demande contraire), aucun emoji. Ajoutée dans `agents/zawadi.md`, `template/AGENTS.md` (§2) et `docs/agents.md`.

## 0.1.5 — 2026-09-19

- Docs alignées sur l'état réel : présentation obligatoire (`agents.md`), arborescence avec `opencode.json` + `watch-work.py` (`architecture.md`), watcher actuel au présent (`automation.md`), install depuis n'importe quel projet + `tunda sayrhazi` (`installation.md`), format `task_id:` / `status:` exigé par le watcher (`reports.md`).

## 0.1.4 — 2026-09-19

- Template `sayrhazi.yaml` : `runtime.local_url` vaut désormais `http://localhost:5173` par défaut (modifiable, `null` si aucun serveur local) au lieu de `A_COMPLETER`. Ne s'applique qu'aux nouvelles installations, les configs existantes ne sont jamais écrasées.

## 0.1.3 — 2026-09-19

- Nouveau check optionnel `tunda sayrhazi` (`scripts/tunda.py` + fonction profil) : checklist ASCII point par point, 0 `A_COMPLETER` exige, alertes §4 et watcher.

## 0.1.2 — 2026-09-19

- `Install-Sayrhazi.ps1` : messages ASCII (fix mojibake PowerShell 5.1 sans BOM).
- `validate-sayrhazi.py` : sorties ASCII (fix caracteres bizarres en console Windows) + liste le nombre et les lignes `sayrhazi.yaml:<n>` de chaque `A_COMPLETER` restant.

## 0.1.1 — 2026-09-19

- Tous les agents se présentent avec `project.name`, type et stack lus dans `sayrhazi.yaml` ; suppression des noms en dur.
- Correction Zawadi : chemin `.opencode/resume/build.txt` et verdict toujours avec nom du projet.
- `template/AGENTS.md` réécrit comme vrai template projet ; règles du noyau déplacées vers `AGENTS.md` racine.
- `Install-Sayrhazi.ps1` copie désormais `opencode.json` ; PS + sh installent `watch-work.py` dans `.opencode/`.
- `watch-work.py` durci : détection racine robuste, déclenchement uniquement sur `task_id` + `status: TERMINÉ`, anti-double, vérif `review.txt`, modes `--check` / `--once`.

## 0.1.0 — 2026-09-15

- Création du noyau portable Sayrhazi.
- Ajout des rôles génériques Lawibrahim, Bamse, Hadji, Hifadhui et Zawadi.
- Ajout du modèle `sayrhazi.yaml`.
- Ajout des règles communes et du script d'installation.
- Clarification : Docker est optionnel et appartient au projet qui l'utilise, pas au workflow lui-même.
