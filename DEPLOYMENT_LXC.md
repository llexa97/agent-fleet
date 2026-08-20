# Déploiement sur LXC Proxmox

Ce guide cible Debian 12/13 ou Ubuntu 24.04 dans des [LXC Proxmox non privilégiés](https://pve.proxmox.com/pve-docs/pve-admin-guide.html#pct_unprivileged). Les commandes utilisent `uv` pour toutes les dépendances Python. Aucun LXC privilégié, Docker ou accès SSH du Control Plane vers les workers n’est requis en production.

## Topologie recommandée

```text
LXC control-plane
  Caddy :443
  API :127.0.0.1:8000
  dispatcher
  PostgreSQL :127.0.0.1:5432
  Redis :127.0.0.1:6379

LXC worker-a                         LXC worker-b
  worker -> WSS :443                  worker -> WSS :443
  codex-acp / faux ACP                claude-agent-acp / faux ACP
  /srv/projects/...                   /srv/projects/...
```

## Installation automatisée

Deux scripts tout-en-un installent aussi les paquets du système, puis appellent
les installateurs applicatifs sécurisés :

```bash
# Dans le LXC Control Plane
sudo ./scripts/bootstrap-control-plane-lxc.sh \
  --source "$PWD" \
  --domain fleet.example.net

# Dans chaque LXC Worker, après création du runner dans la web app
sudo ./scripts/bootstrap-worker-lxc.sh \
  --source "$PWD" \
  --control-plane-url wss://fleet.example.net/api/v1/workers/connect \
  --worker-id UUID \
  --token-file /root/worker.token \
  --workspace-id mon-projet \
  --workspace-root /srv/projects/mon-projet \
  --harness codex
```

Voir [le guide rapide](docs/LXC_QUICK_INSTALL.md) pour la copie du dépôt, les
secrets fournisseur, Claude, les ACL et les vérifications. Les sections
suivantes restent le guide de référence pour comprendre et personnaliser chaque
étape.

Pour un premier usage, 4 vCPU, 8 Gio de RAM et 40 Gio de disque conviennent au Control Plane. Un worker démarre raisonnablement à 4 vCPU, 8 Gio et un disque dimensionné pour ses projets ; les modèles/harness et builds peuvent nécessiter davantage. Fixer des limites Proxmox plutôt que du swap illimité.

## Préparer les LXC

Dans Proxmox :

1. créer des conteneurs non privilégiés ;
2. désactiver nesting, keyctl et montages hôte sauf besoin démontré ;
3. ne monter dans un worker que les projets qu’il doit voir ;
4. attribuer IP privées stables ou noms Tailscale ;
5. activer sauvegardes Proxmox, limites CPU/RAM et firewall ;
6. synchroniser l’heure avec l’hôte/NTP.

Le Control Plane accepte en entrée TCP 443 depuis les utilisateurs et workers. Son SSH d’administration reste limité au réseau privé. PostgreSQL, Redis et le port 8000 ne sont jamais exposés. Un worker n’a besoin d’aucune entrée publique ; sa sortie minimale est TCP 443 vers le Control Plane et vers les fournisseurs explicitement utilisés, plus DNS/NTP.

## Réseau privé avec Tailscale

Tailscale est recommandé mais un VLAN correctement filtré convient. Suivre l’[installation Linux officielle](https://tailscale.com/kb/1031/install-linux), puis taguer les machines (`tag:agent-fleet-control`, `tag:agent-fleet-worker`). Une ACL indicative autorise les utilisateurs vers `control:443` et les workers uniquement vers `control:443`. Les tags et groupes réels appartiennent à votre tailnet.

Utiliser un nom MagicDNS, par exemple `agent-fleet-control.<tailnet>.ts.net`, dans Caddy et `control_plane.url`. Tailscale ne remplace ni TLS applicatif ni les jetons worker.

## LXC Control Plane

### Paquets et utilisateurs

Installer les outils de base, PostgreSQL, Redis, Caddy, Node.js 20+ et `pnpm`. Pour Caddy et Node, suivre leurs dépôts officiels afin de ne pas dépendre des versions anciennes d’une distribution :

- [paquets Caddy Debian/Ubuntu](https://caddyserver.com/docs/install#debian-ubuntu-raspbian) ;
- [Node.js et Corepack](https://nodejs.org/api/corepack.html) ;
- [installation uv](https://docs.astral.sh/uv/getting-started/installation/).

Le script automatise la copie, les comptes, `uv sync`, le build web et systemd après installation de ces prérequis :

```bash
sudo apt update
sudo apt install --yes \
  ca-certificates curl gnupg openssl rsync jq age \
  postgresql postgresql-client redis-server

# Installer uv dans un chemin système, puis vérifier le binaire.
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
uv --version

# Après installation de Node.js 20+ depuis une source officielle :
sudo corepack enable
sudo corepack prepare pnpm@11.19.0 --activate
node --version
pnpm --version
```

Installer ensuite Caddy depuis son dépôt officiel et vérifier `caddy version`. Pour un environnement réglementé, épingler et vérifier hors bande les paquets/scripts téléchargés plutôt que d’exécuter directement une URL.

Puis lancer l’installateur Agent Fleet :

```bash
sudo ./scripts/install-control-plane.sh \
  --source "$PWD" \
  --domain agent-fleet-control.example.net
```

Il ne démarre pas les services tant que la configuration n’est pas validée. Les chemins sont :

```text
/opt/agent-fleet                 code et .venv immuables pour le service
/etc/agent-fleet/control-plane.env  secrets/configuration, 0600
/var/lib/agent-fleet             état applicatif éventuel
/var/backups/agent-fleet         archives chiffrées
```

### PostgreSQL

Créer un rôle avec mot de passe saisi interactivement et une base dédiée :

```bash
sudo -u postgres createuser --pwprompt agent_fleet
sudo -u postgres createdb --owner=agent_fleet --encoding=UTF8 agent_fleet
```

Configurer `listen_addresses = '127.0.0.1'` et une règle `pg_hba.conf` limitée à la base et l’utilisateur locaux. Redémarrer PostgreSQL puis tester avec `psql` sans placer le mot de passe dans l’historique shell.

PostgreSQL doit avoir des sauvegardes indépendantes et, pour un produit B2B, une stratégie PITR/WAL. Le script fourni produit un dump logique cohérent.

### Redis

Redis est reconstructible. Le lier à `127.0.0.1`, conserver `protected-mode yes` et désactiver toute exposition firewall. Une politique de mémoire avec éviction est acceptable, car aucune donnée métier critique n’y vit.

### Variables

Copier le modèle et remplacer chaque valeur :

```bash
sudo install -m 0600 -o root -g root \
  infra/systemd/control-plane.env.example \
  /etc/agent-fleet/control-plane.env
sudoedit /etc/agent-fleet/control-plane.env
```

Les valeurs obligatoires sont notamment l’URL PostgreSQL asyncpg, Redis, l’URL publique HTTPS, l’origine web, un secret de session aléatoire et un token de bootstrap à usage unique. Générer des secrets sans les afficher dans les logs :

```bash
openssl rand -base64 48
```

Le mot de passe PostgreSQL dans une URL doit être encodé pour URI. Le fichier reste `0600`; ne le sauvegarder que dans une archive chiffrée.

### Migrations

Toujours sauvegarder avant migration, puis :

```bash
sudo systemctl start agent-fleet-migrate.service
sudo systemctl status agent-fleet-migrate.service --no-pager
```

L’unité systemd charge directement l’`EnvironmentFile` sans placer l’URL de base ou ses secrets dans la ligne de commande. Utilisez-la aussi pour les migrations manuelles, avec `systemctl restart agent-fleet-migrate.service` si elle a déjà été exécutée.

### Caddy

Installer le fichier puis définir le domaine dans l’environnement de l’unité Caddy :

```bash
sudo install -m 0644 infra/caddy/Caddyfile /etc/caddy/Caddyfile
sudo install -d -m 0755 /etc/systemd/system/caddy.service.d
sudo install -m 0644 infra/caddy/agent-fleet.conf \
  /etc/systemd/system/caddy.service.d/agent-fleet.conf
sudo systemctl daemon-reload
sudo caddy validate --config /etc/caddy/Caddyfile
```

Caddy obtient et renouvelle le certificat si le domaine public/DNS est joignable. Pour un nom Tailscale, utiliser le certificat/HTTPS prévu par votre tailnet ou une PKI privée correctement distribuée. HTTPS/WSS reste obligatoire.

### Démarrage

```bash
sudo systemctl enable --now postgresql redis-server caddy
sudo systemctl enable --now agent-fleet-control-plane.target
curl --fail --silent https://agent-fleet-control.example.net/api/v1/health
curl --fail --silent https://agent-fleet-control.example.net/api/v1/readiness
```

Les métriques `/metrics` ne sont pas routées par le Caddyfile public ; collecter sur `127.0.0.1:8000` ou via un réseau d’administration.

### Premier propriétaire

Le bootstrap ne fonctionne qu’avant la création du propriétaire. Ne mettez pas le token dans l’historique. L’interface le demande ou l’appel API peut lire le token dans un fichier temporaire :

```bash
read -r -s -p 'Token de bootstrap: ' BOOTSTRAP_TOKEN
printf '\n'
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  -H "X-Bootstrap-Token: ${BOOTSTRAP_TOKEN}" \
  --data '{"email":"axel@example.net","display_name":"Axel","password":"remplacer-par-un-mot-de-passe-long","tenant_name":"Agent Fleet"}' \
  https://agent-fleet-control.example.net/api/v1/auth/bootstrap
unset BOOTSTRAP_TOKEN
```

La commande illustre le contrat, mais saisir aussi le mot de passe de manière non historique est préférable via la web app. Après succès, remplacer/invalider `AGENT_FLEET_BOOTSTRAP_TOKEN` et redémarrer l’API.

## LXC Worker

### Exécution root et projets

Le worker est exécuté par `root` dans son LXC et l'installateur ne crée aucun
compte Linux applicatif. Le LXC doit donc être non privilégié, dédié à Agent
Fleet et ne monter que les projets strictement nécessaires. Ne montez jamais
`/`, `/root`, une socket Docker ni des clés SSH générales de l'hôte.

### Installation

Après Python 3.12, `uv` et les outils nécessaires au harness :

```bash
sudo apt update
sudo apt install --yes ca-certificates curl rsync git openssl
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
uv --version
```

Le worker n’a pas besoin des paquets PostgreSQL/Redis/Caddy. Installer Node.js uniquement si l’adaptateur choisi l’exige. Le script Agent Fleet ne fabrique pas de credential : enregistrer d’abord le worker depuis l’UI/API. Le Control Plane retourne son UUID et un jeton une seule fois. Placer le jeton seul dans un fichier temporaire `0600`, puis lancer :

```bash
sudo install -m 0600 /dev/null /root/agent-fleet-worker.token
sudoedit /root/agent-fleet-worker.token
sudo ./scripts/install-worker.sh \
  --source "$PWD" \
  --control-plane-url wss://agent-fleet-control.example.net/api/v1/workers/connect \
  --worker-id '<uuid-retourne>' \
  --token-file /root/agent-fleet-worker.token
sudo shred -u /root/agent-fleet-worker.token 2>/dev/null || \
  sudo find /root/agent-fleet-worker.token -delete
```

Sur stockage flash ou copy-on-write, `shred` ne garantit pas l’effacement physique ; le jeton demeure dans `/etc/agent-fleet/worker.env` protégé et le fichier temporaire doit être supprimé. Éditer ensuite `/etc/agent-fleet/worker.yaml`.

Le YAML ne contient pas de secret :

```yaml
worker:
  id: a1000000-0000-4000-8000-000000000001
  labels: [development, git, client-projects]
  max_sessions: 4
  state_dir: /var/lib/agent-fleet-worker
control_plane:
  url: wss://agent-fleet-control.example.net/api/v1/workers/connect
  token_env: AGENT_FLEET_WORKER_TOKEN
  heartbeat_seconds: 15
harnesses:
  codex:
    executable: /usr/local/bin/codex-acp
    args: []
    enabled: true
    max_instances: 4
    env_allowlist: [CODEX_API_KEY, OPENAI_API_KEY, NO_BROWSER]
workspaces:
  - id: fleetbase-ui
    display_name: Fleetbase UI
    root: /srv/projects/fleetbase-ui
    read_only: false
```

Valider les chemins comme `root`, qui est l’utilisateur du service :

```bash
sudo test -x /usr/local/bin/codex-acp
sudo test -r /srv/projects/fleetbase-ui
sudo test -w /srv/projects/fleetbase-ui
```

### Adaptateurs et secrets

Installer des versions exactes des paquets officiels, puis enregistrer les chemins absolus :

```bash
sudo npm install --global @agentclientprotocol/codex-acp@<version-testee>
sudo npm install --global @agentclientprotocol/claude-agent-acp@<version-testee>
command -v codex-acp claude-agent-acp
```

Les références officielles et méthodes d’authentification sont dans [ACP_INTEGRATION.md](ACP_INTEGRATION.md). Les clés fournisseur restent dans `/etc/agent-fleet/worker.env` en mode `0600`; elles ne sont ni inventoriées ni envoyées au Control Plane.

### Démarrage et diagnostic

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now agent-fleet-worker.service
sudo systemctl status agent-fleet-worker.service --no-pager
sudo journalctl -u agent-fleet-worker.service -f
```

Vérifier ensuite dans Runners : worker en ligne, version protocole `1.0`, harness détectés, capacité et workspaces exacts. Une erreur de certificat ne doit jamais être contournée avec une option « insecure » en production.

### Révocation

Révoquer le credential depuis le Control Plane, vérifier que la socket ferme et que le worker ne se reconnecte plus, puis arrêter le service et effacer le secret local :

```bash
sudo systemctl disable --now agent-fleet-worker.service
sudoedit /etc/agent-fleet/worker.env
```

Ne réutiliser jamais un jeton révoqué pour un nouveau worker.

## Développement local

Le Compose est détaillé dans [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). Il lance PostgreSQL, Redis, l’API, le dispatcher, la web app et deux workers faux avec `uv` dans l’image Python.

```bash
docker compose -f infra/compose/docker-compose.yml up --build
```

Les secrets et tokens intégrés sont strictement réservés à loopback et à la démonstration.

## Mise à jour

1. Lire les notes de version et prendre une sauvegarde chiffrée.
2. Copier la nouvelle révision dans un nouveau répertoire, par exemple `/opt/agent-fleet-releases/<version>`; l’installateur utilise `/opt/agent-fleet` comme lien atomique vers la release active.
3. Exécuter `uv sync --frozen --no-dev` et `pnpm install --frozen-lockfile && pnpm --dir apps/web build`.
4. Tester migration `upgrade` et, si disponible, chemin de downgrade sur une restauration temporaire.
5. Exécuter l’installateur avec `--activate` : il bascule atomiquement `/opt/agent-fleet`, exécute la migration via systemd, redémarre et exige une readiness réussie.
6. Mettre à jour les workers progressivement, un canari d’abord.
7. Vérifier readiness, métriques, connexions, livraisons en attente et erreurs.

Ne remplacez pas les fichiers de configuration/secrets lors d’une mise à jour.

## Rollback

Un rollback applicatif consiste à arrêter dispatcher/API, repointer vers la release précédente, restaurer la base si la migration n’est pas rétrocompatible, puis redémarrer. Ne lancez jamais un ancien binaire sur un schéma inconnu « pour voir ». Les sessions actives peuvent devoir être marquées en reprise/échec explicite.

```bash
sudo systemctl stop agent-fleet-dispatcher agent-fleet-api
# restaurer la base si requis, puis repointer la release validée de façon atomique
sudo ln -s /opt/agent-fleet-releases/<release-precedente> /opt/agent-fleet.next
sudo mv -Tf /opt/agent-fleet.next /opt/agent-fleet
sudo systemctl start agent-fleet-api agent-fleet-dispatcher
curl --fail --silent https://agent-fleet-control.example.net/api/v1/readiness
```

## Sauvegardes et restauration

Voir [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md). Tester au moins trimestriellement une restauration sur un LXC isolé et conserver le rapport (date, archive, version, checksum, durée, résultat) sans secret.

## Vérifications post-installation

- API health/readiness et login ;
- Caddy TLS et WebSocket navigateur ;
- PostgreSQL/Redis inaccessibles depuis un autre hôte ;
- deux workers connectés uniquement en sortie ;
- faux ACP : Axel → `@cto` → `@backend-dev` ;
- permission en attente puis refus ;
- worker révoqué refusé ;
- workspace inconnu et symlink de sortie refusés ;
- redémarrage Redis sans perte de message/livraison ;
- sauvegarde et restauration temporaire réussies.
