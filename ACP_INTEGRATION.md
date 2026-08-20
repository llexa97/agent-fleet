# Intégration ACP

## Positionnement

Agent Fleet agit comme client ACP à l’intérieur de chaque worker. Le transport stable est ACP v1 en JSON-RPC sur `stdin/stdout`; aucun port du harness n’est exposé et ACP n’est pas transporté directement entre LXC. Le protocole WebSocket interne encapsule des commandes métier et des mises à jour normalisées.

Références officielles consultées :

- [vue d’ensemble ACP v1](https://agentclientprotocol.com/protocol/v1/overview) et [schéma v1](https://agentclientprotocol.com/protocol/v1/schema) ;
- [initialisation et négociation](https://agentclientprotocol.com/protocol/v1/initialization) ;
- [cycle d’un prompt](https://agentclientprotocol.com/protocol/v1/prompt-turn) ;
- [permissions](https://agentclientprotocol.com/protocol/v1/tool-calls#requesting-permission) ;
- [brouillon ACP v2](https://agentclientprotocol.com/protocol/v2/overview) ;
- [SDK Python ACP officiel](https://github.com/agentclientprotocol/python-sdk) ;
- [adaptateur codex-acp officiel](https://github.com/agentclientprotocol/codex-acp) ;
- [adaptateur claude-agent-acp officiel](https://github.com/agentclientprotocol/claude-agent-acp) ;
- [authentification Claude officielle](https://platform.claude.com/docs/en/manage-claude/authentication) ;
- [Buzz](https://github.com/block/buzz), son bridge `buzz-acp` et sa [spécification d’agents distants](https://github.com/block/buzz/blob/main/docs/remote-agents.md).

Le SDK Python est verrouillé dans `uv.lock`. Les binaires externes `codex-acp` et `claude-agent-acp` ne sont pas installés par ce lockfile : l’opérateur doit verrouiller leur version côté worker et vérifier la version remontée dans l’inventaire. Toute mise à jour ACP ou d’adaptateur exige les tests de contrat et de reprise avant déploiement.

## ACP v1 et v2

ACP v1 est la surface de production. Le worker ne suppose aucune méthode optionnelle : il appelle `initialize`, enregistre la version et les capacités retournées, puis construit un objet de capacités négociées.

ACP v2 reste un brouillon susceptible de changements incompatibles et n’est pas implémenté dans le MVP livré. Le Control Plane annonce systématiquement `acp_v2_experimental: false`; le worker refuse explicitement une configuration reçue à `true`. La variable `AGENT_FLEET_ACP_V2_EXPERIMENTAL` est un nom réservé pour une évolution future, pas un flag actuellement chargé par le worker.

Une future expérimentation exigera simultanément :

- `AGENT_FLEET_ACP_V2_EXPERIMENTAL=true` sur le worker ;
- une configuration explicite du harness ;
- une version annoncée compatible lors de l’initialisation ;
- un adapter v2 isolé de l’implémentation v1 ;
- des tests de contrat dédiés.

Tant que ces éléments et leurs tests de contrat n’existent pas, aucune session v2 ne peut démarrer. Un futur échec v2 ne devra pas basculer silencieusement vers une sémantique approximative ; un retour vers v1 devra créer une nouvelle session explicitement tracée.

## Interface interne

```text
HarnessAdapter
├── discover()
├── health_check()
├── spawn(workspace, environment)
├── initialize()
├── create_session(context)
├── resume_session(harness_session_id, context)
├── prompt(content)
├── cancel()
├── close_session()
├── terminate()
└── parse_updates()
```

`CodexAcpAdapter`, `ClaudeAcpAdapter`, `OpenCodeAcpAdapter` et `FakeAcpAdapter`
partagent cette interface. OpenCode est lancé via son transport ACP natif
`opencode acp`. Les futurs Hermes, Goose et Gemini CLI n’imposent donc aucune
modification au modèle de l’agent logique.

## Découverte et lancement

Les exécutables sont exclusivement issus du YAML local :

```yaml
harnesses:
  codex:
    executable: /usr/local/bin/codex-acp
    args: []
    enabled: true
    max_instances: 4
    env_allowlist: [CODEX_API_KEY, OPENAI_API_KEY, NO_BROWSER]
  claude:
    executable: /usr/local/bin/claude-agent-acp
    args: []
    enabled: true
    max_instances: 2
    env_allowlist: [ANTHROPIC_API_KEY]
  opencode:
    executable: /root/.opencode/bin/opencode
    args: [acp]
    enabled: true
    max_instances: 2
    env_allowlist: [OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENCODE_API_KEY]
    version_args: [--version]
```

Le worker vérifie actuellement que le chemin configuré est absolu, désigne un fichier et est exécutable, puis relève sa version. Le contrôle strict du propriétaire et des bits d’écriture du binaire reste un durcissement de déploiement à ajouter ; les permissions Linux/systemd doivent donc empêcher sa modification par l’utilisateur du harness. Le processus reçoit seulement les variables allowlistées et le workspace canonique est transmis par `cwd` à `session/new`/reprise. La commande réseau choisit `harness_type`, jamais `executable` ou `args`.

`stderr` est capturé comme diagnostic redacté et borné. `stdout` est réservé au framing ACP ; tout texte non JSON-RPC est une erreur de protocole, pas un message utilisateur.

## Cycle de vie

1. Lancer le processus dans son groupe de processus propre.
2. Construire les flux NDJSON/JSON-RPC via le SDK officiel.
3. Envoyer `initialize` avec les capacités client réellement offertes.
4. Vérifier la version choisie et stocker toutes les capacités retournées.
5. Utiliser les credentials locaux du processus ; aucun flux ACP interactif d’authentification n’est implémenté par le worker MVP.
6. Appeler `session/new` avec un `cwd` absolu et les serveurs MCP autorisés.
7. Ou appeler la méthode de reprise/listing uniquement si annoncée.
8. Envoyer `session/prompt` et diffuser chaque `session/update` normalisé.
9. Relayer les permissions et attendre une décision centrale.
10. Terminer le tour avec le `stopReason`, l’usage et l’état durable.
11. À l’annulation, envoyer `session/cancel`, attendre un délai borné, puis terminer le groupe si nécessaire.
12. Fermer une session si la capacité existe, sinon annuler ; la suppression ACP n’est pas implémentée dans le MVP.

Le process ACP peut survivre à plusieurs tours de la même session logique, mais jamais être partagé entre tenants ou espaces dans le mode `per_session`.

## Capacités

La matrice est issue de la réponse `initialize`, pas du nom du harness.

| Fonction | Comportement sans capacité |
|---|---|
| reprise de session | nouvelle session avec contexte central reconstruit, événement `resume_unavailable` |
| liste/suppression | bouton/API désactivé, aucune méthode envoyée |
| mode/modèle/config | conserver le défaut local et rendre l’option indisponible |
| MCP client | ne pas injecter `fleet-mcp-proxy`; publier le fallback final uniquement |
| terminal/fichiers | refuser l’opération client correspondante |
| usage/coût | stocker `unknown`, ne jamais inventer une estimation comme mesure réelle |
| logout | ne pas appeler la méthode |

Les extensions `_meta` sont conservées de façon namespacée mais ne donnent aucun droit supplémentaire.

## Normalisation des mises à jour

| Mise à jour ACP | Événement Agent Fleet |
|---|---|
| message agent chunk | `agent_message_chunk` |
| plan explicitement fourni | `plan` |
| tool call/update | `tool_call` / `tool_result` |
| terminal | `terminal_output` |
| modification fichier/diff | `file_change` |
| usage | `usage` |
| permission | `permission_request` |
| erreur/stop reason | `error` / complétion de session |

Les numéros de séquence sont attribués par le worker. Le plan de contrôle persiste avant diffusion. La UI n’affiche aucune chaîne de pensée privée : uniquement messages, plans explicitement fournis, résumés déclarés, statuts, actions et résultats observables.

## Contexte de session

Le prompt initial est construit côté Control Plane et contient uniquement le contexte autorisé :

```text
Tu es @backend-dev.
Espace : Business
Channel : #client-taxi
Tâche : TASK-145
Trace : TRACE-001
Demandeur : @cto
Workspace autorisé : /srv/projects/fleetbase-ui
Membres disponibles : @cto, @backend-dev, @code-reviewer
Pour communiquer, utilise les outils fleet.*.
Une citation textuelle ne constitue pas une mention ou une délégation.
Respecte les permissions et le budget fournis.
```

Le chemin est obtenu de l’inventaire du worker après sélection par `workspace_id`, jamais interpolé depuis une entrée utilisateur. Les historiques Business et Personnel ne sont jamais fusionnés.

## fleet-mcp-proxy

Le proxy MCP tourne localement et est configuré au moment de `session/new` si le harness le permet. Il expose :

```text
fleet.list_agents             fleet.get_agent
fleet.list_channel_members    fleet.read_channel_history
fleet.get_thread              fleet.post_message
fleet.reply_message           fleet.mention_agent
fleet.create_task             fleet.delegate_task
fleet.update_task             fleet.complete_task
fleet.fail_task               fleet.request_human_approval
fleet.get_trace               fleet.cancel_trace
```

Chaque appel est lié à la session locale. Le worker transmet `tool_call` par sa socket authentifiée, le Control Plane vérifie tenant, espace, membership, délégation, budget et idempotence, puis renvoie `tool_result`. Un agent ne choisit pas son identité dans les arguments.

Si l’agent publie via `fleet.post_message` ou `fleet.reply_message`, le compteur de publication du tour empêche la duplication. Sinon, le texte final peut être publié automatiquement. Les logs ACP ne deviennent pas des messages ordinaires.

## Permissions ACP

Une demande de permission est persistée avec l’identifiant externe exact et place session/livraison en `waiting_approval`. L’absence de réponse n’autorise rien. Une décision obsolète ou liée à une autre session ne correspond à aucune attente active et n’accorde rien.

`allow_session` et `allow_agent` sont enregistrés, mais le MVP ne maintient pas encore de registre central de grants réutilisables/expirables : le worker choisit l’option ACP `allow_always` du processus courant lorsqu’elle existe. Cette limite doit rester visible dans l’UI et aucune autorisation implicite ne doit être annoncée au-delà de ce processus.

À l’annulation pendant une attente, la demande est clôturée, le harness reçoit un refus/annulation conforme à ses capacités puis le tour est annulé.

## Codex

L’adaptateur officiel `codex-acp` est un serveur ACP stdio qui lance le Codex App Server et traduit ses événements. Son installation officielle peut se faire par le paquet npm `@agentclientprotocol/codex-acp`; en production, installer une version exacte et enregistrer son chemin absolu.

Il supporte actuellement l’authentification ChatGPT, clé API (`CODEX_API_KEY` ou `OPENAI_API_KEY`) et passerelle compatible lorsque le client l’annonce. Sur un LXC sans navigateur, `NO_BROWSER=1` évite de proposer un flux interactif inadapté. Les credentials restent sur le worker.

Le mapping couvre modèle/mode si annoncé, working directory, streaming, outils, commandes, modifications, permissions, annulation, sessions, usage et erreurs. Les métadonnées Codex namespacées sont conservées sans être interprétées comme autorisations.

## Claude

`claude-agent-acp` adapte l’Agent SDK Claude et prend en charge notamment outils, permissions, terminaux, TODO et MCP client selon sa version. Installer le paquet officiel à une version verrouillée.

Pour un service non interactif, utiliser une méthode d’authentification explicitement supportée par l’adaptateur et le fournisseur. `ANTHROPIC_API_KEY` est la valeur MVP documentée et reste dans `worker.env`; un futur mécanisme d’identité de workload peut la remplacer. Ne pas supposer qu’un login Claude Code ou un abonnement utilisateur est automatiquement réutilisable par un outil tiers.

## Faux agent ACP

Le faux agent est déterministe et ne dépend d’aucun secret. Il doit pouvoir :

- initialiser et négocier des capacités ;
- créer/reprendre une session ;
- émettre plusieurs chunks ;
- appeler un outil `fleet.*` ;
- demander une permission ;
- simuler timeout, crash et annulation ;
- produire une mention structurée d’un second agent.

Il est le harness par défaut des tests unitaires, intégration et E2E. Aucun mock conditionnel ne se trouve dans les adaptateurs de production.

## Tests réels optionnels

```bash
# Les marqueurs external restent exclus du parcours général.
uv run pytest -m external -k codex
uv run pytest -m external -k claude
```

Le test Codex est ignoré sans credential/méthode locale valide. Le test Claude est ignoré sans `ANTHROPIC_API_KEY` ou configuration explicitement reconnue. Les sorties de test passent par la redaction et ne capturent jamais l’environnement.

## Mise à niveau

1. Lire les notes ACP, SDK et adaptateur.
2. Mettre à jour une version exacte dans un environnement isolé.
3. Régénérer/valider les schémas et fixtures.
4. Exécuter faux ACP, contrats, permissions, annulation, crash et reprise.
5. Exécuter les smoke tests externes si les secrets sont disponibles.
6. Déployer sur un worker canari, puis vérifier sessions et erreurs.
7. Conserver l’ancien binaire pour rollback et ne migrer aucune session en cours de force.
