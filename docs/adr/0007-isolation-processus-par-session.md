# ADR-0007 — Processus ACP par session active

Statut : acceptée
Date : 20 août 2026

## Contexte

Partager un processus entre agents, espaces ou tenants réduit le coût mais augmente fortement le risque de fuite de contexte et complique annulation/reprise.

## Décision

Le mode MVP est `per_session` : une session logique active possède son processus ACP ou une isolation de groupe équivalente. L’interface prépare `per_agent` et `pooled`, mais ces modes restent désactivés jusqu’à preuve d’isolation et tests dédiés.

## Alternatives

- Processus par agent : coût moindre, mais collision possible entre channels et tâches.
- Pool global : rejeté pour le MVP, isolation insuffisamment démontrée.

## Conséquences

La capacité worker est comptée en sessions, les processus consomment plus de mémoire, et les limites LXC doivent en tenir compte. Annulation, crash et nettoyage deviennent plus prévisibles.
