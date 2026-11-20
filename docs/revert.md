# Revert / Undo natifs d'OpenCode

> Brouillon d'investigation. État au 2026-11-13, à partir d'OpenCode 1.19.0
> (port 4096, `/doc` OpenAPI). Aucune implémentation Sayrhazi engagée pour
> l'instant. À compléter après vérification TUI + test réel.

## Mission

Retrouver dans Sayrhazi le comportement natif « Revert - undo messages and
file changes » d'OpenCode.

Scénario : une conversation contient A, B, C, D, …, K. Cliquer sur Revert sur
C doit, conformément à la TUI :

- annuler les échanges postérieurs (D, E, …, K) ;
- retirer ces messages de la conversation active ;
- restaurer les fichiers modifiés par ces échanges ;
- remettre le message C dans la zone de saisie (éditable, configurable) ;
- OpenCode reprend alors la conversation depuis ce point.

Sayrhazi orchestre par HTTP, OpenCode gère la mécanique. Sayrhazi n'écrit pas
SQLite, n'invente pas de snapshots, ne restaure pas les fichiers.

## Contraintes architecturales

- OpenCode est la source de vérité (sessions, messages, snapshots, fichiers, snapshots).
- Sayrhazi = HTTP orchestration + présentation.
- Pas d'ajout de snapshots parallèles à Sayrhazi.
- Pas d'écriture directe dans la base OpenCode par Sayrhazi.
- Pas de Revert manuellement reprogrammé en Python (ni repli, ni raccourci).

## Endpoints natives trouvés dans OpenCode 1.19.0

(Question vérifiée par inspection de `/doc` le 2026-11-13 ; aucun appel
exécuté sur la session Sarhi.)

### Voie "legacy", un seul POST `/session/{sessionID}/revert`

```
POST /session/{sessionID}/revert
  ?directory=<path>                 (optionnel)
  body: { "messageID": "msg_..." | "msg_...", "partID": "prt_..." }
  réponses : 200 OK (Session mise à jour), 400 InvalidRequest,
             404 NotFound, 409 SessionBusyError
  description : "Revert a specific message in a session, undoing its effects
                and restoring the previous state."
```

### Voie "staged / v2", deux temps

Un Revert se fait en deux temps : `/stage` (annule rien, affiche le plan) puis
`/commit` (applique vraiment). Un `/clear` avant `commit` annule la mise en
scène, équivalent possible d'un « Redo ».

```
POST /api/session/{sessionID}/revert/stage
  body : { "messageID": "msg_...", "files?": bool }
  réponses : 200 OK + { "data": RevertState } | 400 / 401 / 404 / 500
  description : "Stage or move a reversible session boundary and optionally
                apply its file changes."
```

```
POST /api/session/{sessionID}/revert/clear
  body : (vide)
  réponses : 204 No Content | 400 / 401 / 404 / 500
  description : "Clear staged revert"
```

```
POST /api/session/{sessionID}/revert/commit
  body : (vide)
  réponses : 204 No Content | 400 / 401 / 404
  description : "Commit staged revert"
```

Un seul genre de session ne peut avoir qu'un seul revert staged à la fois
(State unique). `session.revert` sur la réponse `GET /session/{id}` porterait
ce marqueur (`{ "messageID": "msg_...", "partID"?: "prt_...", "snapshot": "...",
"diff": "..." }`).

### Undo / redo express

- `/session/{sessionID}/unrevert` (legacy) - "Restore all previously reverted messages in a session."
- `/api/session/{sessionID}/revert/clear` (v2) - annule la mise en scène (pas de commit).
- `/api/session/{sessionID}/revert/commit` (v2) - valide l'opération.

Aucun endpoint `/redo` textuel - la voie de redo naturelle est `commit`.

## `RevertState` schéma observable

```
{ messageID: "msg_…",
  partID?: "prt_…",
  snapshot: "<snapshot-id>",
  diff?:    "<diff-…>",
  files?:   [FileDiff] }
```

## Événements SSE rencontrés dans le schéma

