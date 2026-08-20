# ADR-0006 — ACP v1 stable, ACP v2 expérimental

Statut : acceptée
Date : 20 août 2026

## Contexte

ACP v1 est la surface stable publiée. ACP v2 est un brouillon avec API et fil susceptibles de changements incompatibles.

## Décision

La production utilise ACP v1 sur stdio. Les capacités optionnelles sont négociées à `initialize`. Le MVP désactive et refuse ACP v2. Toute implémentation v2 future devra posséder un adapter distinct, un feature flag faux par défaut et des tests séparés ; aucune capacité v2 ne sera supposée depuis un nom/version de harness.

## Alternatives

- Adopter v2 immédiatement : rejeté, risque de rupture et de transport instable.
- Ignorer v2 totalement : rejeté, empêcherait l’expérimentation maîtrisée et la préparation de migration.

## Conséquences

Le MVP ne contient aucun chemin v2 actif. Une petite duplication d’adapter sera acceptée pour isoler l’instabilité lorsqu’une expérimentation sera réellement ajoutée. La documentation et les lockfiles fixent les versions effectivement testées.
