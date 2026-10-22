# Modèle de rapport Sayrhazi (contrat : task_id + agent + status + completed_at + summary requis).
# Horodatage : completed_at (ISO 8601) uniquement, jamais `version`.
# Statuts par rôle : build-back EN COURS/TERMINÉ/BLOQUÉ ; build-front PROPOSÉ/VALIDÉ ;
# reviewer/security VALIDÉ/VALIDÉ AVEC RÉSERVES/CORRECTIONS NÉCESSAIRES ; qa ajoute À COMPLÉTER ;
# architect (plan.txt) À IMPLÉMENTER/INFORMATIF/VALIDÉ.

task_id: FEATURE-001
agent: build-back
status: TERMINÉ
completed_at: "2026-01-01T00:00:00+00:00"
summary: Résumé d'une phrase.
next_agents: [reviewer]
verifications:
  - Vérification réalisée 1.
problemes: []
limites:
  - Limite connue 1.
suite: Prochaine étape ou destinataire.