Noms observés sous les `type`s :

- `session.next.revert.staged`
- `session.next.revert.committed`
- `session.next.revert.cleared`
- `sync.session.next.revert.committed.1`

Ces noms devraient apparaître sur `/api/chat/session/event` une fois un
Revert exécuté. Ils ne sont pas encore validés d'après la trace OpenCode
de Sarhi.

## Ce que Sayrhazi doit vérifier avant d'implémenter

### Deux architectures possibles

Voie one-step (legacy) :

Sayrhazi map -> POST /session/{id}/revert  avec body messageID.
Sayrhazi doit trouver le vrai `msg_…` OpenCode correspondant. Simple, opaque,
pas de notion de stage.

Voie staged :

- Sayrhazi appelle `/stage` pour détecter la cible et demander à OpenCode
  l'aperçu du Revert, via le `RevertState` retourné.
- Dit « Valider » → Sayrhazi appelle `/commit` (ou abandonne).
- Mieux adapté à la « Redo »/undo d'un revert non validé.

### Questions à fermer avant Phase 1→2

1. ** Sayrhazi conserve-t-il le vrai `msg_…` OpenCode sur ses messages ?**
   Sayrhazi affiche `message_id` synthétique (ex : id OpenCode) mais il faut
   vérifier que celui-ci est bien un vrai `msg_…` OpenCode + `partID` par part.
2. ** Faut-il la voie legacy ou staged ?**
   Deux modes possibles - décisifs pour les boutons undo/commit/clear.
3. **Quand et comment restaurer les fichiers ?**
   `files: bool` sur `/stage`. À vérifier si Sayrhazi appelle avec `true`
   (fichiers restaurés au moment du stage) ou `false` (restaurés au commit).
4. ** Sayrhazi branchement sur `/event`** pour suivre
   `session.next.revert.staged|cleared|committed`.
5. ** Sync avec la sidebar**. Aucun autre effet (suppression de session,
   renommage natif) ne doit être impacté par ces événements.

## Risques identifiés (multi-agent)

Sayrhazi distribue six agents opencode sur le même répertoire et partage la
même base OpenCode. Revert sur un message écrit par agent A peut restaurer
des fichiers touchés entretemps par agent B si un snapshot OpenCode antérieur
a été pris avant que B n'intervienne.

Sayrhazi ne peut pas (et ne doit pas) gérer les conflicts multi-agents
au-delà de ce que fait OpenCode nativement - notamment si deux agents alternent
sur le même fichier dans la même session. Sayrhazi doit documenter la
contrainte : « en cas de travail concurrent, un Revert peut produire un état
partiellement cassé ; le snapshot natif ne sait pas séparer les agents. »

## Prochaines étapes précises

1. Contester la reconnaissance du vrai `message_id` par Sayrhazi
   (`GET /api/chat/session/{id}/messages` - chercher `msg_`). Envisager au
   besoin de lever `message_id` plus haut, ou de les ré-extraire directement
   de la base opensource. Sayrhazi le fait déjà pour `seq`/`parent_id`.
2. Tester sur `Sarhi` : un Revert via curl (pas encore fait) :
   POST /session/{id}/revert avec `{"messageID": "msg_..."}` - observer
   comportement (200 / 409 / autre) avant code Sayrhazi.
3. Si voie staged retenue, trois routes Sayrhazi nécessaires :
   `/api/chat/session/:id/revert/stage`, `/clear`, `/commit` (proxy + pass-through).
4. Actions GUI : menu contextuel sur message utilisateur → Copier /
   Revenir à ce message. Confirmation avec mention pertes potentielles.
5. Tests : revert des derniers messages, après file edit, nouvelle session.
6. Unitaires - fenêtre chat (`/api/chat/session/:id/revert/*`).

## Historique

- 2026-11-13 - Investigation OpenAPI 1.19.0 (endpoints, schemas, events) consignée.
  Aucun changement Sayrhazi engagé. Le fichier `docs/automation.md.tmp` est un
  brouillon abandonné, à supprimer.
