# Sécurité d’Agent Fleet

Ce document décrit les garanties attendues du MVP et le durcissement d’une installation LXC. Il ne transforme pas un harness autonome en environnement sûr par magie : un agent autorisé à écrire ou exécuter dans un workspace dispose du pouvoir réel correspondant sur ce LXC.

## Principes

- Refus par défaut et privilège minimal.
- PostgreSQL fait foi ; Redis ne porte aucune autorisation durable.
- Aucune confiance fondée sur une adresse IP.
- Identité d’un agent dérivée de sa session, jamais d’un argument fourni par le modèle.
- Workspace choisi par identifiant enregistré, jamais par chemin réseau libre.
- Secret absent des messages, événements, URLs, journaux et captures de test.
- Toute action sensible est attribuable dans un audit append-only applicatif.

## Menaces couvertes

| Menace | Contrôle principal | Limite résiduelle |
|---|---|---|
| Vol de session web | cookie `HttpOnly`, `Secure`, `SameSite`, TTL, révocation, CSRF | un poste déjà compromis reste hors périmètre |
| Traversée inter-tenant/espace | filtres obligatoires, memberships, UUID immuables, tests d’isolation | les super-administrateurs restent privilégiés |
| Worker usurpé | jeton individuel hashé, TLS, rotation/révocation, `worker_id` lié au secret | protéger le fichier d’environnement du LXC |
| Injection de commande | exécutables/arguments approuvés dans YAML local, aucun argv libre venant du réseau | le harness lui-même peut demander des outils soumis à politique |
| Sortie de workspace | chemin canonique, `realpath`, contrôle des parents et symlinks, sandbox systemd | le service est root dans son LXC ; aucun montage hôte inutile ne doit exister |
| Prompt/tool malveillant | permissions centrales, budgets, approbation humaine, audit | une action explicitement autorisée conserve son risque |
| Rejeu/double exécution | IDs, clés d’idempotence, reçus durables, leases et séquences | traitement au moins une fois, pas exactement une fois réseau |
| Fuite dans les logs | redaction structurée, allowlist de champs, pas de corps par défaut | les sorties d’outils peuvent contenir des secrets et doivent être filtrées |
| SSRF workflow | HTTPS par défaut, DNS/IP privés filtrés, redirections refusées, proxy d’environnement désactivé, timeout/taille | résolution puis connexion séparées : firewall egress requis contre le DNS rebinding |
| Déni de service | limites de taille, débit, concurrence, budgets, timeouts | dimensionnement et quotas LXC restent nécessaires |

## Authentification des utilisateurs

Le premier propriétaire est créé avec un jeton de bootstrap à usage unique, retiré ou invalidé immédiatement après création. Les mots de passe sont hachés avec Argon2id et un paramétrage réévaluable. Une authentification réussie crée une session serveur révocable ; le navigateur ne reçoit qu’un cookie opaque.

En production :

- `Secure`, `HttpOnly`, `SameSite=Lax` au minimum ;
- domaine et chemin de cookie réduits ;
- expiration absolue et expiration d’inactivité ;
- rotation de l’identifiant après connexion ou élévation ;
- CSRF synchronisé ou double-submit lié à la session pour toute mutation ;
- limitation par compte et source, sans journaliser le mot de passe ;
- audit des succès, échecs, révocations et changements de mot de passe.

L’ajout futur d’OIDC, passkeys et MFA passe par l’abstraction d’identité, sans modifier les ACL métier.

## Autorisation et isolation

Chaque requête charge le tenant depuis la session, jamais depuis un en-tête libre. Les identifiants de ressources sont toujours résolus avec `tenant_id`; les ressources d’espace exigent aussi un membership explicite. Un agent Business ne voit Personnel que si une adhésion administrative explicite l’y autorise.

Pour un channel privé, lire l’existence du channel, son historique ou ses membres exige le membership. Les erreurs publiques ne doivent pas permettre de distinguer « absent » de « présent mais interdit » lorsque cette distinction fuit une information.

Les outils `fleet.*` reçoivent un contexte serveur contenant l’agent, la session, le channel, la tâche et la trace. Un champ `agent_id` fourni dans les paramètres ne peut pas remplacer ce contexte.

## Authentification des workers

