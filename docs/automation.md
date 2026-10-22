# Automatisation de Sayrhazi

## Principe

Le cœur de Sayrhazi fonctionne manuellement. L'automatisation est une extension facultative destinée à réduire les transitions répétitives, pas à remplacer les décisions de l'utilisateur.

> Sayrhazi ne doit pas décider à la place de l'utilisateur ; il doit empêcher que le workflow oublie une étape prévue.

Le moteur applique une règle explicite, il ne choisit pas un agent librement :

```text
un rapport identifié
+ un statut autorisé
+ une condition satisfaite
+ une transition déclarée
= un agent éligible
```

Le mode initial reste supervisé (`auto_start: false`) : les transitions techniques prévues peuvent être automatiques, la création ou le lancement d'une tâche importante reste soumis à validation humaine, le déploiement, la suppression et les migrations de données restent toujours soumis à validation humaine, et tout blocage ou ambiguïté est signalé plutôt que deviné.

Les terminaux sont des interfaces d'observation et d'intervention, jamais la mémoire du workflow : l'état indispensable à la reprise vit dans des fichiers projet, lisible après fermeture de l'IDE, arrêt de la machine ou absence prolongée.

Les agents conditionnels ne sont jamais invoqués systématiquement : jamais de parcours automatique `Bamse → Hadji → Hifadhui → Zawadi → Ali`. Chaque tâche déclare ses rôles nécessaires (ex. interface : Bamse/Hadji/Zawadi ; authentification : Bamse/Hadji/Hifadhui sans Zawadi).

## Transitions automatisables cibles vs état actuel

Cible (seulement si déclarées et vérifiées, Lots 1-4) :

```text
plan validé → Bamse
build terminé → Hadji
build terminé + security_required → Hifadhui
build terminé + visual_qa_required → Zawadi
plan validé + design_required → Ali
review avec corrections → retour vers Bamse
plan validé + scope:fullstack → Ali (section frontend de fullstack.txt)
section frontend TERMINÉ + scope:fullstack → Bamse (section backend de fullstack.txt)
```

Restent toujours humains : décision finale Lawibrahim/utilisateur, lancement d'une tâche importante en mode supervisé, changement de périmètre ou de critères d'acceptation, déploiement, migration ou suppression de données, correction à solutions multiples, blocage ou incohérence non déterministe.

Une première automatisation raisonnable consiste à surveiller `backend.txt` et à lancer Hadji lorsque Bamse a produit un rapport complet. Le script doit vérifier un statut explicite et un identifiant de tâche ; une simple modification de fichier ne suffit pas.

## Workflow prescrit vs transitions automatiques

Ne pas confondre les deux niveaux :

```text
WORKFLOW PRESCRIT (conceptuel, à orchestration humaine) :
Lawibrahim → Ali → Bamse → Hadji → Hifadhui → Zawadi → Terminé

TRANSITIONS AUTOMATIQUES ACTUELLES (réel, watch-work.py) :
backend.txt TERMINÉ + task_id → Hadji → vérifier review.txt
```

Seule la transition `backend.txt → Hadji` est automatisée. Hifadhui, Zawadi et le retour vers Bamse restent déclenchés manuellement : le moteur n'empêche pas toutes les transitions invalides, ce sont les contrats documentaires (rapports, statuts, `task_id`) qui les cadrent.

## État persistant et reprise

Les terminaux ne sont pas la mémoire du workflow : `.opencode/state/workflow-state.yaml` est la vue de coordination (`task_id`, statut, étapes `current/completed/pending/blocked`, agents en cours). Les rapports restent les preuves détaillées. Le dossier `.opencode/state/` est local : à ignorer par git (reprise mono-poste, pas de bruit).

Au redémarrage (ou à tout moment), réconcilier avant de reprendre :

```powershell
python engine/state.py --reconcile --project .
```

La réconciliation lit l'état, relit `workflow.yaml`, vérifie les rapports du `task_id` actif, ne retient que les étapes éligibles, ne relance jamais une étape terminée (sauf retour `CORRECTIONS NÉCESSAIRES` ou `qa.txt À COMPLÉTER` vers l'implémentation) et signale toute incohérence au lieu de la deviner. `tunda sayrhazi` contrôle la présence et la validité de l'état sans bloquer. Le watcher lit la config et met à jour l'état minimal + le journal (la réconciliation complète reste `engine/state.py --reconcile`).

