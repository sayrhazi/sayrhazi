# Sessions OpenCode du watcher — principe et contrat

> Document de travail. Aucune implémentation engagée tant que ce document n'est
> pas validé point par point. Décisions issues des échanges Sayrhazi 2026-11-14.

## Principe non négociable

**OpenCode est l'unique autorité sur les métriques du contexte.** Sayrhazi ne
calcule, ne reconstruit et ne déduit aucune métrique déjà fournie par
OpenCode. Sayrhazi consomme exclusivement les données exposées par OpenCode
pour appliquer sa propre politique de rotation des sessions.

Si OpenCode ne fournit pas une donnée, Sayrhazi ne l'invente pas : il signale
que l'information est indisponible et applique une politique de sécurité
définie (rotation conservatrice), plutôt que de prendre une décision sur une
valeur estimée.

## Séparation des responsabilités

| Élément | Responsabilité |
|---|---|
| OpenCode | Fournir l'utilisation réelle du contexte (tokens, limites, usage %, compaction, sessions) |
| Sayrhazi | Définir sa propre politique de rotation (seuil configurable) |
| watch-work.py | Décider quand créer une nouvelle session et l'exécuter |

## Ce que Sayrhazi ne doit surtout pas faire

- Recalculer les tokens utilisés.
- Recalculer la fenêtre contextuelle.
- Déduire la limite du modèle.
- Estimer le pourcentage d'utilisation.
- Reproduire la logique de compaction d'OpenCode.
- Inventer une interprétation des données internes du modèle.

Même si ces calculs semblent simples, ils risquent de diverger de ceux
d'OpenCode.

## Ce que Sayrhazi doit faire

OpenCode expose les données natives (tokens, limites, usage %, compaction,
sessions). Sayrhazi les consomme via les fonctions déjà présentes dans
`sayrhazi-modules.py` :

- `last_assistant_message` — dernier message assistant avec tokens
- `message_context_tokens` — calcul `input + output + reasoning + cache.read + cache.write`
  (miroir exact de la formule d'OpenCode, consommation et non invention)
- `load_model_limits` — fenêtre de contexte depuis le registre `models.yaml`
  (config statique Sayrhazi, pas une métrique OpenCode)

Sayrhazi compare le résultat au seuil configurable et décide de la rotation.
watch-work.py exécute la décision.

## Stratégie : anticiper, pas subir

**Seuil initial : 85 %** (configurable via `session.max_usage` dans
`sayrhazi.yaml`, déjà lu par le watcher historique).

Deux mécanismes indépendants, l'un ou l'autre déclenche la rotation :

1. **Primaire** : `time_compacting` change chez OpenCode → nouvelle session
   (compaction détectée, le contexte a été résumé).
2. **Secondaire** : usage ≥ 85 % → nouvelle session (sécurité, avant que la
   compaction n'intervienne).

La compaction d'OpenCode devient une information secondaire, non une condition
nécessaire à la rotation. Sayrhazi n'attend pas la compaction d'OpenCode pour
tourner.

## Gestion de l'agent en cours de travail

La rotation ne se décide jamais pendant l'exécution (l'agent est synchrone
via `opencode run`), mais **après** que l'agent a terminé. Deux conditions
cumulatives :

1. L'agent doit avoir terminé (pas dans `running_agents`, pas de
   `session_status: busy` chez OpenCode).
2. ET le dernier message assistant montre ≥ 85 % OU `time_compacting` a
   changé depuis la dernière vérification.

Si l'agent est encore en cours → on ne touche à rien, même si l'usage dépasse
85 %. On attend qu'il finisse, puis on vérifie.

## Fichier `watcher-state.yaml`

