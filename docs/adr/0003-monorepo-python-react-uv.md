# ADR-0003 — Monorepo Python/FastAPI et React

Statut : acceptée
Date : 20 août 2026

## Contexte

Le produit comprend une API, un dispatcher, un worker, un proxy MCP, des contrats partagés et une SPA. L’équipe souhaite Python avec `uv` pour le backend et TypeScript strict pour le frontend.

## Décision

Le dépôt reste un monorepo : Python 3.12, FastAPI, Pydantic, SQLAlchemy async, Alembic, SDK ACP Python et pytest sont gérés par `uv` avec un lockfile unique. React, Vite et pnpm gèrent l’interface. Les contrats réseau Python sont versionnés dans `packages/contracts`; les types frontend sont générés à partir d’OpenAPI lorsque le pipeline de génération est activé.

## Alternatives

- Tout TypeScript : bon écosystème ACP, mais contraire à la stack demandée et aux fondations existantes.
- Dépôts par service : rejetés pour le MVP, car ils multiplient versions et CI sans autonomie d’équipes correspondante.
- Poetry/pip-tools : rejetés au profit de `uv`, choisi explicitement et déjà utilisé.

## Conséquences

Une seule révision peut faire évoluer schéma, service et UI. Les images et installations doivent utiliser `uv sync --frozen`; aucune installation ad hoc avec `pip` n’est documentée.
