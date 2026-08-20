# Protocole Control Plane ↔ Worker

Version stable interne : `1.0`
Transport de production : WebSocket sécurisé sortant (`wss`)
Endpoint : `/api/v1/workers/connect`

Ce protocole est propre à Agent Fleet. Il transporte des commandes, événements et appels du proxy MCP entre LXC ; ce n’est pas ACP. ACP reste un JSON-RPC local sur `stdin/stdout` entre le worker et le harness.

## Invariants

- Le worker initie toujours la connexion.
- Un jeton identifie un worker précis et peut être révoqué ou tourné.
- Le serveur ne transmet jamais un chemin d’exécutable ou de workspace libre.
- Tout message est strictement validé et borné en taille.
- Une commande peut être livrée plusieurs fois mais son effet est idempotent.
- PostgreSQL conserve les commandes et événements centraux ; le journal local protège des doubles lancements après reconnexion.
- Une perte de Redis ou de WebSocket ne supprime aucun travail durable.

## Handshake

1. Le worker ouvre `wss://<control-plane>/api/v1/workers/connect?worker_id=<UUID>` avec son jeton dans l’en-tête `Authorization: Bearer …`. L’UUID n’est pas secret ; le jeton n’apparaît jamais dans l’URL.
2. Le serveur valide TLS, jeton, révocation et association du `worker_id`.
3. Le worker envoie `hello` dans le délai d’initialisation.
4. Le serveur choisit l’intersection de versions. Pour le MVP, seule `1.0` est acceptée.
5. Le serveur renvoie `welcome` avec les paramètres de heartbeat, limites et commandes non acquittées.
6. Le worker envoie un inventaire complet puis des heartbeats différentiels.

Le serveur refuse une socket dont le `worker_id` de l’enveloppe ne correspond pas au jeton. Une nouvelle connexion avec le même `boot_id` remplace proprement l’ancienne ; un nouveau `boot_id` déclenche la réconciliation des sessions.

## Enveloppe

```json
{
  "protocol_version": "1.0",
  "message_type": "prompt",
  "message_id": "3cd79026-b558-4bb0-99a1-2f25e0e895a8",
  "command_id": "4b2598a7-1665-44c2-a74f-e80d46d1ee15",
  "worker_id": "a1000000-0000-4000-8000-000000000001",
  "timestamp": "2026-08-20T10:00:00Z",
  "trace_id": "5d6301ed-dce0-4e22-ac21-a16cbc7415fa",
  "session_id": "7b945753-63ab-43fc-a5a8-b087a1997e4d",
  "idempotency_key": "worker-command:delivery:8f52:generation:1",
  "payload": {}
}
```

Tous les champs sauf `trace_id` et `session_id` sont obligatoires. Les UUID sont canoniques, le timestamp est UTC avec fuseau, `payload` refuse les champs inconnus selon le schéma du `message_type`. Une même `message_id` avec un hash de payload différent est une violation de protocole.

## Messages Control Plane → Worker

| Type | But | Accusé requis |
|---|---|---|
| `welcome` | version choisie, limites, heartbeat | non |
| `sync_configuration` | références de binding autorisées, jamais des secrets | oui |
| `start_session` | lancer un adapter et créer une session ACP | oui |
| `resume_session` | relancer/reprendre une session si négocié | oui |
| `prompt` | envoyer un tour à une session prête | oui |
| `cancel_prompt` | annuler le tour actif | oui |
| `close_session` | fermeture ACP propre | oui |
| `approve_permission` | répondre à une requête exacte | oui |
| `deny_permission` | refuser une requête exacte | oui |
| `shutdown_session` | terminer et nettoyer le groupe de processus | oui |
| `tool_result` | résultat autorisé d’un appel `fleet.*` | oui |
| `event_ack` | confirmer ou rejeter un événement worker durable | non |
| `ping` | mesure de vivacité | `pong` |

## Messages Worker → Control Plane

| Type | But |
|---|---|
| `hello` | version, `boot_id`, inventaire initial |
| `inventory` | harness, workspaces, labels, capacité |
| `heartbeat` | présence, charge, sessions et erreurs synthétiques |
| `ack` | commande reçue/rejetée avec état durable |
| `session_started` | processus et session ACP créés |
| `session_resumed` | reprise confirmée ou fallback déclaré |
| `session_update` | streaming, plan, outil, terminal, fichier, usage, erreur |
| `permission_request` | requête ACP bloquante |
| `usage_update` | tokens/coût quand connus |
| `session_completed` | fin de tour réussie |
| `session_failed` | erreur structurée et politique de reprise possible |
| `tool_call` | appel local `fleet.*` à autoriser côté serveur |
| `log` | diagnostic borné et redacté |
| `pong` | réponse de heartbeat |

## Hello et inventaire

```json
{
  "worker_version": "0.1.0",
  "supported_protocol_versions": ["1.0"],
  "boot_id": "d395f707-1857-44aa-94b4-ed28a932963b",
  "inventory": {
    "worker_id": "a1000000-0000-4000-8000-000000000001",
    "hostname": "dev-lxc-01",
    "version": "0.1.0",
    "protocol_versions": ["1.0"],
    "labels": ["development", "git"],
    "capacity": {"max_sessions": 4, "available_sessions": 3},
    "harnesses": [
      {
        "type": "codex",
        "adapter": "codex-acp",
        "version": "detected",
        "available": true,
        "capabilities": ["session/new", "session/prompt"]
      }
    ],
    "workspaces": [
      {
        "id": "fleetbase-ui",
        "display_name": "Fleetbase UI",
        "root": "/srv/projects/fleetbase-ui",
        "read_only": false
      }
    ]
  }
}
```

