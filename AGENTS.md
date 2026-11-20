# Règles de développement du dépôt Sayrhazi

Ce dépôt contient le noyau générique du workflow Sayrhazi. Il ne contient pas de code métier de projet utilisateur.

## Séparation des responsabilités

Les fichiers `core/`, `runtimes/`, `engine/`, `cli/`, `tests/` et `docs/` définissent le système portable. Les rapports d'une instance réelle (`plan.txt`, `design.txt`, `build.txt`, `review.txt`, `security.txt`, `qa.txt`) ne doivent jamais être ajoutés à ce dépôt.

Les règles destinées aux projets qui installent Sayrhazi se trouvent dans `runtimes/opencode/templates/AGENTS.md`. Ne pas confondre ce fichier avec les règles de contribution du présent dépôt.

## Modification du noyau

Avant toute modification, identifier le problème que la modification résout, vérifier son impact sur les installations existantes et mettre à jour la documentation concernée. Les agents génériques ne doivent pas contenir de nom de projet en dur.

Toute modification du contrat de configuration, des statuts, des rapports ou des chemins doit être répercutée dans le modèle, le validateur et la documentation. Toute modification du comportement public doit être inscrite dans `CHANGELOG.md`.

## Validation minimale

Avant un commit :

1. exécuter `python tests/test_core.py`, `python tests/test_integration.py` et `python tests/test_cli.py` ;

2. exécuter `python engine/checker.py` sur une instance de test complète ;
3. vérifier la syntaxe JSON avec un parseur JSON ;
4. vérifier les scripts shell avec `bash -n` ;
5. contrôler que les scripts d'installation ne suppriment jamais les rapports ni l'historique d'un projet ;
6. rechercher les références spécifiques à un projet dans `core/` et `runtimes/`.
7. Ne jamais faire un commit sans un accord explicite par le propriétaire.

## Sécurité des opérations Git

L'agent doit préserver en permanence les modifications locales du propriétaire et celles réalisées pendant la tâche en cours.

1. Avant toute opération Git susceptible de modifier l'état du dépôt, exécuter git status --short et identifier les modifications présentes.
2. Ne jamais exécuter git stash simplement pour lancer les tests, effectuer une vérification ou nettoyer temporairement le dossier de travail.
3. Ne jamais exécuter git stash, git stash pop, git stash apply, git stash drop ou git stash clear sans nécessité technique clairement identifiée et explication préalable au propriétaire.
4. Ne jamais utiliser git reset --hard, git clean -fd, git checkout -- . ou git restore . pour supprimer ou remplacer des modifications locales.
5. Ne jamais écraser, supprimer ou remplacer silencieusement des modifications existantes, qu'elles proviennent du propriétaire ou de l'agent.
6. En cas de conflit entre des modifications locales et une opération Git, interrompre l'opération et expliquer le conflit avant toute action destructive.
7. Après toute opération ayant un impact sur l'état du dépôt, vérifier le résultat avec git status.
8. Les tests doivent être exécutés directement sur l'état courant du projet. Un stash n'est pas une étape normale de validation.
9. Aucun commit, push, amend, rebase ou autre réécriture de l'historique ne doit être effectué sans autorisation explicite du propriétaire.

En cas de doute sur la conservation des modifications, privilégier l'arrêt de l'opération plutôt qu'une tentative automatique de nettoyage ou de restauration.

## Versionnement

1. Lis le fichier `engine/bump_version.py` à chaque fois qu'une nouvelle version sera écrite dans le fichier `CHANGELOG.md` pour mettre à jours tous les fichiers concernés pour afficher la version.
2. Dans `CHANGELOG.md`, séparez le numéro de version de la date par un trait d'union simple `-` (pas de trait cadratin `-`) : `## X.Y.Z - AAAA-MM-JJ`. `engine/bump_version.py` ne lit que la première section `## X.Y.Z` ; après l'avoir ajoutée, lancez `python engine/bump_version.py` pour aligner `VERSION`, `pyproject.toml`, `package.json`, `package-lock.json`, le template `sayrhazi.yaml` et la bannière `README.md`.
3. Les commits suivent les Conventional Commits : `type(portée): objet` en minuscules, sans version dans le message. Types : `feat`, `fix`, `docs`, `test`, `refactor`, `chore`. Portées : `core`, `engine`, `cli`, `runtimes`, `docs`, `tests`, `watcher`. Exemple : `feat(watcher): moteur générique piloté par workflow.yaml`. Les versions vivent uniquement dans `VERSION`, `CHANGELOG.md`, les tags Git (`v0.1.0`) et les releases GitHub.
