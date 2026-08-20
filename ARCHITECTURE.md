# Architecture d’Agent Fleet

Statut : architecture de référence du MVP
Dernière mise à jour : 20 août 2026

## Objectif

Agent Fleet est un plan de contrôle web qui coordonne des agents logiques persistants sans déplacer leurs outils ni leurs fichiers hors des LXC où ils travaillent. Le navigateur ne parle jamais directement à un harness. Chaque worker initie une connexion WebSocket sécurisée vers le plan de contrôle et lance localement un adaptateur ACP sur `stdin/stdout`.

La tranche verticale prioritaire est :

```text
Axel publie un message structuré
  -> PostgreSQL persiste message, mention, trace et livraison
  -> le dispatcher réclame la livraison
  -> un worker sortant reçoit une commande versionnée
  -> le worker lance/reprend un processus ACP local
  -> les mises à jour ACP reviennent en streaming
  -> la réponse et ses mentions structurées sont persistées
  -> un autre agent peut être exécuté sur un autre worker
```

## Vue d’ensemble

```text
                       HTTPS / WSS
┌──────────────┐    ┌──────────────────────────────────────────────┐
│ Navigateur   │───>│ Caddy                                        │
│ React/Vite   │<───│ TLS, en-têtes, routage SPA/API/WebSocket     │
└──────────────┘    └───────────────┬──────────────────────────────┘
                                    │
                    ┌───────────────▼────────────────┐
                    │ FastAPI / API v1               │
                    │ auth, ACL, commandes, événements│
                    └───────┬──────────────┬─────────┘
                            │              │
                  ┌─────────▼──────┐  ┌────▼──────────┐
                  │ PostgreSQL     │  │ Redis         │
                  │ source de vérité│  │ réveils, pubsub│
                  └─────────▲──────┘  └────┬──────────┘
                            │              │
                    ┌───────┴──────────────▼─────────┐
                    │ Dispatcher durable             │
                    │ leases, budgets, sélection     │
                    └───────────────┬────────────────┘
                                    │ commandes persistées
                         WSS sortant│du worker
              ┌─────────────────────▼─────────────────────┐
              │ Worker LXC                               │
              │ journal local, supervision, workspaces   │
              │ fleet-mcp-proxy                          │
              └───────────────┬───────────────────────────┘
                              │ ACP v1 JSON-RPC, stdio
                        ┌─────▼─────────────┐
                        │ codex-acp /       │
                        │ claude-agent-acp /│
                        │ faux ACP          │
                        └───────────────────┘
```

## Composants

### Application web

L’application React est un client non privilégié. Elle utilise les cookies de session `HttpOnly`, récupère un jeton CSRF éphémère par l’API, consomme `/api/v1/` et se reconnecte à `/api/v1/events/ws`. Après une reconnexion, elle recharge l’état durable par HTTP avant d’appliquer les événements plus récents. Elle n’enregistre aucun jeton durable dans `localStorage`.

### API FastAPI

L’API valide l’identité, le tenant, l’espace et les memberships avant toute lecture ou mutation. Les routes délèguent les invariants métier aux services. Les mutations critiques exigent une clé d’idempotence. Les écritures qui produisent un travail futur persistent dans la même transaction les données métier, les livraisons et l’événement durable correspondant.

### PostgreSQL

PostgreSQL est l’unique source de vérité pour les identités, messages, mentions, tâches, traces, livraisons, sessions, workers, permissions, workflows et audits. Les contraintes et index empêchent notamment deux livraisons actives sur la même file logique. Les migrations Alembic sont appliquées avant le démarrage d’une nouvelle version.

### Redis

Redis sert à réveiller rapidement les dispatchers, publier des événements WebSocket, limiter le débit, stocker la présence éphémère et certains leases d’optimisation. Un événement Redis peut être perdu sans perte métier : le dispatcher sonde aussi PostgreSQL et le navigateur peut reconstruire l’état depuis l’API.

### Dispatcher

Le dispatcher réclame des livraisons avec un verrou transactionnel équivalent à `FOR UPDATE SKIP LOCKED`, pose un lease expirant, vérifie les limites de trace et choisit un worker. Il ne considère une commande comme exécutée qu’après accusé de réception. Un lease abandonné redevient réclamable. Les échecs suivent un backoff borné, puis un état d’échec explicite.

### Worker