L’inventaire décrit des faits détectés localement. Le serveur ne peut pas activer un exécutable absent de `worker.yaml`. Les valeurs de variables d’environnement et les secrets ne figurent jamais dans l’inventaire.

## Commandes et accusés

Avant de répondre `ack:accepted`, le worker inscrit les commandes à effet important dans son journal local avec `command_id`, clé d’idempotence, type, hash, génération et statut. Les statuts locaux recommandés sont `received`, `started`, `completed`, `failed` et `cancelled`.

Un `ack` indique :

```json
{
  "acked_command_id": "4b2598a7-1665-44c2-a74f-e80d46d1ee15",
  "status": "accepted",
  "duplicate": false,
  "error": null
}
```

Si la commande est rejouée :

- même ID et même hash, le worker renvoie l’état mémorisé sans répéter l’effet ;
- même clé d’idempotence et nouvelle enveloppe, il rattache la nouvelle tentative au même effet ;
- même ID avec contenu différent, il refuse et signale une violation ;
- génération d’exécution plus ancienne, il refuse comme obsolète.

L’accusé confirme la prise en charge, pas la réussite du prompt. Le résultat arrive dans `session_*`.

## État d’une session

```text
absente -> starting -> active -> processing -> active
                     |              |
                     |              +-> waiting_approval -> processing
                     +-> failed
active/processing/waiting_approval -> cancelling -> cancelled
active -> closing -> closed
```

Une session n’accepte qu’un prompt à la fois. Chaque `session_update` contient `delivery_id`, une `sequence` croissante et un `update_type` parmi `status`, `agent_message_chunk`, `plan`, `tool_call`, `tool_result`, `terminal_output`, `file_change`, `usage`, `error`. Les doublons `(session_id, sequence)` et `(worker_id, worker_event_id)` sont ignorés après comparaison du hash.

## Appels fleet-mcp-proxy

Le processus MCP local ne possède pas de jeton global. Le worker lui injecte un canal/session local contenant l’agent effectif. Un `tool_call` vers le serveur contient session, channel/tâche implicites et arguments métier, mais aucun champ libre ne peut substituer l’appelant. Le serveur répond par `tool_result` après ACL, budget, audit et idempotence.

## Reconnexion

Le worker utilise un backoff exponentiel avec jitter, par exemple 1 s à 60 s, remis à zéro après une connexion stable. Il conserve les processus actifs durant une courte perte réseau tout en bornant les buffers. Après reconnexion :

1. nouveau `hello` avec le même `boot_id` si le processus worker n’a pas redémarré ;
2. inventaire et liste des sessions locales ;
3. serveur renvoie les commandes sans accusé et l’état central attendu ;
4. worker rejoue ses événements non confirmés par ordre de séquence ;
5. divergence résolue explicitement : reprendre, fermer ou marquer échec, jamais ignorer.

Un buffer plein met la session en pause ou la fait échouer proprement ; il ne supprime pas silencieusement des événements.

## Heartbeats et présence

Le serveur annonce l’intervalle, typiquement 15 s, et marque le worker hors ligne après trois intervalles. Le heartbeat comprend capacité, sessions actives et horodatage monotone local indicatif. L’horloge serveur fait autorité pour leases et présence. `ping/pong` ne remplace pas l’inventaire métier.

## Limites

- Taille d’enveloppe par défaut : 1 MiB.
- Taille de log et terminal par événement : bornée, avec fragmentation explicite.
- Timeout `hello` : 5 s.
- Timeout d’accusé de commande : configurable, puis retry borné.
- Heartbeat : 15 s par défaut, offline à 45 s.
- Les fichiers et artefacts volumineux utilisent plus tard un stockage dédié, jamais le WebSocket brut.

## Erreurs

Une erreur structurée contient un code stable, un message utilisateur redacté, un caractère retryable et des détails non sensibles. Codes minimaux : `authentication_failed`, `worker_revoked`, `protocol_version_unsupported`, `invalid_message`, `message_too_large`, `unknown_workspace`, `unknown_harness`, `capacity_exhausted`, `session_not_found`, `session_conflict`, `permission_expired`, `adapter_failed`, `timeout`, `cancelled`.

## Compatibilité

Une modification additive de payload peut être introduite seulement si les anciens validateurs peuvent l’ignorer selon une nouvelle version négociée ; dans `1.0`, `extra=forbid` impose donc un bump avant ajout sur le fil. Une version majeure incompatible n’est choisie que si les deux côtés l’annoncent. Le serveur peut maintenir une fenêtre de versions pendant les mises à jour progressives.

ACP v2 n’est pas une version de ce protocole. Dans le MVP, le Control Plane annonce `acp_v2_experimental: false` et le worker rejette `true` : aucun adapter ni feature flag v2 actif n’est livré. Une expérimentation future devra utiliser un adapter, un flag et des tests séparés.

## Sécurité opérationnelle

- jeton dans un fichier d’environnement `0600`, pas dans l’URL ;
- horodatage et IDs dans les logs, jamais le jeton ou le prompt complet ;
- WSS et vérification du certificat ;
- aucune commande libre, aucun chemin libre, aucune variable non allowlistée ;
- révocation ferme la socket et annule les nouvelles affectations ;
- limites de ressources du service et du LXC ;
- journal local en mode `0700`, nettoyage seulement après confirmation centrale et rétention.

Les schémas exécutables résident dans `packages/contracts/worker_protocol.py` et prévalent sur les exemples de ce document.
