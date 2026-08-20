# Vérification de l’infrastructure

Date : 20 août 2026

## Contrôles réussis

```text
docker compose -f infra/compose/docker-compose.yml config
docker compose -f infra/compose/docker-compose.yml --profile edge config --quiet
bash -n scripts/backup.sh scripts/restore.sh scripts/install-control-plane.sh scripts/install-worker.sh
uvx --from shellcheck-py shellcheck scripts/backup.sh scripts/restore.sh scripts/install-control-plane.sh scripts/install-worker.sh
uv run python  # chargement strict des YAML Compose, local et systemd
uv run python  # contrôle des origines WebSocket 5173/8080 et refus d’une origine tierce
python3         # génération/vérification HMAC et refus après modification du checksum
git diff --check -- ARCHITECTURE.md SECURITY.md DEPLOYMENT_LXC.md WORKER_PROTOCOL.md ACP_INTEGRATION.md docs infra scripts
uv run pytest -q tests/integration/test_workflow_api.py tests/unit/test_mentions_and_policy.py tests/unit/test_worker_gateway.py tests/unit/test_task_transitions.py
```

Les configurations worker ont été acceptées avec leurs UUID, harness `fake`, URL Compose explicitement allowlistée, exemple natif loopback et workspaces distincts. Les deux origines de développement (`5173` direct et `8080` via Caddy) sont acceptées, tandis qu’une origine tierce est refusée.

Le calcul HMAC du sidecar a été reproduit avec une clé de 32 octets : la comparaison réussit sur le checksum original et échoue après modification.

Les 11 tests ciblés workflow/webhook, mentions/politiques, journal/protocole worker et transitions terminales de tâches ont réussi. Un avertissement de dépréciation Starlette/TestClient concernant une future migration `httpx2` reste non bloquant.

## Restauration PostgreSQL réelle

Un cluster PostgreSQL temporaire isolé a été initialisé sur un socket Unix et le port 55439. Le test a :

1. créé une base et une table `messages` avec UUID tenant et contenu ;
2. produit un dump custom avec `pg_dump` ;
3. supprimé puis recréé la base de test ;
4. restauré avec `pg_restore --exit-on-error --single-transaction` ;
5. vérifié la valeur exacte `22222222-2222-4222-8222-222222222222:persistant` ;
6. arrêté le cluster et supprimé le répertoire temporaire validé.

Résultat : réussi.

## Contrôles non exécutés dans cet environnement

Le client Docker est installé mais son daemon n’était pas démarré. La construction et le démarrage réels des images n’ont donc pas été exécutés ici. Caddy, `age` et `systemd-analyze` ne sont pas disponibles sur l’hôte macOS ; la chaîne complète chiffrement/HMAC et les validations runtime correspondantes doivent donc être exécutées sur un LXC de recette. Les scripts refusent l’activation/restauration si ces dépendances ou contrôles manquent.
