# Architecture de Sayrhazi

## Rôle du système

Sayrhazi est un workflow multi-agents portable. Il organise le cycle de développement autour de rôles spécialisés, de rapports courts et d'un historique local au projet. Sayrhazi n'est pas une application métier ni un service métier ; son serveur web (tableaux de bord, conversations) est un outil de travail indépendant, démarré par `sayrhazi server`. Sayrhazi n'impose ni base de données côté projet, ni Docker.

Le noyau est versionné séparément des applications qui l'utilisent. Une application installe une copie locale des agents et des modèles dans son propre dossier `.opencode/`.

## Deux niveaux

Le dépôt Sayrhazi contient les éléments réutilisables : agents, modèles, schéma, scripts et documentation. Le projet utilisateur contient son code, ses règles locales, sa configuration `sayrhazi.yaml`, ses rapports et son historique.

```text
Sayrhazi/
├── core/           concepts (agents, workflow, schemas, rules, templates)
├── runtimes/       adaptateur opencode (agents .md, templates, scripts)
├── engine/         opérations (installer, checker, updater, renderer, resolver, state, agent_sync)
├── cli/            commandes (install, check, update, remove, info, server)
├── tests/          non-régression stdlib
├── docs/           documentation
├── AGENTS.md       règles de contribution au noyau
├── CHANGELOG.md    versions
└── VERSION         version courante

Projet/
├── AGENTS.md
└── .opencode/
    ├── agent/             6 agents installés
    ├── sayrhazi.yaml      configuration locale
    ├── opencode.json      permissions locales
    ├── workflow.yaml      parcours déclaratif piloté par le watcher
    ├── watch-work.py      orchestrateur du workflow
    ├── sayrhazi-modules.py briques partagées (lecture OpenCode + handler web)
    ├── sayrhazi-serveur.py serveur unifié (dashboard web + watcher)
    ├── models.yaml        fenêtres de contexte connues (dashboard)
    ├── static/            fichiers du dashboard web (index.html, style.css, sayrhazi-server.js…)
    ├── resume/
    ├── history/
    └── features/
```

## Interfaces du projet

* **Dashboard web** (servi par `sayrhazi-serveur.py` sur le port **8363**) — état du workflow, rapports d'agents détaillés, conversations avec les agents via l'API native d'OpenCode (création/suppression/renommage automatiques du titre, streaming token par token, indicateurs d'exécution, copie du message utilisateur).
* **Watcher** (`watch-work.py`) — pilote le parcours déclaratif décrit par `workflow.yaml` (déclenchements automatiques selon les rapports produits).

Les rapports des agents, l'historique et la configuration restent dans le projet : le noyau ne lit ni n'écrit ailleurs.

## Rôles

Lawibrahim clarifie le besoin et consigne les décisions validées. Ali maquette l'UI quand la tâche est visuelle. Bamse implémente et vérifie le code. Hadji contrôle la qualité générale, sans remplacer l'audit sécurité. Hifadhui examine les risques de sécurité. Zawadi vérifie le comportement réel et l'expérience visuelle lorsqu'un navigateur ou des captures sont disponibles.

Les rôles sont séparés pour éviter l'auto-validation. Les agents de review, sécurité et QA analysent et rapportent ; ils ne modifient pas le code source.

## Projet courant

Le nom du projet n'est jamais codé dans les agents. Il est lu dans `.opencode/sayrhazi.yaml`. Les règles techniques et métier spécifiques restent dans le `AGENTS.md` du projet utilisateur.