## Moteur (`runtimes/opencode/scripts/watch-work.py`, installé dans `.opencode/`)

Le watcher est devenu un moteur piloté par `.opencode/workflow.yaml` : pour chaque étape `auto: true`, il vérifie rapport stable, `task_id` + statut autorisés, condition satisfaite et sortie non existante, puis appelle l'agent du rôle (prompt, minuteur, durée, vérification du rapport produit). Verrou d'instance unique, anti-double en mémoire + sortie existante (point 7), journal `history/workflow-log.md`, mise à jour minimale de `state/workflow-state.yaml`. Sans `workflow.yaml` valide, repli sur la transition historique `backend.txt TERMINÉ → Hadji`, sans jamais planter.

Les conditions se résolvent depuis `sayrhazi.yaml` (`visual_qa_required` → Zawadi, `security_audit_required_by_default` → Hifadhui ; `design_required` sans indicateur : Ali reste manuel). Sont automatiques : `review` (toujours), `security` et `visual_qa` (si requis), `rework` (retour vers Bamse sur `CORRECTIONS NÉCESSAIRES`, sauf `backend.txt` plus récent), `rework_qa` (retour vers Bamse sur `qa.txt À COMPLÉTER` : Zawadi attend une action — purge cache, relance frontal, fix env — puis recontrôle). Pour les tâches `fullstack`, l'étape `backend` est verrouillée tant que la section `frontend` de `fullstack.txt` n'est pas `TERMINÉ` : Bamse ne démarre pas avant qu'Ali n'ait terminé sa partie. Étapes inconnues ou cassées : signalées et ignorées, jamais devinées. `tunda sayrhazi` affiche les agents requis, manuels et inactifs de la configuration.

Les étapes éligibles ensemble partent en parallèle (threads stdlib), bornées par `workflow.max_parallel_agents` (défaut 3, garde-fou machine). La convergence est journalisée (`Convergence : review OK, security OK (2/2)`) : un échec (timeout, erreur) n'empêche jamais les autres branches, qui écrivent chacune leur rapport ; la branche échouée sera réévaluée au prochain changement. `running_agents` reflète l'ensemble en cours. Séquentiel si une seule étape éligible, repli historique sinon.

## Sessions : une par appel, OpenCode responsable

Le watcher n'ouvre **jamais** de session partagée. Chaque appel est un `opencode run` sans `--session` : une session neuve, que le watcher ne réutilise pas et ne réinjecte pas. Conséquence : OpenCode garde la main entière sur le contexte (cache, compaction, saturation), le watcher ne fait pas son travail.

Chaque appel titre sa session de façon déterministe et unique :

```
Sayrhazi <task_id> <agent> <run_id>     ex. Sayrhazi FEATURE-007 hadji 20261015-120000-1a2b
```

Le `run_id` (horodatage + suffixe) distingue deux appels successifs du même agent sur la même tâche. Le titre est la **seule** chose que le watcher produit pour l'observation ; il ne lit pas la base d'OpenCode, ne calcule aucun pourcentage, ne fixe aucun seuil, ne déclenche ni rotation ni compaction. Le modèle passé à chaque appel (`--model`) reste celui du frontmatter de la fiche agent : changer de modèle impose d'éditer la fiche, jamais un paramètre d'exécution.

La mémoire d'un agent est dans ses rapports (`.opencode/resume/`, archivés dans `history/`), pas dans un historique de conversation réinjecté. Le cloisonnement d'écriture reste exigé à chaque appel : l'agent ne produit QUE son rapport (lire les autres est utile, les écrire est interdit, archivage vers `history/` autorisé).

`state/workflow-state.yaml` ne contient plus de bloc `session:` : l'état ne décrit que la coordination (tâche, statut, étapes, agents en cours). Un état plus ancien peut encore contenir ce bloc ; il est accepté, jamais exigé, jamais réécrit.

### Pourquoi ce changement

Jusqu'à 0.12.x, le watcher réutilisait `--session` par (tâche, agent) et estimait la saturation avec `tokens_input + tokens_output + tokens_cache_write`, en **excluant `tokens_cache_read`**. Cette exclusion était fausse : la relecture de cache fait partie du contexte renvoyé à chaque appel et en représente souvent la majeure partie. La jauge tombait donc à ~0 % pendant que le panneau Context d'OpenCode affichait la valeur réelle. Retirer tout le mécanisme est plus sûr que de le corriger.

