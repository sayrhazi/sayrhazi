# Chat du dashboard : streaming en direct

## Objetif

Afficher la réponse de l'agent **token par token** dans le chat du dashboard
(`http://localhost:8363/chat`), comme le fait la TUI OpenCode.

Ce document décrit l'état actuel du chat (ce qui fonctionne, ce qui ne
fonctionne pas encore) et le plan d'implémentation du streaming. Il est
volontairement ancré sur des faits vérifiés dans OpenCode 1.19.0, pas sur des
suppositions.

## État actuel

| Besoin | État | Comment |
|---|---|---|
| Créer une discussion | fait | `POST /api/chat/session` → `POST /session?directory=…` (API native) |
| Renommage automatique du titre | fait | agent natif `title` d'OpenCode ; titre lu via l'événement `session.updated` |
| Supprimer une discussion | fait | `DELETE /api/chat/session/:id` → `DELETE /session/:id` (récursif sur les enfants) |
| Suivre le cycle de vie des discussions | fait | proxy SSE `GET /api/chat/session/event` → `GET /event?directory=…` |
| Indicateur « l'agent est en cours de réponse » | fait | événement `session.idle` + rafraîchissement de la discussion |
| **Affichage token par token** | **fait** | `GET /api/chat/stream?session_id=…` : le backend relie `/event?directory=…`, filtre par session, traduit chaque `message.part.updated` en événement `{kind:"part", messageID, partID, item}` via `part_to_item` et le front-end remplit le bloc agent en place. `session.idle` déclenche le rechargement complet (pied natif, durée de tour). |

> Implémenté le 2026-11-12. Le flux `/api/chat/stream` est techniquement identique à celui de la TUI (même source `/event`), la différence porte sur le format émis : Sayrhazi traduit les événements en un format interne stable, pas en événements natifs bruts.

Réponse à un message aujourd'hui : le message part en asynchrone
(`prompt_async`), l'indicateur s'affiche, puis la discussion est **rechargée
d'un bloc** quand la réponse est terminée. Le contenu n'apparaît pas au fil
de l'eau.

## Pourquoi le streaming ne fonctionne pas encore

Deux causes distinctes, toutes deux dues au passage à OpenCode 1.19.0.

### 1. Le format des événements a changé

OpenCode 1.19.0 émet `{id, type, properties}` :

```json
{"id":"evt_…","type":"message.part.delta",
 "properties":{"sessionID":"ses_…","messageID":"msg_…","partID":"prt_…",
               "field":"text","delta":"Bonjour"}}
```

Le gestionnaire SSE du dashboard (`handleSSEEvent`,
`runtimes/opencode/scripts/static/sayrhazi-server.js`) filtre sur
`data.session_id` et `data.event` : ce sont les champs de l'ancien format.
Tout ce qu'envoie OpenCode 1.19 est donc **ignoré**. Ce code est écrit pour
une génération d'événements qui n'existe plus ; il faut l'adapter ou le
remplacer, pas le compléter.

### 2. Le proxy pointe vers le mauvais flux

| Endpoint Sayrhazi | Endpoint OpenCode | Ce qu'il porte |
|---|---|---|
| `GET /api/chat/global/event` | `GET /global/event` | événements **serveur** seulement (`server.connected`, …) |
| `GET /api/chat/session/event` | `GET /event?directory=…` | **tous** les événements : sessions, messages, parts |

Les événements de message (`message.updated`, `message.part.updated`,
`message.part.delta`) sont sur `/event`, pas sur `/global/event`.

## Les événements natifs à consommer

Extraits de la spécification OpenAPI exposée par le serveur local
(`GET http://127.0.0.1:4096/doc`) et du binaire `opencode.exe` 1.19.0.

| Événement | Charge utile | Usage |
|---|---|---|
| `message.part.delta` | `{sessionID, messageID, partID, field, delta}` | **le token** (`field: "text"`) |
| `message.part.updated` | `{sessionID, part, time}` | état complet d'un part |
| `message.updated` | `{sessionID, info}` | le message ; `info.time.completed` = fin du tour |
| `message.part.removed` | `{sessionID, messageID, partID}` | part retiré |
| `session.created` / `session.updated` / `session.deleted` | `{sessionID, info}` | cycle de vie d'une discussion (déjà utilisé) |
| `session.idle` | `{sessionID}` | fin de tour : l'agent a fini |
| `session.error` / `session.status` | `{sessionID, …}` | erreur / état |

Le flux `/event` est **global au répertoire** (pas par session) : le proxy doit
filtrer avant de relayer, sinon le dashboard reçoit le bruit des autres
sessions du projet.

## La structure des parts

Un part est une ligne de la table `part` (colonne `data`, JSON) de la base
OpenCode.

| `type` | Contenu |
|---|---|
| `text` | `text` : le texte de la réponse |
| `reasoning` | `text` : le raisonnement (« Thought » de la TUI) |
| `tool` | `state` = `{status: pending\|running\|completed\|error, title, input, output, metadata, time{start,end}, error}` |
| `step-start` / `step-finish` | marqueurs d'étape, à ignorer |
| `file` | pièce jointe |

C'est **exactement** la structure que le backend convertit déjà en `items`
(`kind: reasoning` / `text` / `tool`) quand il relit une session
(`_handle_chat_session_messages`).

## Deux architectures possibles

### A — le front-end consomme les événements natifs