Un worker tourne sous un utilisateur Linux dédié et ouvre uniquement une connexion WSS sortante. Il annonce un inventaire vérifié localement, maintient les heartbeats, refuse les exécutables ou workspaces reçus du réseau et supervise les processus ACP. Les commandes importantes sont dédupliquées dans un petit journal durable sous `/var/lib/agent-fleet-worker`.

### Adaptateurs ACP et fleet-mcp-proxy

Les adaptateurs présentent une interface interne commune mais négocient les capacités réelles du harness lors de `initialize`. ACP reste local au worker. `fleet-mcp-proxy` expose les outils `fleet.*` au harness et transmet les appels au plan de contrôle sur la connexion déjà authentifiée ; l’identité de l’agent est liée à la session et ne vient jamais d’un argument du modèle.

## Frontières de confiance

```text
Internet ──TLS──> Caddy ──loopback──> API
                                │
                    tenant/space/membership
                                │
Worker LXC ──WSS + jeton──> passerelle worker
    │                           │
utilisateur Linux          session ↔ agent
    │
workspace enregistré ── ACP stdio ── harness autorisé
```

Les frontières sont fail-closed : un tenant, un espace, un channel, un workspace, un harness ou une capacité absent/inconnu entraîne un refus, jamais un élargissement implicite.

## Modèle d’identité

Un acteur est référencé par `(actor_type, actor_id)` avec les types `human`, `agent`, `system` et `workflow`. Un agent logique conserve son UUID, son handle, ses memberships, son historique, ses politiques et ses tâches quand son modèle, son harness ou son worker change. La liaison d’exécution est donc une configuration remplaçable, pas l’identité de l’agent.

Toute donnée sensible porte un `tenant_id`. Un `space_id` isole Business, Personnel et les espaces futurs. Les contrôles applicatifs sont doublés par des clés étrangères et des contraintes cohérentes ; une future défense en profondeur par Row Level Security est possible sans changer les contrats publics.

## Flux transactionnel d’une mention

1. Le client envoie le contenu visible et une liste de cibles structurées, avec une clé d’idempotence.
2. L’API authentifie l’auteur et vérifie son membership du channel.
3. Le service résout chaque handle dans le même tenant et le même espace, puis vérifie le membership du destinataire.
4. Une transaction crée le message, les mentions, la trace si nécessaire, une livraison par agent et les événements d’audit.
5. Après commit seulement, Redis reçoit un réveil best-effort.
6. Le dispatcher réclame une livraison et verrouille la file `queue:{agent_id}:{channel_id}`.
7. Les budgets, la profondeur, les boucles, le binding runtime et la capacité worker sont vérifiés.
8. Une commande persistante `start_session`, `resume_session` ou `prompt` est envoyée au worker.
9. Chaque mise à jour du worker est dédupliquée, persistée et publiée en temps réel.
10. La réponse finale est publiée une seule fois : publication explicite via `fleet.*`, sinon fallback automatique.
11. Les nouvelles mentions structurées repassent par le même pipeline.

Une chaîne `@` présente uniquement dans le texte n’active personne. Le parseur de secours reste désactivé par défaut.

## Files, concurrence et garanties

La file logique est `(tenant_id, agent_id, channel_id)`. Une contrainte partielle garantit au plus une livraison active par file. Un agent peut travailler dans plusieurs channels si ses budgets et la capacité du worker l’autorisent.

La garantie est « au moins une tentative, un seul effet métier » :

- les messages, livraisons et commandes ont des clés d’idempotence uniques ;
- les réceptions worker ont un identifiant et un hash de payload ;
- les événements de session ont une séquence unique par session ;
- un accusé tardif ne redémarre pas une commande terminée ;
- les retries sont bornés et visibles.

## Sessions et contexte

Une session logique utilise une clé dérivée de :

```text
agent_id + channel_id + (thread_id | task_id | none) + workspace_id
```

Le mode MVP est `per_session` : un processus ACP par session active. Aucun processus ni contexte n’est partagé entre tenants ou espaces. Le `ContextBuilder` sélectionne les instructions, la tâche, les messages récents pertinents, un résumé ancien, les membres accessibles, les outils, permissions et budget restant. Il ne charge jamais tout l’historique d’un channel par défaut.

## Tâches, traces et contrôle de boucle

Une mention est conversationnelle ; une délégation crée en plus une tâche persistante. Chaque exécution appartient à une trace arborescente. Le plan de contrôle, non les agents, applique les maxima de profondeur, tours, délégations, parallélisme, durée, coût et tokens.

