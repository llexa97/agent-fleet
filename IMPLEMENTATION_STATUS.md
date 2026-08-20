# État d'implémentation

Dernière vérification : 20 août 2026
Version : `0.1.0`
Statut global : MVP vertical fonctionnel et testé, durcissement de recette LXC encore requis

Ce document distingue ce qui est prouvé par un test de ce qui est seulement préparé. Une case partielle ne doit pas être interprétée comme une fonctionnalité de production terminée.

## Tranche verticale prioritaire

Fonctionnelle et couverte par `tests/integration/test_full_stack_multi_worker.py` :

```text
Axel publie dans #client-taxi
  → mention structurée de @cto
  → livraison PostgreSQL
  → Worker A lance le faux harness ACP local
  → @cto crée une tâche et délègue via fleet.delegate_task
  → Worker B exécute @backend-dev
  → @backend-dev délègue à @code-reviewer
  → Worker A exécute la revue
  → les sous-tâches sont terminées et les demandeurs notifiés
  → @cto répond à Axel
```

Le test démarre une vraie API Uvicorn, deux vrais processus worker, plusieurs processus ACP locaux et `fleet-mcp-proxy`. Il vérifie cinq livraisons terminées, deux workers distincts, trois participants de trace, les tâches persistantes et la récupération de l'historique par une nouvelle session navigateur.

## Phases

### Phase 0 — Audit et conception : terminée

- ancien prototype ClauBot audité puis retiré du dossier ;
- environnement local, CLI Codex/Claude et adaptateurs disponibles inventoriés ;
- sources officielles ACP, adaptateurs et Buzz consultées ;
- architecture, sept ADR, contrats d'événements et protocole worker documentés ;
- ACP v1 choisi pour la production ; ACP v2 est refusé et désactivé.

### Phase 1 — Fondations : terminée pour le MVP

- monorepo Python/React avec `uv.lock` et `pnpm-lock.yaml` ;
- FastAPI, SQLAlchemy async, Alembic, PostgreSQL et Redis optionnel ;
- bootstrap du propriétaire, Argon2id, session opaque HttpOnly, CSRF et rate limit login ;
- espaces Business/Personnel, channels, memberships, messages, threads et replay d'événements ;
- application React utilisable avec Channels, Agents, Tasks, Traces, Workflows, Runners et Settings ;
- reconnexion WebSocket navigateur couverte par un test d'intégration.

Limite : la pagination est complète pour les historiques volumineux, mais certaines petites listes d'administration retournent encore un tableau borné. Les types HTTP frontend sont centralisés et testés, sans génération TypeScript automatique depuis OpenAPI.

### Phase 2 — Worker et ACP : terminée avec faux ACP, partielle avec fournisseurs

- WSS sortant, authentification individuelle, inventaire, heartbeat, backoff et reconnexion ;
- journal SQLite local pour commandes, idempotence et rejeu des événements ;
- validation stricte des exécutables, variables d'environnement et workspaces locaux ;
- interface `HarnessAdapter` et adaptateurs ACP v1 Codex, Claude et faux agent ;
- isolation `per_session`, streaming, reprise `session/load`, annulation, permissions et usage ;
- faux ACP déterministe : streaming, MCP, permission, crash, timeout, reprise et délégation.

Limites : `codex-acp` et `claude-agent-acp` ne sont pas installés sur la machine de développement. Les smoke tests externes existent mais n'ont pas été exécutés. La suppression d'une session ACP n'est pas exposée. Le contrôle du propriétaire et des modes Unix d'un exécutable configuré reste à durcir sur le LXC.

### Phase 3 — Mentions et agents : terminée pour le MVP

- agent logique indépendant du harness, worker, workspace et modèle ;
- autocomplétion `@` et identifiants structurés ;
- une simple chaîne contenant `@handle` ne crée aucune livraison ;
- transaction message + mentions + trace + livraisons + outbox ;
- file sérielle par agent/channel, concurrence inter-channel, lease et retry borné ;
- reprise de lease et doubles livraisons couvertes par tests.

### Phase 4 — Communication agent-à-agent : terminée pour le MVP

- proxy MCP local et 15 outils `fleet.*` ;
- identité appelante liée à la session, champs d'usurpation refusés ;
- contrôles tenant, espace, channel, membership, délégation et budget ;
- publication explicite ou fallback final, sans double message ;
- enchaînement réel entre deux workers couvert par le test E2E Python.

### Phase 5 — Tâches et traces : fonctionnelle, détection de boucle partielle

