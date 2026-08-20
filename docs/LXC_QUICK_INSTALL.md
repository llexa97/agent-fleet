# Installation rapide sur LXC

Ces deux scripts installent les dépendances système et Agent Fleet directement
dans des LXC Debian ou Ubuntu utilisant systemd. Docker n'est pas utilisé.

Ils téléchargent des paquets depuis les dépôts APT Debian/Ubuntu, Caddy et
NodeSource, ainsi que `uv` depuis Astral et les adaptateurs ACP depuis npm.
Exécutez-les d'abord sur des LXC de recette non privilégiés et sauvegardés.
Les processus Agent Fleet sont exécutés par `root` dans leur LXC et aucun
compte Linux applicatif supplémentaire n'est créé. N'ajoutez donc aucun montage
hôte, socket Docker ou secret sans rapport avec Agent Fleet dans ces LXC.

## 1. Avant de commencer

Préparer au minimum :

- un LXC Control Plane avec 4 vCPU, 8 Gio de RAM et 40 Gio de disque ;
- un LXC Worker avec 4 vCPU, 8 Gio de RAM et les projets nécessaires ;
- un domaine HTTPS pointant vers le Control Plane ;
- TCP 443 autorisé vers le Control Plane ;
- le dépôt Agent Fleet copié dans chaque LXC, par exemple sous
  `/srv/agent-fleet-src`.

Depuis la machine de développement, une copie peut être faite avec :

```bash
rsync -a --delete \
  --exclude .git --exclude .venv --exclude node_modules \
  ./ root@ADRESSE_LXC:/srv/agent-fleet-src/
```

Vérifier le chemin de destination avant d'utiliser `--delete`.

## 2. Control Plane

Dans le LXC Control Plane :

```bash
cd /srv/agent-fleet-src

sudo ./scripts/bootstrap-control-plane-lxc.sh \
  --source "$PWD" \
  --domain fleet.example.net
```

Pour un nom résolu uniquement par le DNS privé, ajouter `--internal-tls`. Il
faudra alors distribuer et approuver la CA racine Caddy sur les navigateurs.

Le script installe et configure :

- PostgreSQL lié à `127.0.0.1` ;
- Redis protégé et lié localement ;
- Caddy et HTTPS ;
- Node.js 22 et pnpm ;
- `uv` et Python 3.12 géré par `uv` ;
- les migrations Alembic ;
- l'API, le dispatcher et le frontend ;
- les unités systemd.

Il génère les secrets de session, de base et de bootstrap sans les afficher.
Ils sont conservés dans :

```text
/etc/agent-fleet/control-plane.env
```

Ouvrir ensuite `https://fleet.example.net`, lire le token de bootstrap avec
`sudoedit /etc/agent-fleet/control-plane.env`, puis créer le premier compte.
Après le bootstrap, remplacer le token dans ce fichier et redémarrer l'API.

Vérification :

```bash
curl --fail https://fleet.example.net/api/v1/health
curl --fail https://fleet.example.net/api/v1/readiness
systemctl status agent-fleet-api agent-fleet-dispatcher caddy --no-pager
```

## 3. Créer le worker dans Agent Fleet

Dans la web app :

1. ouvrir **Runners** ;
2. créer un worker ;
3. copier son UUID et son token, affiché une seule fois ;
4. enregistrer le token dans un fichier temporaire du LXC Worker.

```bash
sudo install -m 0600 /dev/null /root/agent-fleet-worker.token
sudoedit /root/agent-fleet-worker.token
```

## 4. Secrets fournisseur du worker

Cette étape est facultative pour le faux harness, mais nécessaire pour une
authentification API Codex ou Claude :

```bash
sudo install -m 0600 /dev/null /root/agent-fleet-provider.env
sudoedit /root/agent-fleet-provider.env
```

Exemple Codex :

```dotenv
OPENAI_API_KEY=remplacer
NO_BROWSER=1
```

Exemple Claude :

```dotenv
ANTHROPIC_API_KEY=remplacer
```

Le fichier n'accepte que `CODEX_API_KEY`, `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY` et `NO_BROWSER`.

## 5. Worker

Le projet doit déjà exister sur le LXC, par exemple :

```text
/srv/projects/fleetbase-ui
```

Les workspaces automatisés sont volontairement limités à un sous-répertoire de
`/srv/projects`, qui correspond à la sandbox de l'unité systemd du worker.

Puis lancer :

```bash
cd /srv/agent-fleet-src

sudo ./scripts/bootstrap-worker-lxc.sh \
  --source "$PWD" \
  --control-plane-url wss://fleet.example.net/api/v1/workers/connect \
  --worker-id 'UUID_RETOURNE_PAR_RUNNERS' \
  --token-file /root/agent-fleet-worker.token \
  --provider-env-file /root/agent-fleet-provider.env \
  --workspace-id fleetbase-ui \
  --workspace-root /srv/projects/fleetbase-ui \
  --harness codex
```

Valeurs possibles pour `--harness` :

- `codex` ;
- `claude` ;
- `both` ;
- `fake`, pour tester sans fournisseur ni token IA.

Le worker tourne sous `root` dans son LXC. Utiliser `--read-only` pour interdire
les écritures applicatives et réserver ce LXC aux seuls projets autorisés.

Après vérification du worker dans Runners, supprimer les fichiers temporaires :

```bash
sudo shred -u /root/agent-fleet-worker.token \
  /root/agent-fleet-provider.env
```

Sur un stockage copy-on-write, `shred` ne garantit pas l'effacement physique ;
les fichiers doivent néanmoins être supprimés. Les copies actives restent dans
`/etc/agent-fleet/worker.env`, protégé en mode `0600`.

## 6. Deuxième worker

Créer un second worker dans Runners et répéter la procédure sur un autre LXC
avec son propre UUID, son propre token et ses propres workspaces.

## 7. Diagnostic

```bash
# Control Plane
journalctl -u agent-fleet-api -u agent-fleet-dispatcher -f

# Worker
journalctl -u agent-fleet-worker -f

# État général
systemctl --failed
```

Le worker doit apparaître `online` dans Runners. Ne contournez jamais une erreur
TLS avec une option insecure : corrigez le DNS, le certificat ou l'autorité de
certification.