Le MVP bloque l’auto-mention, les messages normalisés répétés, la répétition excessive d’une même paire source/cible, les transitions impossibles d’une tâche terminale et les dépassements de profondeur, tours, délégations, durée, tokens ou coût. Pause, reprise et annulation d’une trace sont persistées et auditées.

La détection sémantique d’absence de progression et la reconnaissance stricte d’une alternance ordonnée A→B→A ne sont pas encore implémentées : `max_no_progress_turns` est réservé dans la politique mais n’autorise pas à annoncer ce contrôle comme actif. Elles nécessiteront un signal de progression déterministe et des tests dédiés avant activation.

## Permissions

Une requête ACP sensible suspend la session et crée une `permission_request`. `deny` et `allow_once` sont appliqués à la requête courante ; `allow_session` et `allow_agent` sont persistés comme décisions mais, dans le MVP, ne créent pas encore de grant central réutilisable avec expiration. Le worker les traduit au mieux vers l’option ACP `allow_always` du processus courant. L’absence de décision n’autorise rien. La décision est une commande durable liée à la requête et transmise sur le canal WSS authentifié ; elle ne possède pas de signature applicative indépendante.

## Workflows

Les définitions et exécutions sont persistantes. Chaque déclencheur génère une clé d’idempotence et une trace. Les actions repassent par les mêmes services d’autorisation que les humains et agents. Les appels webhook imposent HTTPS par défaut, refusent les IP littérales ou résolues privées/réservées, désactivent les redirections et les proxies d’environnement, et bornent taille et timeout.

La résolution validée et la connexion HTTP restent deux opérations distinctes dans le MVP ; un filtrage egress du LXC demeure donc obligatoire contre le DNS rebinding. Il n’existe pas encore d’allowlist de réseaux privés : toute exception devra pinner l’adresse de connexion et revalider chaque tentative avant d’être documentée comme sûre.

## Observabilité

Les logs JSON portent `trace_id`, `session_id`, `delivery_id`, `worker_id` lorsque disponibles, sans prompt ni secret par défaut. `/api/v1/health` vérifie le processus, `/api/v1/readiness` les dépendances indispensables et `/metrics` expose les métriques Prometheus uniquement au réseau d’administration. Les compteurs principaux sont workers connectés, sessions actives, livraisons en attente, erreurs, durée de prompt, appels d’outils, permissions en attente, tokens et coût.

## Déploiement

Le plan de contrôle et chaque worker utilisent des LXC non privilégiés distincts. En production, Caddy termine TLS ; l’API et les bases ne sont pas exposées. Tailscale ou un VLAN privé relie les LXC. Les workers n’ont besoin d’aucune entrée réseau et ne reçoivent jamais de commande shell libre.

Voir [DEPLOYMENT_LXC.md](DEPLOYMENT_LXC.md), [SECURITY.md](SECURITY.md), [WORKER_PROTOCOL.md](WORKER_PROTOCOL.md) et [ACP_INTEGRATION.md](ACP_INTEGRATION.md).

## Décisions

Les décisions structurantes sont consignées dans [docs/adr](docs/adr/README.md). Les références externes principales sont :

- [spécification ACP v1](https://agentclientprotocol.com/protocol/v1/overview) ;
- [brouillon ACP v2](https://agentclientprotocol.com/protocol/v2/overview) ;
- [SDK Python ACP officiel](https://github.com/agentclientprotocol/python-sdk) ;
- [codex-acp officiel](https://github.com/agentclientprotocol/codex-acp) ;
- [claude-agent-acp officiel](https://github.com/agentclientprotocol/claude-agent-acp) ;
- [Buzz](https://github.com/block/buzz) et sa [spécification des agents distants](https://github.com/block/buzz/blob/main/docs/remote-agents.md).

Agent Fleet n’est ni un fork ni une copie de Buzz. Buzz sert de référence pour les humains/agents partageant des channels, la file par channel et le bridge vers des harness ACP. Agent Fleet retient volontairement un autre socle : identité/ACL et historique centralisés dans PostgreSQL, connexion de gestion WSS détenue par chaque worker LXC, tâches et approbations persistantes. La spécification Buzz des agents distants décrit au contraire un modèle où le relay/presence est le canal après déploiement ; ses invariants de refus d’identité vide, de redaction, d’idempotence et d’arrêt propre ont toutefois informé nos frontières de confiance.