## Dashboard (`watch-tui.py`)

Le dashboard observe, il ne pilote rien. Pour chaque agent il liste **toutes** ses sessions sur le projet et distingue les deux origines :

* **Auto** — le titre porte le préfixe `Sayrhazi <task_id> <agent> `, donc la session a été ouverte par le watcher ;
* **Manu** — titre libre, session ouverte à la main depuis un terminal (Lawibrahim, Bamse désactivé, Ali, tout appel direct).

Un agent manuel consomme autant de contexte qu'un appel automatique, il est donc affiché au même titre. Le filtre reste strict sur le projet et l'agent : jamais la session d'un autre agent ou d'un autre projet ; à défaut, `n/d`.

La ligne affiche la session **la plus récemment active** de l'agent, quelle que soit son origine.

« La plus récemment active » signifie la plus grande `session.time_updated`, **pas** la plus grande `time_created`. Une session manuelle longue peut avoir été ouverte bien avant une session automatique tout en restant celle que l'utilisateur regarde ; comparer les dates de création affichait alors la session automatique périmée et un contexte qui ne correspondait à aucun terminal ouvert.

### Deux blocs, deux portées

La ligne sépare visuellement deux groupes de colonnes, parce qu'ils ne mesurent pas la même chose. Les confondre laissait croire qu'un compteur d'historique décrivait la session affichée.

**Bloc session** — `Session`, `Origine`, `Jauge`, `Tokens`, `%`, `Coût sess.`

Décrivent **uniquement** la session affichée, celle dont l'ID est donné dans la ligne. Quand la vague suivante ouvre une session neuve, ces valeurs changent avec elle et suivent le nouvel ID. Elles ne sont jamais sommées d'une session à l'autre : additionner des fenêtres de contextes différents n'a pas de sens.

**Bloc projet** — `Auto`, `Manuel`, `Coût proj.`

Agrègent **toutes** les sessions de cet agent sur ce projet, toutes tâches confondues. `Auto = 2` signifie « deux appels du watcher depuis le début du projet », pas « deux appels dans la session affichée ». Pour une facture, l'addition a un sens ; pour un contexte, jamais.

* **Contexte du dernier message** — reproduit la formule du panneau Context d'OpenCode : dernier message assistant **portant une mesure**, `input + output + reasoning + cache.read + cache.write`, divisé par la fenêtre de contexte du modèle. Ce pourcentage décrit ce message, pas le cumul de la session.

Un message assistant dont le contexte vaut zéro n'est pas une mesure : le dashboard le saute. Deux formes existent dans la base d'OpenCode — le message ouvert au début d'un run (`finish` absent) et un tour interrompu (`finish: "unknown"`, chaîne non vide, qu'un simple test sur la présence de `finish` ne peut pas écarter). Le dashboard garde alors la dernière valeur réelle au lieu de retomber à zéro pendant l'exécution.

La compaction d'OpenCode n'a pas de cas particulier : elle est marquée par un part `compaction` attaché à un message **user**, jamais à un message assistant. Le dashboard n'y voit qu'une baisse de contexte et affiche normalement la nouvelle valeur.

Aucun pourcentage cumulatif n'est affiché : additionner des fenêtres de sessions différentes n'a pas de sens.

OpenCode ne stocke nulle part la fenêtre de contexte d'un modèle (ni en base, ni dans sa configuration). Sayrhazi la lit dans `core/runtime/models.yaml`, installé dans `.opencode/models.yaml` et surchargeable par projet. Modèle absent du registre → `n/d` : mieux vaut une métrique absente qu'un pourcentage silencieusement faux ; tokens et coût restent affichés. `tunda sayrhazi` contrôle la présence du registre, de `watch-tui.py` et de la dépendance `textual`, et nomme la commande d'installation adaptée à la plateforme (`pip` / `pip3`).

### Panneau de détail

Sous le tableau, un panneau suit la ligne sélectionnée (flèches du clavier ou clic, aucune touche supplémentaire). Il reprend ce que la ligne ne peut pas contenir faute de place : modèle et fenêtre, coût de session, horodatages, et surtout la **décomposition du contexte** telle qu'OpenCode l'a comptée :