- tâches, sous-tâches, historique, réassignation contrôlée et résultat structuré ;
- notification automatique du demandeur sur terminaison ;
- participants, parenté, tours, profondeur, délégations, tokens, coût et durée de trace ;
- limites de profondeur, message identique, paire répétée, auto-mention, parallélisme et budgets ;
- pause, reprise et annulation avec annulation ACP réelle et protection contre une complétion tardive ;
- vues Kanban et arbre de trace.

Limites : il n'existe pas encore de détecteur sémantique d'absence de progression ni d'automate strict A→B→A. La table de dépendances est prête, mais son CRUD et l'ordonnancement conditionnel ne sont pas encore exposés. La réassignation est une mutation de tâche qui valide tenant/espace/channel ; elle ne reprogramme pas automatiquement une tâche déjà en cours et n'est pas une action dédiée de la vue Trace.

### Phase 6 — Permissions et Claude : partielle

- requêtes ACP persistantes, session/livraison en attente et décision humaine auditée ;
- refus, autorisation ponctuelle, de session ou d'agent représentés dans les contrats ;
- expiration sans autorisation implicite ;
- adaptateur Claude ACP et test optionnel réel.

Limites : `allow_session` et `allow_agent` ne disposent pas encore d'un registre central réutilisable entre plusieurs demandes. Le smoke test Claude réel est ignoré sans `ANTHROPIC_API_KEY`, opt-in et binaire officiel.

### Phase 7 — Workflows : terminée pour un moteur simple

- triggers manuel, message, mention, tâche créée/terminée, intervalle et webhook HMAC ;
- actions publier, mentionner, créer/assigner/invoquer, demander approbation, délai et webhook ;
- runs, traces, curseurs d'événement et reprise par étape persistants/idempotents ;
- activation, pause, annulation et reprise via API ;
- HTTPS obligatoire par défaut, DNS/IP privés refusés, redirections désactivées, timeout et taille bornée.

Limites : le scheduler MVP utilise des intervalles et non une syntaxe cron complète. La validation DNS et la connexion HTTP ne sont pas encore épinglées à la même IP ; un firewall egress reste obligatoire contre le DNS rebinding.

### Phase 8 — Durcissement et déploiement : documentée, recette LXC restante

- logs JSON et redaction, health/readiness, métriques Prometheus et audit append-only ;
- Compose, Caddy, cinq unités systemd et installations Control Plane/worker ;
- scripts bootstrap LXC tout-en-un pour installer les paquets système, `uv`,
  le Control Plane et les workers sans Docker ;
- LXC non privilégié, utilisateur dédié, Tailscale/VLAN, firewall, limites et rollback documentés ;
- sauvegardes chiffrées `age`, checksum HMAC, restauration exacte et points de retour chiffrés ;
- restauration PostgreSQL temporaire exécutée avec succès ;
- migration initiale testée sur SQLite et PostgreSQL 18.3, y compris downgrade/upgrade et trigger append-only.

Le Compose complet a désormais été construit et exécuté localement : migrations,
seed idempotent, API et frontend sains, Caddy en HTTP 200 et deux workers en
ligne. Caddy natif, `age` et systemd ne sont pas disponibles sur macOS ; les
deux scripts bootstrap et les unités doivent encore être exécutés dans un LXC
de recette avant production.

## Preuves de qualité

Commandes finales exécutées :

```text
uv run pytest -q
  47 passed, 2 skipped

uv run ruff check .
  réussi
uv run ruff format --check .
  129 fichiers formatés
uv run mypy apps services packages scripts tests
  108 fichiers, aucune erreur

pnpm --dir apps/web lint
pnpm --dir apps/web typecheck
pnpm --dir apps/web test
  4 tests réussis
pnpm --dir apps/web build
  build Vite réussi
pnpm --dir apps/web test:e2e
  1 test Chromium réussi
```

Les deux tests ignorés sont `test_real_codex_acp_smoke` et `test_real_claude_acp_smoke`. Ils sont explicitement opt-in et ne rendent jamais la suite normale dépendante de secrets.

Avertissements non bloquants :

- dépréciation `starlette.testclient`/`httpx` provenant des dépendances ;
- avertissement `FastMCP`/`pydantic-settings` sur une annotation `lifespan` non résolue dans la dépendance MCP.

## Avant une première production

1. Exécuter les scripts bootstrap et les unités systemd dans un LXC de recette non privilégié.
2. Installer les versions officielles verrouillées de `codex-acp` et/ou `claude-agent-acp` sur les workers.
3. Fournir les secrets uniquement dans le fichier worker `0600` ou les credentials systemd.
4. Exécuter les deux smoke tests externes et un canari sur un workspace non critique.
5. Tester Caddy HTTPS/WSS, la rotation/révocation en direct, la sauvegarde `age` et une restauration complète.
6. Ajouter le registre de permissions persistantes et le pinning DNS avant d'autoriser des actions externes sensibles.
