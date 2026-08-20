# Développement local

## Prérequis

- Docker Engine avec Compose v2, ou Podman Compose compatible ;
- sans conteneur : Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 20+ et pnpm ;
- ports loopback disponibles : 5173, 8000, 5432 et 6379.

## Compose

```bash
cp .env.example .env
# Les valeurs de démonstration ne doivent jamais être réutilisées en production.
docker compose -f infra/compose/docker-compose.yml up --build
```

Services :

| Service | Adresse locale | Rôle |
|---|---|---|
| `web` | http://127.0.0.1:5173 | Vite/React |
| `api` | http://127.0.0.1:8000 | FastAPI/OpenAPI |
| `postgres` | 127.0.0.1:5432 | source de vérité |
| `redis` | 127.0.0.1:6379 | réveils/pubsub |
| `dispatcher` | interne | livraisons |
| `worker-a`, `worker-b` | sortant vers API | faux LXC/faux ACP |

Le profil `edge` ajoute Caddy sur http://127.0.0.1:8080 :

```bash
docker compose -f infra/compose/docker-compose.yml --profile edge up --build
```

Les workers emploient les tokens fixes de démonstration de 32+ caractères. Le script de données de démonstration doit enregistrer les mêmes empreintes avant leur première authentification. Ils se reconnectent avec backoff tant que le seed n’existe pas.

Pour migrations, seed Axel/agents/workers et démarrage coordonné :

```bash
./scripts/demo.sh
```

Le script est idempotent sur le tenant de démonstration. Les identifiants qu’il affiche sont publics et réservés à loopback ; ne rendez jamais cet environnement accessible sur le réseau.

## Sans Docker

```bash
cp infra/compose/config/control-plane-local.env.example .env
uv sync --all-groups --frozen
pnpm install --frozen-lockfile
uv run alembic upgrade head
uv run uvicorn apps.api.agent_fleet_api.main:app --reload --host 127.0.0.1 --port 8000
uv run python -m services.dispatcher.main
pnpm --dir apps/web dev --host 127.0.0.1
sed "s|/ABSOLUTE/PATH/TO/REPO|$(pwd -P)|g" \
  infra/compose/config/worker-local.example.yaml \
  > /tmp/agent-fleet-worker-local.yaml
AGENT_FLEET_WORKER_TOKEN='demo-worker-a-token-change-me-at-least-32-characters' \
  uv run python -m services.worker.main --config /tmp/agent-fleet-worker-local.yaml
```

Le fichier local dédié utilise uniquement `127.0.0.1`; les noms DNS `postgres`, `redis` et `api.agent-fleet.internal` sont réservés au réseau Compose. Démarrer PostgreSQL et Redis localement avec les identifiants indiqués, puis exécuter le seed de démonstration avant le worker. SQLite n’est prévu que pour certains tests, pas pour démontrer les garanties de file. `/tmp/agent-fleet-worker-local.yaml` ne contient aucun secret et peut être supprimé après le test.

## Santé et logs

```bash
curl --fail http://127.0.0.1:8000/api/v1/health
curl --fail http://127.0.0.1:8000/api/v1/readiness
curl --fail http://127.0.0.1:8000/metrics
docker compose -f infra/compose/docker-compose.yml logs -f api dispatcher worker-a worker-b
```

Ne collez pas un dump d’environnement ou un token dans un ticket. Les tokens du Compose ne sont pas valables hors de la base locale de démonstration.

## Tests

```bash
uv run pytest tests/unit
uv run pytest tests/integration
uv run ruff check .
uv run ruff format --check .
uv run mypy apps services packages
pnpm --dir apps/web test
pnpm --dir apps/web typecheck
pnpm --dir apps/web test:e2e
```

Les smoke tests réels sont marqués `external` et restent optionnels.

## Nettoyage

Arrêter sans supprimer les données :

```bash
docker compose -f infra/compose/docker-compose.yml down
```

Supprimer les volumes efface la base et les journaux workers de démonstration. Cette action est volontairement laissée manuelle :

```bash
docker compose -f infra/compose/docker-compose.yml down --volumes
```
