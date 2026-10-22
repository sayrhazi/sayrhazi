# Rapports Sayrhazi

Les rapports courants se trouvent dans `.opencode/resume/`. Ils décrivent uniquement le dernier état utile à l'étape suivante. Avant remplacement, l'ancien contenu est ajouté au journal correspondant dans `.opencode/history/`.

| Fichier | Auteur | Fonction |
|---|---|---|
| `plan.txt` | Lawibrahim | décisions en cours et contexte validé |
| `frontend.txt` | Ali | état de l'implémentation frontend (scope `frontend`) |
| `backend.txt` | Bamse | état de l'implémentation backend (scope `backend`) |
| `review.txt` | Hadji | verdict qualité et corrections |
| `security.txt` | Hifadhui | verdict sécurité et réserves |
| `qa.txt` | Zawadi | observations réelles et verdict QA |
| `frontend.txt` | Ali | état de l'implémentation frontend (scope `frontend` ou étape frontend d'un `fullstack`) |
| `backend.txt` | Bamse | état de l'implémentation backend (scope `backend` ou étape backend d'un `fullstack`) |
| `fullstack.txt` | Ali + Bamse | artefact partagé d'une tâche `fullstack` : deux sections séparées (frontend par Ali, backend par Bamse), jamais d'écrasement mutuel |

Les journaux recommandés sont `build-log.md`, `design-log.md`, `review-log.md`, `security-log.md`, `qa-log.md`, `frontend-log.md`, `backend-log.md` et `fullstack-log.md`. `plan.txt` reste cumulatif jusqu'à l'archivage explicite d'une feature dans `.opencode/features/`.

## Artefact partagé `fullstack.txt`

`fullstack.txt` est utilisé uniquement par les tâches de scope `fullstack`. Il conserve les rapports d'implémentation d'Ali et de Bamse dans des sections clairement séparées :

* En-tête partagé : `task_id`, `scope` (doit valoir `fullstack`), `status`.
* Section `## Étape frontend — Ali` : `agent: frontend`, `stage_status`, `completed_at`, `summary`, etc. Seul Ali peut mettre à jour cette section.
* Section `## Étape backend — Bamse` : `agent: backend`, `stage_status`, `completed_at`, `summary`, etc. Seul Bamse peut mettre à jour cette section.

La mise à jour est non destructive : modifier une section préserve intégralement l'autre. Validateur : `engine/fullstack.py` (`read_fullstack`, `update_section`, `validate_fullstack`). `tunda`/`checker` signalent les violations sans bloquer.

## Etats de controle

Chaque controle de verification possede un etat explicite, declare dans `plan.txt` sous le champ `controls` (mapping nom_controle -> etat) :

* `OK` : le controle est passe avec succes.
* `KO` : le controle a echoue ; la cloture est bloquee.
* `Non requis` : le controle ne s'applique pas a cette tâche ; ne bloque pas la cloture.
* `En attente` : le controle requis n'a pas encore ete execute ; ne vaut jamais OK.

Les noms de controles connus : `review` (Hadji), `security` (Hifadhui), `visual_qa` (Zawadi), `integrated` (parcours complet).

Le champ optionnel `controls_required` liste les controles obligatoires pour la tâche. Un controle requis en attente ou marque `Non requis` est signale par `validate_task` et par `tunda`/`checker`. Un controle absent ou en attente ne vaut jamais OK.

## Contrat minimal d'un rapport

Cinq champs sont requis, sur leurs propres lignes (référence normative : `core/schemas/report.schema.yaml`, validateur : `engine/report.py`) :

```text
task_id: FEATURE-001
agent: build-back
status: TERMINÉ
completed_at: "2026-09-26T10:00:00+04:00"
summary: Fonctionnalité implémentée et vérifiée localement.
next_agents: [reviewer]
```

