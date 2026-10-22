# Modèle de tâche Sayrhazi (contrat : task_id stable recopié dans chaque rapport).
# status : À IMPLÉMENTER, INFORMATIF, EN COURS, TERMINÉ, BLOQUÉ, VALIDÉ.
# agents_required = source de vérité (rôles stables) ; les indicateurs sont des raccourcis
# qui doivent rester cohérents (security_required: true impose security dans agents_required, idem qa/build-front).

task_id: FEATURE-001
title: Titre court de la tâche
status: À IMPLÉMENTER
agents_required: [build-back, reviewer]
security_required: false
visual_qa_required: false
design_required: false
# scope: frontend | backend | fullstack
scope: backend
acceptance_criteria:
  - Critère observable 1.
  - Critère observable 2.
