# ADR-0001 — PostgreSQL est la source de vérité

Statut : acceptée
Date : 20 août 2026

## Contexte

Les messages, tâches, traces et sessions doivent survivre à un redémarrage de Redis, du dispatcher ou du plan de contrôle. Un bus éphémère simplifie le temps réel mais ne peut garantir cette conservation.

## Décision

Toutes les données métier, livraisons, commandes worker et audits sont transactionnelles dans PostgreSQL. Redis sert aux réveils, au pub/sub, à la présence, aux limites de débit et aux caches reconstruisibles. Le dispatcher sonde PostgreSQL même sans notification Redis.

## Alternatives

- Redis Streams comme journal principal : rejeté, car il dupliquerait la source d’autorité et complexifierait sauvegarde/restauration.
- Kafka dès le MVP : rejeté, coût opérationnel disproportionné pour une première installation.
- File en mémoire : rejetée, perte au redémarrage.

## Conséquences

Les transactions sont plus importantes et les index de file doivent être soignés. En contrepartie, la reprise, l’idempotence, les audits et les sauvegardes utilisent un modèle unique et testable.
