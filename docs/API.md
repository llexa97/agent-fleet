# API HTTP et WebSocket

L'API publique est versionnée sous `/api/v1`. Le contrat exécutable est généré par FastAPI :

- Swagger UI : `/api/v1/docs`
- ReDoc : `/api/v1/redoc`
- schéma OpenAPI JSON : `/api/v1/openapi.json`

Le schéma courant contient 41 chemins HTTP et 55 schémas nommés. Les payloads TypeScript consommés par le frontend sont regroupés dans `apps/web/src/types/api.ts` et vérifiés par les tests de contrat UI.

## Authentification

`POST /auth/bootstrap` crée le premier propriétaire lorsqu'aucun compte n'existe. Elle exige `X-Bootstrap-Token`. `POST /auth/login` crée ensuite une session serveur révocable.

Le navigateur reçoit deux cookies :

- un identifiant de session opaque `HttpOnly`, jamais stocké dans `localStorage` ;
- un jeton CSRF lisible par le client et renvoyé dans `X-CSRF-Token` pour toute mutation authentifiée.

En production, les cookies sont `Secure` et `SameSite=Strict`. `POST /auth/logout` révoque la session centrale avant de supprimer les cookies.

## Conventions

- Les identifiants publics sont des UUID stables.
- Les mutations critiques de message, tâche, lancement de workflow et webhook exigent `Idempotency-Key`.
- Une mention d'agent est routée uniquement par l'objet structuré `{target_type, target_id, handle_at_creation}`. Un `@handle` présent seulement dans le texte ne réveille personne.
- Une erreur métier utilise `{"error":{"code","message","details","request_id"}}` avec un code HTTP cohérent.
- Les historiques de messages et d'événements acceptent une limite bornée et un curseur. Les petites collections d'administration du MVP sont renvoyées comme tableaux bornés par tenant ; une pagination uniforme reste une extension avant un grand déploiement B2B.

## Ressources

| Ressource | Opérations principales |
|---|---|
| `/auth` | bootstrap, login, utilisateur courant, logout |
| `/spaces` | lister et créer des espaces |
| `/channels` | lister/créer, membres, messages et threads |
| `/threads` | lire et modifier un thread |
| `/agents` | lister/créer/modifier, ajouter un membership |
| `/tasks` | lister/créer/lire/modifier |
| `/traces` | lister/lire, pause, reprise et annulation |
| `/workers` | inventaire, enregistrement, rotation et révocation |
| `/workspaces` | lister et affecter à un espace |
| `/sessions` | sessions et événements ACP visibles |
| `/permissions` | demandes en attente et décision humaine |
| `/workflows` | CRUD, activation, pause, lancement et webhook signé |
| `/workflow-runs` | lister/lire, reprendre et annuler |
| `/events` | replay de l'outbox interne pour le navigateur |

## Temps réel navigateur

Le navigateur ouvre :

```text
GET /api/v1/events/ws?after=<event_id>
```

La socket utilise le cookie de session, vérifie l'origine et rejoue jusqu'à 1 000 événements durables après le curseur. Le client recharge d'abord l'état HTTP, puis applique les événements. Le heartbeat applicatif est `ping` / `pong`.

## WebSocket worker

Le endpoint `/api/v1/workers/connect?worker_id=<uuid>` n'est pas une API navigateur. Il exige `Authorization: Bearer <worker-token>` et le protocole versionné décrit dans [WORKER_PROTOCOL.md](../WORKER_PROTOCOL.md). Le jeton identifie le worker ; l'UUID dans la query ne constitue jamais une preuve d'identité.

## Santé et métriques

- `GET /api/v1/health` : vivacité du processus ;
- `GET /api/v1/readiness` : accès base et synthèse des files/workers ;
- `GET /metrics` : exposition Prometheus, à limiter au réseau d'administration.

Les endpoints de santé sont volontairement exclus du schéma OpenAPI public.

## Exemple de mention structurée

```http
POST /api/v1/channels/10000000-0000-4000-8000-000000000021/messages
Cookie: agent_fleet_session=...
X-CSRF-Token: ...
Idempotency-Key: axel-cto-auth-001
Content-Type: application/json
```

```json
{
  "content": "@cto fais avancer l'authentification",
  "mentions": [
    {
      "target_type": "agent",
      "target_id": "c7000000-0000-4000-8000-000000000001",
      "handle_at_creation": "cto"
    }
  ],
  "expects_response": true
}
```

Le commit transactionnel contient le message, la mention, la trace, la livraison et l'événement de réveil. Rejouer exactement la même clé retourne la ressource existante sans créer une seconde livraison.
