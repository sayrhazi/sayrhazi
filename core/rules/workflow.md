# Règles de workflow Sayrhazi (runtime-agnostiques).

## Principe directeur

Sayrhazi n'automatise que des transitions, jamais des décisions métier.
Le moteur applique une règle explicite, il ne choisit pas à la place de l'humain :

```text
un rapport identifié
+ un statut autorisé
+ une condition satisfaite
+ une transition déclarée
= un agent éligible
```

Le moteur orchestre les étapes prévues ; architecture, périmètre, déploiement
restent décidés par Lawibrahim et l'utilisateur.

## Flux et transitions

- Flux : planification → design (si visuel) → implémentation → review → sécurité (si risque) → QA (si observable). Projet vierge : squelette d'abord, via l'implémentation.
- Seule la transition `backend.txt TERMINÉ + task_id → review` peut être automatique aujourd'hui ; le reste est orchestré par l'humain. Les transitions automatisables cibles (`plan validé → build-back`, `backend + security_required → security`, `backend + visual_qa_required → qa`, `plan + design_required → build-front`, `review CORRECTIONS NÉCESSAIRES → build-back`) ne deviennent automatiques que si déclarées et vérifiées.
- Un verdict `CORRECTIONS NÉCESSAIRES` renvoie vers l'implémentation, jamais vers l'étape suivante.
- Les agents conditionnels (design, sécurité, QA) ne sont jamais invoqués systématiquement : chaque tâche déclare ses rôles nécessaires. Jamais de parcours automatique `build-back → reviewer → security → qa → build-front`.
- Les terminaux sont observation + intervention, jamais la mémoire du workflow : l'état indispensable à la reprise vit dans des fichiers projet, lisible après fermeture IDE, arrêt machine ou absence prolongée.
- Mode initial supervisé (`auto_start: false`) : transitions techniques prévues automatiques ; création/lancement de tâche importante, changement de périmètre ou de critères, déploiement, suppression, migrations de données : toujours validation humaine. Blocage ou ambiguïté : signalé, jamais deviné.
- Déploiement, suppression et migrations de données : toujours validation humaine.