Chaque worker possède un jeton aléatoire d’au moins 256 bits, affiché une seule fois à l’enregistrement et stocké haché côté serveur. Le jeton est conservé dans `/etc/agent-fleet/worker.env` en mode `0600`, lisible uniquement par root et le service via systemd. Les valeurs ne sont pas placées dans le YAML ni dans la ligne de commande.

L’unité systemd garde `/root` en lecture seule, sauf les répertoires d’état
strictement nécessaires à Codex, Claude et OpenCode (`.codex`, `.claude` et les
répertoires XDG OpenCode). Cela permet aux harness de conserver leurs sessions
et authentifications locales sans rendre tout le home inscriptible.

La passerelle vérifie avant `hello` :

1. TLS et hostname valides ;
2. jeton actif, non expiré et associé au `worker_id` ;
3. worker non révoqué ;
4. version de protocole compatible ;
5. limite de connexion et taille du premier message.

La rotation crée un nouveau secret, accepte une courte période de chevauchement contrôlée puis révoque l’ancien. Une révocation ferme la socket et interdit toute reconnexion. Tailscale ACL ou mTLS peut renforcer TLS mais ne remplace pas le jeton applicatif.

## Protocole WebSocket

- WSS obligatoire hors développement local.
- `Origin` vérifié pour la socket navigateur ; la socket worker n’utilise pas les cookies navigateur.
- taille maximale par message, timeout d’initialisation et heartbeat.
- schémas Pydantic `extra=forbid` et version explicite.
- `message_id`, `command_id`, `idempotency_key`, timestamp et worker obligatoires.
- aucune compression WebSocket pour des messages contenant des secrets lorsque le risque de canal auxiliaire est pertinent.
- fermeture avec un code non ambigu en cas d’authentification, de version ou de limite invalide.

## Workspaces

Les racines sont déclarées localement dans `worker.yaml`, inventoriées et associées ensuite aux espaces/agents dans le plan de contrôle. Une commande réseau ne transporte que `workspace_id`.

Le worker doit :

1. refuser les identifiants inconnus ;
2. appeler `realpath` sur la racine enregistrée et la cible ;
3. vérifier que `commonpath(root, target) == root` ;
4. contrôler chaque création par rapport au parent canonique afin de bloquer les symlinks de sortie ;
5. appliquer `read_only` avant de lancer le harness ;
6. ne jamais suivre un chemin `..` ou absolu fourni par le réseau ;
7. tourner dans un LXC non privilégié dédié ne contenant que les projets autorisés.

L’isolation la plus forte reste un LXC séparé par client ou domaine sensible.

## Harness et processus

Le worker accepte uniquement les adaptateurs `enabled` dont l’exécutable est un chemin absolu local approuvé. Les arguments réseau ne peuvent pas modifier l’exécutable. Les variables d’environnement transmises au processus sont filtrées par `env_allowlist`. Les variables sensibles ne sont jamais renvoyées dans l’inventaire.

Le mode MVP lance un groupe de processus par session. systemd utilise `KillMode=control-group`; le worker impose des délais d’arrêt puis tue le groupe si nécessaire. Des limites CPU, mémoire, processus et fichiers ouverts sont appliquées au service ou au LXC.

## Permissions humaines

Les catégories minimales sont shell, écriture fichier, répertoire additionnel, Git, réseau sensible, délégation et production. Une demande en attente bloque la session. Seules les décisions proposées par le plan de contrôle sont acceptées :

- `deny` : refuse cette demande ;
- `allow_once` : autorise exactement l’identifiant externe courant ;
- `allow_session` : autorise une règle bornée jusqu’à la fin de session ;
- `allow_agent` : crée une politique durable explicite et auditée.

Une politique large ne doit pas être déduite d’un bouton « une fois ». Toute décision conserve auteur, empreinte de la commande/outillage, portée, date et expiration.

## Secrets

Les secrets fournisseurs restent de préférence sur le worker. `OPENAI_API_KEY`, `CODEX_API_KEY` et `ANTHROPIC_API_KEY` sont chargés via un fichier systemd protégé ou un gestionnaire de secrets et ne transitent pas par le plan de contrôle. Une authentification interactive locale ne doit pas être supposée réutilisable par un service tiers.

Interdictions :

- secret dans Git, YAML d’inventaire, message ou tâche ;
- secret dans query string, ligne de commande ou exception publique ;
- dump d’environnement dans un diagnostic ;
- journal du contenu des en-têtes `Authorization`, cookies ou valeurs `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`.