```yaml
# .opencode/state/watcher-state.yaml
# État des sessions réutilisées par le watcher (interne, ignoré par git).
# Réinitialisé automatiquement quand la tâche change.
watcher:
  task_id: "FEATURE-008"
  sessions:
    lawibrahim:
      session_id: "ses_e25548dc8ffeWzqPhhz7s2wLxq"
      created_at: "2026-11-14T17:30:12+01:00"
      last_usage_pct: "42 %"
      last_checked: "2026-11-14T17:32:05+01:00"
      rotation_pending: false
    ali:
      session_id: "ses_e24cbf9cdffeEBsCrHISy1JBR3"
      created_at: "2026-11-14T17:30:14+01:00"
      last_usage_pct: "12 %"
      last_checked: "2026-11-14T17:32:05+01:00"
      rotation_pending: false
    bamse:
      session_id: "ses_e2666f565ffeJOFh8Mkvca5Ils"
      created_at: "2026-11-14T17:30:15+01:00"
      last_usage_pct: "8 %"
      last_checked: "2026-11-14T17:32:05+01:00"
      rotation_pending: false
    hadji:
      session_id: "ses_f22f4c5efffeId8AhyLrvataac"
      created_at: "2026-11-14T17:30:16+01:00"
      last_usage_pct: "3 %"
      last_checked: "2026-11-14T17:32:05+01:00"
      rotation_pending: false
    hifadhui:
      session_id: "ses_y6ZatCMQ45ffeKZOMomq8YLR0my"
      created_at: "2026-11-14T17:30:17+01:00"
      last_usage_pct: "5 %"
      last_checked: "2026-11-14T17:32:05+01:00"
      rotation_pending: false
    zawadi:
      session_id: "ses_e2523bf5affeNwaDeq5t9hbncH"
      created_at: "2026-11-14T17:30:18+01:00"
      last_usage_pct: "0 %"
      last_checked: "2026-11-14T17:32:05+01:00"
      rotation_pending: false
```

**Champ `rotation_pending`** : quand l'usage atteint 85 % pendant l'exécution de
l'agent, on marque `rotation_pending: true` sans interrompre. Une fois
l'agent terminé, la prochaine invocation utilise une nouvelle session et remet
`rotation_pending: false` avec le nouvel `session_id`.

## Logique du watcher

1. Démarrage → lire `watcher-state.yaml`.
2. Si `task_id` ≠ tâche en cours → fichier ignoré (nouvelle tâche = sessions neuves).
3. Pour chaque agent à déclencher : lire la session stockée → interroger
   OpenCode (comme le dashboard) → si usage ≥ 85 % ou compaction détectée
   ou `rotation_pending: true` → nouvelle session + mise à jour du fichier.
   Sinon → `--session <id>`.
4. Après l'appel : mettre à jour `last_usage_pct` et `last_checked`.
5. Si l'agent est en cours de travail → rotation différée au prochain cycle
   où l'agent est idle.

## Ce qui ne change pas

Le dashboard, les rapports, les métriques, le principe « mémoire dans les
rapports » — tout reste comme maintenant. Le fichier n'est qu'un pointeur
« dernière session connue par agent ».

## Ce qui disparaît

La notion de « lancement », `started_at`, la rotation complexe de `ddfe2c4`.
Tout devient linéaire : une session par (tâche, agent), réutilisée tant
qu'OpenCode dit qu'elle n'est pas saturée.

## Fichiers à créer / modifier (non implémenté)

- `watcher-state.yaml` : nouveau fichier d'état (`.opencode/state/`)
- `watch-work.py` : lire/écrire `watcher-state.yaml`, réutiliser `--session`,
  lire les métriques via `last_assistant_message` + `message_context_tokens`
  + `load_model_limits` (déjà dans `sayrhazi-modules.py`)
- `sayrhazi-modules.py` : import dans `watch-work.py` (déjà possible, même
  répertoire)
- `engine/state.py` : ignorer `watcher:` comme clé inconnue (déjà le cas
  pour `session:`)
- `docs/` : mise à jour de la documentation de session

## Statut

**Implémenté et validé (0.17.0).** `watch-work.py` réutilise `--session` par
(tâche, agent) avec rotation sur `session.max_usage` (défaut 85 %) ou
changement de `time_compacting` ; état dans `.opencode/state/watcher-state.yaml`.