```text
modèle    opencode/muse-spark-1.3-contributor-free  ·  fenêtre 1 048 576
contexte  572 128 tokens =  55 % de la fenêtre
          input 715  cache.read 570 993  cache.write 0  output 420  reasoning 0
```

La décomposition n'est pas décorative : `cache.read` domine presque toujours. Sur les sessions réelles de Sayrhazi-web, il représente 99 % du contexte de Bamse (570 993 sur 572 128) et 98 % de celui de Hadji (51 441 sur 52 282). Sans cette ligne, « 55 % de fenêtre » laisse croire que l'agent a produit 572 K tokens ; il n'a quasi rien produit et relu le projet — deux coûts très différents.

Un agent sans mesure affiche `n/d` partout : aucun pourcentage, aucune durée, aucun coût ne sont inventés.

La sélection survit au rafraîchissement. Le tableau est reconstruit à chaque tick, ce qui réinitialisait le curseur sur la première ligne : cliquer sur hadji perdait la surbrillance en une seconde. L'agent sous le curseur est mémorisé **par son rôle** et restauré après reconstruction, pas par index, pour survivre à tout réordonnancement des lignes.

En cas de timeout, le watcher vérifie si le rapport a quand même été produit (le processus peut rester bloqué après l'écriture, ex. serveur de prévisualisation laissé actif) : rapport présent → succès acté, sans retry. Les retries réseau/timeout sont à un seul coup (pas de réveil permanent) et la ligne `État :` ne s'affiche qu'en cas de changement — fini le spam toutes les 3 secondes.

## Reprise après interruption (`--continue`)

Un Ctrl+C au repos écrit un marqueur d'arrêt propre (`.opencode/state/clean_stop`) ; un arrêt brutal (terminal fermé, processus tué) n'en laisse pas. Relancé avec `--continue`, le watcher diagnostique : marqueur présent/absent, agents retrouvés en cours dans l'état (purge), appels sans terminaison au journal, pause quota enregistrée. Puis la boucle normale reprend le travail inachevé (rapports et point 7). Sans le flag, le démarrage reste silencieux comme avant.

La console colore les mots importants (agents, `task_id`, pourcentages, `ERREUR`/`ATTENTION`) et affiche le modèle de chaque agent appelé (`[muse-spark-1.3]`, lu dans le frontmatter de `.opencode/agent/*.md`) ; le journal fichier reste en texte brut, avec le nom complet du modèle. Pour la lisibilité, chaque vague de déclenchements est encadrée (`########## vague : … ##########`) et la sortie de chaque agent est encadrée (`============ AGENT · … ============` … `============ FIN · … ============`).

## Services séparés

Les agents produisent des rapports ; les serveurs (`yarn dev` × 2) sont des processus persistants du projet, gérés par `runtimes/opencode/scripts/services.py` (installé dans `.opencode/`, jamais mélangé au moteur) : `start` (refusé si port occupé), `stop` (arbre complet), `status`, `logs`, `restart` explicite uniquement. Santé vérifiée par HTTP, logs vers `history/services-<nom>.log`, PID dans `state/`. Redémarrage jamais automatique : un arrêt peut être volontaire. Les agents utilisent `runtime.local_url` tel quel et ne (re)démarrent ni ne tuent jamais un serveur : un serveur absent est une limite signalée, pas une action.

Modes utiles : `--check` (valide la config sans surveiller), `--once` (une seule vérification, exit `0` si déclenché), `--timeout SEC`.

## Rework ciblé et invalidation des verdicts

Dès qu'un fichier examiné par un contrôle est modifié, le verdict associé
devient périmé (`En attente`) jusqu'à sa nouvelle exécution. Les contrôles
non affectés conservent leur état.

Le rework est routé vers l'agent responsable du périmètre :
- contrôle `review` (qualité globale) -> Lawibrahim (architect)
- contrôle `security` (sécurité backend) -> Bamse (build-back)
- contrôle `visual_qa` (rendu frontend) -> Ali (build-front)
- contrôle `integrated` (parcours complet) -> Lawibrahim (architect)

Outil : `engine/rework.py` (`invalidate_controls`, `rework_role`, `controls_to_replay`, `is_rework_needed`).
Les périmètres de fichiers par contrôle sont definis dans `CONTROL_SCOPES` et surchargeables par projet.

## Instructions des agents et scopes

Les fiches agents (`core/agents/*/instructions.md`, régénérées dans `runtimes/opencode/agents/*.md` par `engine/renderer.py`) ont été adaptées aux trois scopes :

* **Lawibrahim (architect)** : déclare `scope` (`frontend`/`backend`/`fullstack`), `agents_required` cohérent, `controls_required` et `controls` (états `OK`/`KO`/`Non requis`/`En attente`) dans `plan.txt`.
* **Ali (build-front)** : scope `frontend` → écrit `frontend.txt` ; scope `fullstack` → met à jour uniquement la section frontend de `fullstack.txt`, jamais celle de Bamse. Rapport `agent: frontend` (identité OpenCode `agent: build-front`, alias `designer` pour retrocompatibilite).
* **Bamse (build-back)** : scope `backend` → écrit `backend.txt` ; scope `fullstack` → démarre uniquement si la section frontend d'Ali est `TERMINÉ`, met à jour uniquement sa section backend. Rapport `agent: backend` (identité OpenCode `agent: build-back`, alias `builder` pour retrocompatibilite).
* **Hadji (reviewer)** : rend le verdict qualité final (`VALIDÉ` / `VALIDÉ AVEC RÉSERVES` / `CORRECTIONS NÉCESSAIRES`). En `fullstack`, examine `fullstack.txt` après que les deux sections sont `TERMINÉ`.
* **Hifadhui (security)** : audite le backend si requis ; son verdict KO bloque la clôture et l'archivage.
* **Zawadi (qa)** : teste après Ali (frontal testable) et/ou après Bamse (parcours intégré) selon le plan ; verdict `À COMPLÉTER` si le rendu n'est pas observable.

## Archivage fullstack

Une tâche `fullstack` est archivée par Lawibrahim uniquement après OK global :
- les etapes frontend et backend sont terminees ;
- tous les controles requis declares sont OK ;
- le verdict final de Hadji est OK.

L'ecriture se fait dans `.opencode/history/fullstack-log.md` (cumulatif, une entree par `task_id`).
L'archivage est idempotent : le `task_id` sert de cle d'unicité, aucune entree dupliquee n'est ajoutee
meme si le watcher redemarre. La reinitialisation de l'artefact courant `fullstack.txt` n'intervient
qu'apres verification que l'archive est complete et enregistrée.

Outil : `engine/archive.py` (`archive_fullstack`, `is_archived`, `validate_archive_conditions`).
`tunda`/`checker` signalent les violations sans bloquer.

## Limites connues

L'approche par hash + délai seule ne prouve pas que le rapport est terminé ni que `review.txt` a été produit : c'est pourquoi les contrôles ci-dessus sont obligatoires et le watcher ne doit pas être bricolé en version hash-seul.

Les problèmes déjà rencontrés concernent les permissions en mode headless, les fichiers créés hors du projet et l'interruption d'une session après de nombreuses étapes d'outils. L'orchestrateur doit travailler depuis la racine du projet, utiliser les chemins du projet, vérifier les droits d'écriture et enregistrer les erreurs.

## Périmètre recommandé

Commencer par une seule transition :

```text
backend.txt : TERMINÉ → lancer Hadji → vérifier review.txt
```

Ne pas lancer automatiquement Bamse, ne pas boucler automatiquement après une review négative et ne pas publier, supprimer ou modifier des éléments sensibles. Les corrections, conflits de rapports et clôtures importantes restent sous contrôle humain.

## Conditions pour une automatisation fiable

Chaque rapport doit contenir `task_id`, `status`, `agent`, `completed_at` (ISO 8601, jamais `version`) et `summary`, plus éventuellement `next_agents` (voir `docs/reports.md` et `core/schemas/report.schema.yaml`). L'orchestrateur doit attendre que le fichier soit stable, refuser les statuts incomplets, vérifier que l'auteur déclaré correspond au rôle attendu, empêcher les doubles déclenchements, appliquer un timeout et signaler les erreurs au lieu de les masquer.

Un agent superviseur doté de raisonnement n'est pas nécessaire pour ces transitions déterministes. Il ne devrait être envisagé qu'après stabilisation d'un orchestrateur scripté et seulement pour les cas ambigus.