Les sauvegardes contenant la base ou `/etc/agent-fleet` sont chiffrées avec `age`. Les clés de déchiffrement ne résident pas dans le même LXC que les archives.

## Logs et audit

Les logs opérationnels sont structurés et redactent les valeurs sensibles récursivement. Les prompts et sorties terminal ne sont pas journalisés par défaut ; leur représentation persistante est soumise aux ACL de session.

L’audit applicatif est append-only : aucune route ordinaire ne met à jour ou supprime un événement. Il couvre connexion, configuration, agents, memberships, messages, tâches, délégations, permissions, sessions, workers, annulations et erreurs de sécurité. L’accès aux audits est administratif et lui-même audité.

Pour une garantie anti-altération plus forte, exporter périodiquement les événements vers un stockage WORM ou une destination SIEM avec chaînage de hash.

## Réseau et LXC

- LXC Proxmox non privilégié et fonctionnalités de nesting désactivées sauf besoin démontré.
- services exécutés par `root` dans le LXC : un LXC dédié et sans montage hôte est obligatoire.
- aucun montage hôte inutile et pas de socket Docker dans le worker.
- PostgreSQL et Redis écoutent sur loopback ou réseau local du Control Plane uniquement.
- API sur loopback derrière Caddy ; `/metrics` limité au réseau d’administration.
- workers : sortie TCP 443 vers le Control Plane et fournisseurs nécessaires, aucune entrée publique.
- Tailscale avec ACL par tags ou VLAN/firewall équivalent.
- limites Proxmox CPU, mémoire, swap, disque et sauvegardes.

## Web et Caddy

Caddy termine TLS et ajoute HSTS, `X-Content-Type-Options`, `Referrer-Policy`, une politique de permissions et une CSP adaptée. La SPA est servie sans exposer les fichiers cachés. Les endpoints internes et `/metrics` ne sont pas routés publiquement. Les WebSockets conservent les mêmes contrôles d’authentification que HTTP.

## Webhooks

Les destinations sortantes imposent HTTPS par défaut et sont contrôlées syntaxiquement puis après résolution DNS. Les plages loopback, link-local, multicast, documentation, privées et métadonnées cloud sont refusées ; les redirections et proxies d’environnement sont désactivés. Les réponses ont un timeout total et une taille maximale. Les secrets de signature sont référencés, jamais rendus à l’UI.

Le MVP ne pinne pas encore l’adresse IP validée lors de la connexion HTTP. Un DNS hostile pourrait donc tenter un rebinding entre ces deux opérations ; le LXC Control Plane doit appliquer un filtrage egress interdisant les réseaux internes et métadonnées. Aucune allowlist privée n’est livrée tant que cette liaison validation/connexion n’est pas garantie.

## Sauvegardes et restauration

Une restauration est une action destructive explicite. Le script exige un indicateur de confirmation, un sidecar checksum authentifié par une clé HMAC distincte et une archive chiffrée `age`, déchiffre dans un répertoire `0700`, vérifie le catalogue PostgreSQL et crée des points de retour eux-mêmes chiffrés avant `pg_restore --clean`. Les sauvegardes claires sont refusées. Voir [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md).

## Checklist avant exposition

- [ ] domaine public et certificat Caddy valides ;
- [ ] `AGENT_FLEET_ENVIRONMENT=production`, cookie sécurisé et secrets aléatoires ;
- [ ] bootstrap consommé puis révoqué ;
- [ ] PostgreSQL/Redis non exposés publiquement ;
- [ ] ACL Tailscale/firewall testées ;
- [ ] workers avec secrets distincts, rotation et révocation testées ;
- [ ] workspaces minimaux, propriétaires et modes vérifiés ;
- [ ] permissions par défaut `deny` ou `ask` ;
- [ ] `/metrics`, audits et sauvegardes restreints ;
- [ ] restauration testée sur un environnement isolé ;
- [ ] tests d’isolation Business/Personnel et worker révoqué réussis.

## Signalement

Ne placez aucun secret ni donnée client dans un rapport d’incident. Révoquez d’abord les sessions et jetons concernés, conservez les audits, isolez le worker, puis restaurez depuis un point connu si nécessaire.
