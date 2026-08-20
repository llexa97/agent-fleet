#!/usr/bin/env bash
set -euo pipefail

compose_file="infra/compose/docker-compose.yml"

docker compose -f "${compose_file}" up -d --build postgres redis
docker compose -f "${compose_file}" build api
docker compose -f "${compose_file}" run --rm api /app/.venv/bin/alembic upgrade head
docker compose -f "${compose_file}" run --rm api /app/.venv/bin/python -m scripts.seed_demo
docker compose --profile edge -f "${compose_file}" up -d --build \
  api dispatcher web worker-a worker-b caddy

printf '%s\n' "Agent Fleet démarre sur http://localhost:8080"
printf '%s\n' "Compte démo: axel@example.com (mot de passe: agent-fleet-demo-password par défaut)."
