# ADR-0004 — Agent logique indépendant du runtime

Statut : acceptée
Date : 20 août 2026

## Contexte

Un rôle comme `@cto` doit conserver son identité, ses channels, tâches, permissions et historique quand Codex est remplacé par Claude ou quand le worker change.

## Décision

L’entité `agents` porte l’identité et la politique. `agent_runtime_bindings` sélectionne harness, modèle, labels worker, workspace et isolation. Les sessions enregistrent le binding effectivement utilisé, sans redéfinir l’agent.

## Alternatives

- Un agent par processus/harness : rejeté, car identité et historique seraient couplés à un outil remplaçable.
- Un agent global multi-espace : rejeté par défaut pour éviter les mélanges Business/Personnel.

## Conséquences

La sélection runtime devient une étape explicite du dispatcher et doit être auditée. La migration de harness ne modifie pas les références historiques.
