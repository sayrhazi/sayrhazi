# Règles de reporting Sayrhazi (runtime-agnostiques).

- Dernier état dans `resume/` : `plan.txt`, `frontend.txt`, `backend.txt`, `review.txt`, `security.txt`, `qa.txt`.
- Avant remplacement, archiver dans `history/` (`build-log.md`, `design-log.md`, `review-log.md`, `security-log.md`, `qa-log.md`).
- Chaque rapport : date système réelle, projet, `task_id` stable, auteur (`agent`, rôle stable), `completed_at` ISO 8601 (jamais `version`), `summary`, vérifications, problèmes classés `CRITIQUE / HAUTE / MOYENNE / FAIBLE`, limites, suite, verdict du rôle. `next_agents` optionnel.
- Le rapport est l'unique mémoire d'un agent : chaque appel ouvre une session neuve, donc rien n'est conservé en conversation. Sur une tâche longue, un `summary` de quelques lignes est insuffisant — consigner décisions prises, hypothèses retenues et écartées, fichiers modifiés, problèmes non résolus et prochaines actions. Un rapport qui ne permet pas à l'agent suivant de reprendre sans relire tout le code n'est pas un rapport.
- Statuts : `TERMINÉ` = achèvement, `VALIDÉ` = verdict, ne pas les mélanger. Plan `À IMPLÉMENTER / INFORMATIF / VALIDÉ` ; implémentation `EN COURS / TERMINÉ / BLOQUÉ` ; interface `PROPOSÉ / VALIDÉ` ; review/sécurité `VALIDÉ / VALIDÉ AVEC RÉSERVES / CORRECTIONS NÉCESSAIRES` ; QA ajoute `À COMPLÉTER`.
- `plan.txt` cumulatif jusqu'à archivage explicite d'une feature dans `features/<nom-stable>.md`.
