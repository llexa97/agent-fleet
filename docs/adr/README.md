# Registre des décisions d’architecture

Une ADR décrit une décision durable, son contexte et ses conséquences. Les décisions acceptées ne sont pas réécrites : une nouvelle ADR les remplace explicitement.

| ADR | Décision | Statut |
|---|---|---|
| [0001](0001-postgresql-source-de-verite.md) | PostgreSQL source de vérité, Redis éphémère | Acceptée |
| [0002](0002-workers-sortants-et-acp-local.md) | Workers sortants et ACP local au LXC | Acceptée |
| [0003](0003-monorepo-python-react-uv.md) | Monorepo Python/FastAPI + React, géré avec uv/pnpm | Acceptée |
| [0004](0004-agent-logique-independant-du-runtime.md) | Agent logique indépendant du harness et du worker | Acceptée |
| [0005](0005-livraisons-au-moins-une-fois.md) | Livraisons au moins une fois avec effets idempotents | Acceptée |
| [0006](0006-acp-v1-stable-v2-experimental.md) | ACP v1 stable, v2 refusé dans le MVP et isolé à l’avenir | Acceptée |
| [0007](0007-isolation-processus-par-session.md) | Processus ACP isolé par session pour le MVP | Acceptée |

## Format

Chaque fichier contient : statut, contexte, décision, alternatives et conséquences. Les changements de configuration non structurants restent dans la documentation d’exploitation.
