# ADR-0005 — Livraison au moins une fois, effet idempotent

Statut : acceptée
Date : 20 août 2026

## Contexte

Une socket peut tomber après exécution mais avant accusé. Promettre exactement une livraison sur un réseau distribué conduirait soit à perdre du travail, soit à masquer des cas ambigus.

## Décision

Le système garantit au moins une tentative avec déduplication des effets. Les livraisons et commandes ont des clés uniques, des générations d’exécution, leases expirables, reçus worker et séquences de session. Les retries sont bornés et finissent dans un état explicite.

## Alternatives

- Au plus une fois : rejeté, car un incident réseau pourrait perdre une mention.
- Transaction distribuée PostgreSQL/worker : rejetée, inadaptée à des LXC intermittents.

## Conséquences

Chaque mutation appelée depuis une livraison ou un outil doit être idempotente. Les opérateurs peuvent voir une tentative rejouée, mais pas deux messages/tâches résultants pour la même clé.