* `task_id` : identifiant stable de la tâche, recopié à l'identique dans chaque rapport (`FEATURE-001` ou `MPANGO-2026-014`, les deux formats acceptés). Il permet de vérifier que les rapports concernent la même tâche et évite de déclencher une étape sur un ancien fichier.
* `agent` : rôle stable de l'auteur (`architect`, `build-front`, `build-back`, `reviewer`, `security`, `qa`), jamais le nom d'affichage configurable.
* `status` : verdict autorisé par le rôle (voir ci-dessous).
* `completed_at` : horodatage ISO 8601 de fin de travail. L'ancien champ `version` comme horodatage est refusé (confusion avec `workflow.version`).
* `summary` : résumé d'une phrase. `next_agents` (optionnel) annonce les destinataires suggérés, sans déclencher quoi que ce soit à lui seul.

Au-delà du contrat, chaque rapport doit indiquer la date et l'heure système, le projet, la tâche examinée, les vérifications réalisées, les problèmes, les limites, les actions suivantes et le verdict autorisé par le rôle.

Les problèmes sont classés `CRITIQUE`, `HAUTE`, `MOYENNE` ou `FAIBLE`. Un rapport ne doit contenir aucun secret, token ou mot de passe.

## Statuts figés

`TERMINÉ` décrit l'achèvement d'un travail, `VALIDÉ` décrit un verdict : ne pas les mélanger.

* Lawibrahim (`plan.txt`, auteur `architect`) : `À IMPLÉMENTER`, `INFORMATIF`, `VALIDÉ` (décision prise, déclenche l'implémentation).
* Bamse (`backend.txt`) : `EN COURS`, `TERMINÉ` ou `BLOQUÉ`.
* Ali (`frontend.txt`) : `PROPOSÉ` ou `VALIDÉ`.
* Hadji (`review.txt`) et Hifadhui (`security.txt`) : `VALIDÉ`, `VALIDÉ AVEC RÉSERVES` ou `CORRECTIONS NÉCESSAIRES`.
* Zawadi (`qa.txt`) : mêmes verdicts, plus `À COMPLÉTER` lorsque l'observation réelle manque — ce verdict rappelle Bamse automatiquement (`rework_qa`, sauf `backend.txt` plus récent).

## Contrat minimal d'une tâche

Référence : `core/schemas/task.schema.yaml`, modèle : `core/templates/task.md`. Requis : `task_id`, `title`, `status` (`À IMPLÉMENTER`, `INFORMATIF`, `EN COURS`, `TERMINÉ`, `BLOQUÉ`, `VALIDÉ`). Recommandés : `agents_required` (rôles stables, source de vérité), `acceptance_criteria` (résultats observables : distinguent « étape terminée » de « fichier modifié »), et les raccourcis `security_required`, `visual_qa_required`, `design_required`, cohérents avec `agents_required` (ex. `security_required: true` impose `security` dans `agents_required`).

### Scope de tâche (Lot frontend/backend/fullstack)

Le champ optionnel `scope` classe la tâche parmi `frontend`, `backend` ou `fullstack`. Il détermine quels rôles sont attendus dans `agents_required` :

* `frontend` exige `build-front` (Ali) ;
* `backend` exige `build-back` (Bamse) ;
* `fullstack` exige `build-front` et `build-back`.

Un scope invalide est signalé par `validate_task` et par `tunda`/`checker`, sans bloquer l'installation. Absent, le scope est considéré non déclaré (comportement historique inchangé).

## Validation avant transition (8 points)

Avant de déclencher une étape, le moteur vérifie :

1. que le fichier attendu existe ;
2. qu'il est stable après écriture ;
3. que son `task_id` correspond à la tâche active ;
4. que son statut est autorisé pour cette transition ;
5. que l'auteur déclaré (`agent`) correspond au rôle attendu ;
6. qu'aucune exécution identique n'est déjà en cours ;
7. qu'un rapport de sortie équivalent n'est pas déjà présent ;
8. que le délai maximal n'est pas dépassé, si un délai est défini.

Le hash d'un fichier aide à détecter une modification mais ne prouve ni la tâche, ni la validité, ni l'autorisation : il n'est qu'un élément du contrat.

## Identité de tâche

Le watcher exige `task_id` présent et `status: TERMINÉ` (Bamse) avant de lancer Hadji, puis vérifie que `review.txt` mentionne le même `task_id`.
