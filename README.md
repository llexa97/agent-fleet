# Agent Fleet

Agent Fleet est un plan de contrôle web pour orchestrer des agents logiques sur plusieurs LXC. Les workers ouvrent une connexion WebSocket sortante vers le Control Plane et gardent ACP au plus près des projets : chaque harness (`codex-acp`, `claude-agent-acp` ou le faux agent de test) est lancé localement en ACP v1 sur `stdin/stdout`.

Le MVP livre une tranche verticale exécutable :

```text
Axel → @cto → @backend-dev → @code-reviewer → @cto → Axel
```

Les messages, mentions structurées, livraisons, tâches, traces, sessions, permissions et commandes worker sont persistés. PostgreSQL reste la source de vérité ; Redis n'est qu'un accélérateur de réveil et de temps réel.

## Démarrage rapide de la démonstration

Prérequis : Docker avec Compose. Puis :

```bash
./scripts/demo.sh
```

Ouvrir <http://localhost:8080> et se connecter avec :

- utilisateur : `axel@example.com`
- mot de passe de démonstration : `agent-fleet-demo-password`

La démonstration démarre PostgreSQL, Redis, l'API, le dispatcher, React, Caddy et deux workers utilisant le faux agent ACP déterministe. Ces identifiants sont strictement locaux et doivent être remplacés avant toute exposition réseau.

## Développement avec uv

Python est géré exclusivement avec [uv](https://docs.astral.sh/uv/). Le fichier `uv.lock` est la source reproductible des versions ; aucun `requirements.txt` n'est utilisé.

```bash
uv sync --all-groups --frozen
pnpm install --frozen-lockfile

# Base et données locales (variables de .env à adapter)
uv run alembic upgrade head
uv run python -m scripts.seed_demo

# Processus de développement
uv run uvicorn apps.api.agent_fleet_api.main:app --reload
uv run python -m services.dispatcher.main
uv run python -m services.worker.main --config infra/compose/config/worker-local.example.yaml
pnpm --dir apps/web dev
```

Les exemples natifs hors Docker sont décrits dans [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Validation

```bash
# Python
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy apps services packages scripts

# Web
pnpm --dir apps/web lint
pnpm --dir apps/web typecheck
pnpm --dir apps/web test
pnpm --dir apps/web build
pnpm --dir apps/web test:e2e
```

Les smoke tests de vrais harness sont optionnels et restent exclus de la suite générale :

```bash
AGENT_FLEET_RUN_CODEX_SMOKE=1 uv run pytest -m external -k codex
AGENT_FLEET_RUN_CLAUDE_SMOKE=1 uv run pytest -m external -k claude
```

Ils exigent l'adaptateur officiel installé sur le worker et une authentification fournisseur valide. Aucun secret n'est requis pour la suite normale.

## Structure

```text
apps/api/                 API FastAPI et services métier
apps/web/                 application React/TypeScript
services/dispatcher/      réclamation durable et sélection des workers
services/worker/          passerelle WSS, journal local et adaptateurs ACP
services/fleet_mcp_proxy/ outils fleet.* liés à l'identité de session
services/fake_acp_agent/  harness ACP déterministe sans jetons
packages/contracts/       enveloppes d'événements et protocole worker
migrations/               migrations Alembic
infra/                    Compose, Caddy et unités systemd
tests/                    unitaires, intégration et Playwright
docs/                     ADR et guides d'exploitation
```

## Parcours fonctionnels

- channels, threads et autocomplétion de mentions `@` structurées ;
- agents logiques indépendants du harness, du worker et du workspace ;
- files durables par agent/channel, leases, retries bornés et déduplication ;
- streaming des événements ACP et reprise après reconnexion ;
- outils MCP `fleet.*` avec identité dérivée de la session ;
- tâches persistantes, délégations et notifications au demandeur ;
- traces avec pause, reprise, annulation, budgets et limites centrales ;
- demandes de permission ACP et décisions humaines auditées ;
- workers, inventaires, workspaces enregistrés et authentification révocable ;
- workflows persistants simples avec exécutions idempotentes.

## Sécurité et production

Le mode production impose PostgreSQL, des cookies sécurisés et des secrets remplacés. Les workers n'acceptent ni exécutable ni chemin arbitraire venant du réseau. Les installateurs LXC exécutent les services sous `root` sans créer de compte Linux supplémentaire : utiliser impérativement des LXC non privilégiés et dédiés, HTTPS/WSS via Caddy, Tailscale ou un VLAN privé, et conserver les secrets fournisseur sur le worker concerné.

Avant un déploiement, lire [SECURITY.md](SECURITY.md) et [DEPLOYMENT_LXC.md](DEPLOYMENT_LXC.md). L'état exact de chaque phase et les limites connues sont consignés dans `IMPLEMENTATION_STATUS.md`.

Pour une installation directe sans Docker dans les LXC, utiliser les deux
scripts tout-en-un :

```bash
sudo ./scripts/bootstrap-control-plane-lxc.sh --domain fleet.example.net
sudo ./scripts/bootstrap-worker-lxc.sh --help
```

Le parcours complet est décrit dans [docs/LXC_QUICK_INSTALL.md](docs/LXC_QUICK_INSTALL.md).

## Documentation

- [Architecture](ARCHITECTURE.md)
- [Intégration ACP](ACP_INTEGRATION.md)
- [Protocole worker](WORKER_PROTOCOL.md)
- [Sécurité](SECURITY.md)
- [Déploiement LXC](DEPLOYMENT_LXC.md)
- [Installation LXC rapide](docs/LXC_QUICK_INSTALL.md)
- [API](docs/API.md)
- [Sauvegarde et restauration](docs/BACKUP_RESTORE.md)
- [ADR](docs/adr/README.md)