Le proxy `/api/chat/session/event` relaie déjà `/event` : il suffit d'écrire
un routeur d'événements 1.19 en JavaScript (`message.part.delta` → concaténer
le delta dans le nœud du part ; `message.part.updated` → créer ou remplacer
l'item ; `message.updated` avec `time.completed` → poser le footer ;
`session.idle` → retirer l'indicateur), plus un index `partID → nœud DOM`.

### B — le backend traduit vers un format « dashboard » (recommandé)

Le backend relaie `/event`, filtre sur la session demandée et réémet des
événements internes (`{kind:"text_delta", partID, delta}`,
`{kind:"part", item}`, `{kind:"done", duration}`) que le front-end sait déjà
consommer.

**Pourquoi B :** la conversion `part → item` existe déjà en Python dans
`_handle_chat_session_messages`. En A, il faudrait la réécrire en JavaScript
et on aurait **deux implémentations du rendu** susceptibles de diverger : le
DOM « live » (événements) et le DOM « après rechargement » (relecture des
parts en base). En B, les deux passent par le même code de conversion — il
suffit de factoriser `part_to_item(part)` et de l'utiliser aux deux
endroits. Le format de transport reste aussi indépendant des révisions
d'OpenCode.

## Plan d'implémentation (option B)

1. **Backend** — factoriser la conversion d'un part en item (extraction de
   `state`, `input`/`output`, diff, todos) dans une fonction réutilisable par
   la relecture et par le flux.
2. **Backend** — nouveau handler SSE `GET /api/chat/stream?session_id=…` :
   il s'abonne à `/event?directory=…` (même mécanisme que le proxy existant),
   filtre sur la session et ne relaie que les événements utiles.
3. **Backend** — accumulation des deltas par `partID` (le store natif fait
   de même avec sa table `part_text_accum_delta`) pour émettre des deltas
   propres, ou émettre le texte complet à chaque `message.part.updated`.
4. **Front-end** — un `EventSource` sur `/api/chat/stream`, un handler qui
   **remplace** l'item correspondant (jamais d'ajout), et à `session.idle` /
   `message.updated` avec `time.completed`, la pose du footer avec la
   **durée de tour** (règle déjà implémentée dans `_select_assistant_footers`).
5. **Nettoyage** — l'ancien code SSE (`handleMessageUpdated`,
   `handleSessionStatus`, `handlePermissionAsked`…) est écrit pour le format
   périmé : l'adapter ou le supprimer, pour éviter deux moteurs d'événements
   qui cohabitent.

## Pièges à anticiper

- **Coût du markdown** : re-parser le markdown à chaque token est coûteux →
  debounce (`requestAnimationFrame` ou ~100 ms) et ne re-parser que le bloc
  concerné.
- **Idempotence** : à la reconnexion SSE, OpenCode rejoue l'état → les mises à
  jour doivent être des **remplacements** par `partID`, pas des ajouts.
- **Outils** : un même part passe par `pending` → `running` → `completed` (et
  parfois `error`) ; `state.output` peut arriver après coup. Sans remplacement,
  la liste des outils grossit à chaque mise à jour.
- **Raisonnement** : les parts `reasoning` arrivent eux aussi en deltas.
- **Défilement** : ne pas forcer le scroll si l'utilisateur est remonté.
- **Charge** : `/event` est un flux par projet ; le relais doit rester filtré.
- **Reprise** : en cas de reconnexion, repartir d'un instantané — l'endpoint
  `GET /api/chat/session/:id/messages` existe déjà et fait foi.

## Granularité : deux niveaux possibles

- **(i) token par token strict** : on consomme `message.part.delta` et on
  n'affiche que le delta. C'est ce que fait la TUI.
- **(ii) rafraîchissement simple** : on renvoie le texte complet à chaque
  `message.part.updated` (c'est déjà ce que produit le rechargement, mais en
  direct). Beaucoup moins risqué, et l'essentiel du confort.

## Tests à écrire

- simulation front-end (Node, sans dépendance) : le texte apparaît
  progressivement, un outil ne duplique pas, le footer est posé une fois.
- test de bout en bout : `prompt_async` → le texte arrive en plusieurs
  fragments dans l'ordre, `session.idle` termine le tour.
- non-régression : le DOM « live » et le DOM « rechargé » sont identiques ;
  `watch-work.py`, les six agents, les rapports et l'historique ne bougent pas.

## Comment reproduire les faits de ce document

- Spécification locale : `GET http://127.0.0.1:4096/doc` renvoie l'OpenAPI
  3.1 de l'API (162 paths). Les paths utiles : `/session`, `/session/{id}`,
  `/session/{id}/message`, `/session/{id}/children`, `/event`,
  `/global/event`.
- Code natif : le binaire `opencode.exe` (npm `opencode-ai`) contient le
  bundle JavaScript ; les fonctions `ko()`, `Mo()`, `To()`, `ua()`,
  `SessionPrompt.ensureTitle` et `Session.remove` s'y trouvent en clair.
- Base locale : `~/.local/share/opencode/opencode.db` (tables `session`,
  `message`, `part`).

## Fichiers concernés

- `runtimes/opencode/scripts/sayrhazi-serveur.py` (proxy SSE, conversion des parts)
- `runtimes/opencode/scripts/static/sayrhazi-server.js` (réception, rendu)
- `runtimes/opencode/scripts/static/style.css` (indicateur d'activité)
- `tests/chat_frontend_sim.js` (simulation front-end, sans dépendance)
- `tests/chat_frontend_sim.js` (simulation front-end)
