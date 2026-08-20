#!/usr/bin/env bash
set -euo pipefail

umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=scripts/lib/lxc-bootstrap-common.sh
source "$SCRIPT_DIR/lib/lxc-bootstrap-common.sh"

SOURCE_DIR=$(pwd -P)
CONTROL_PLANE_URL=
WORKER_ID=
TOKEN_FILE=
PROVIDER_ENV_FILE=
WORKSPACE_ID=
WORKSPACE_ROOT=
WORKSPACE_READ_ONLY=false
HARNESS=codex
REPLACE_CONFIG=false
MANAGE_WORKSPACE_ACL=true
UV_VERSION=0.12.5
CODEX_ACP_VERSION=1.6.2
CLAUDE_ACP_VERSION=0.70.0

usage() {
  cat <<'EOF'
Usage:
  sudo ./scripts/bootstrap-worker-lxc.sh \
    --control-plane-url wss://fleet.example.net/api/v1/workers/connect \
    --worker-id UUID \
    --token-file /root/worker.token \
    --workspace-id mon-projet \
    --workspace-root /srv/projects/mon-projet \
    [options]

Installe et démarre le worker, uv, Node.js si nécessaire, les adaptateurs ACP
choisis, le proxy MCP et l'unité systemd.

Options :
  --source PATH                 dépôt Agent Fleet (défaut: répertoire courant)
  --control-plane-url WSS_URL  endpoint WSS du Control Plane (obligatoire)
  --worker-id UUID             UUID créé dans Runners (obligatoire)
  --token-file PATH            token worker dans un fichier 0600 (obligatoire)
  --provider-env-file PATH     fichier 0600 avec les clés fournisseur autorisées
  --workspace-id ID            identifiant stable du workspace (obligatoire)
  --workspace-root PATH        répertoire de projet existant (obligatoire)
  --read-only                  workspace en lecture seule
  --harness TYPE               codex, claude, both ou fake (défaut: codex)
  --replace-config             remplacer /etc/agent-fleet/worker.yaml
  --no-manage-workspace-acl    ne pas ajouter les ACL du compte worker
  --uv-version VERSION         version uv (défaut: 0.12.5)
  --codex-acp-version VERSION  version Codex ACP (défaut: 1.6.2)
  --claude-acp-version VERSION version Claude ACP (défaut: 0.70.0)
  --help                       afficher cette aide

Le fichier fournisseur accepte uniquement : CODEX_API_KEY, OPENAI_API_KEY,
ANTHROPIC_API_KEY et NO_BROWSER. Les valeurs ne sont jamais affichées.
EOF
}

while (($#)); do
  case "$1" in
    --source)
      (($# >= 2)) || fleet_die "valeur manquante pour --source"
      SOURCE_DIR=$2
      shift 2
      ;;
    --control-plane-url)
      (($# >= 2)) || fleet_die "valeur manquante pour --control-plane-url"
      CONTROL_PLANE_URL=$2
      shift 2
      ;;
    --worker-id)
      (($# >= 2)) || fleet_die "valeur manquante pour --worker-id"
      WORKER_ID=$2
      shift 2
      ;;
    --token-file)
      (($# >= 2)) || fleet_die "valeur manquante pour --token-file"
      TOKEN_FILE=$2
      shift 2
      ;;
    --provider-env-file)
      (($# >= 2)) || fleet_die "valeur manquante pour --provider-env-file"
      PROVIDER_ENV_FILE=$2
      shift 2
      ;;
    --workspace-id)
      (($# >= 2)) || fleet_die "valeur manquante pour --workspace-id"
      WORKSPACE_ID=$2
      shift 2
      ;;
    --workspace-root)
      (($# >= 2)) || fleet_die "valeur manquante pour --workspace-root"
      WORKSPACE_ROOT=$2
      shift 2
      ;;
    --read-only)
      WORKSPACE_READ_ONLY=true
      shift
      ;;
    --harness)
      (($# >= 2)) || fleet_die "valeur manquante pour --harness"
      HARNESS=$2
      shift 2
      ;;
    --replace-config)
      REPLACE_CONFIG=true
      shift
      ;;
    --no-manage-workspace-acl)
      MANAGE_WORKSPACE_ACL=false
      shift
      ;;
    --uv-version)
      (($# >= 2)) || fleet_die "valeur manquante pour --uv-version"
      UV_VERSION=$2
      shift 2
      ;;
    --codex-acp-version)
      (($# >= 2)) || fleet_die "valeur manquante pour --codex-acp-version"
      CODEX_ACP_VERSION=$2
      shift 2
      ;;
    --claude-acp-version)
      (($# >= 2)) || fleet_die "valeur manquante pour --claude-acp-version"
      CLAUDE_ACP_VERSION=$2
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *) fleet_die "option inconnue: $1" ;;
  esac
done

fleet_require_root
fleet_require_supported_system

[[ $CONTROL_PLANE_URL == wss://*/api/v1/workers/connect ]] || \
  fleet_die "--control-plane-url doit être un endpoint wss://.../api/v1/workers/connect"
[[ $WORKER_ID =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$ ]] || \
  fleet_die "UUID worker invalide"
[[ $WORKSPACE_ID =~ ^[a-z0-9][a-z0-9._-]{0,62}$ ]] || fleet_die "workspace-id invalide"
case "$HARNESS" in
  codex|claude|both|fake) ;;
  *) fleet_die "--harness doit valoir codex, claude, both ou fake" ;;
esac
for version in "$UV_VERSION" "$CODEX_ACP_VERSION" "$CLAUDE_ACP_VERSION"; do
  [[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+([+-][A-Za-z0-9.-]+)?$ ]] || \
    fleet_die "version invalide: $version"
done

SOURCE_DIR=$(realpath "$SOURCE_DIR")
[[ -x $SOURCE_DIR/scripts/install-worker.sh ]] || \
  fleet_die "install-worker.sh est absent ou non exécutable dans $SOURCE_DIR"
[[ -f $SOURCE_DIR/uv.lock ]] || fleet_die "uv.lock est absent"

TOKEN_FILE=$(realpath "$TOKEN_FILE")
fleet_assert_secret_file "$TOKEN_FILE"
if [[ -n $PROVIDER_ENV_FILE ]]; then
  PROVIDER_ENV_FILE=$(realpath "$PROVIDER_ENV_FILE")
  fleet_assert_secret_file "$PROVIDER_ENV_FILE"
fi

[[ -d $WORKSPACE_ROOT ]] || fleet_die "workspace absent: $WORKSPACE_ROOT"
WORKSPACE_ROOT=$(realpath -e "$WORKSPACE_ROOT")
[[ $WORKSPACE_ROOT != *$'\n'* && $WORKSPACE_ROOT != *:* && $WORKSPACE_ROOT != *\#* ]] || \
  fleet_die "le chemin du workspace contient un caractère non supporté par le YAML"
[[ $WORKSPACE_ROOT == /srv/projects/* ]] || \
  fleet_die "le workspace doit être placé sous /srv/projects pour respecter le sandbox systemd"
case "$WORKSPACE_ROOT" in
  /|/bin|/boot|/dev|/etc|/home|/opt|/proc|/root|/run|/sbin|/srv|/sys|/usr|/var)
    fleet_die "racine de workspace trop large ou sensible: $WORKSPACE_ROOT"
    ;;
esac

fleet_install_base_packages
export DEBIAN_FRONTEND=noninteractive
apt-get install --yes --no-install-recommends acl iproute2
fleet_install_uv "$UV_VERSION"

codex_executable=
claude_executable=
if [[ $HARNESS != fake ]]; then
  fleet_install_node 22 11.19.0
fi
if [[ $HARNESS == codex || $HARNESS == both ]]; then
  fleet_log "Installation de Codex ACP ${CODEX_ACP_VERSION}"
  npm install --global "@agentclientprotocol/codex-acp@${CODEX_ACP_VERSION}"
  codex_executable=$(command -v codex-acp)
  "$codex_executable" --version
fi
if [[ $HARNESS == claude || $HARNESS == both ]]; then
  fleet_log "Installation de Claude Agent ACP ${CLAUDE_ACP_VERSION}"
  npm install --global "@agentclientprotocol/claude-agent-acp@${CLAUDE_ACP_VERSION}"
  claude_executable=$(command -v claude-agent-acp)
  "$claude_executable" --version
fi

if ! id agent-fleet-worker >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/agent-fleet-worker \
    --shell /usr/sbin/nologin agent-fleet-worker
fi

if [[ $MANAGE_WORKSPACE_ACL == true ]]; then
  fleet_log "Attribution des ACL minimales du workspace au compte agent-fleet-worker"
  parent_path=$(dirname "$WORKSPACE_ROOT")
  while [[ $parent_path != / ]]; do
    setfacl -m u:agent-fleet-worker:--x "$parent_path"
    parent_path=$(dirname "$parent_path")
  done
  if [[ $WORKSPACE_READ_ONLY == true ]]; then
    find "$WORKSPACE_ROOT" -type d -exec setfacl -m u:agent-fleet-worker:rx {} +
    find "$WORKSPACE_ROOT" -type f -exec setfacl -m u:agent-fleet-worker:r-- {} +
    find "$WORKSPACE_ROOT" -type d -exec setfacl -m d:u:agent-fleet-worker:rx {} +
  else
    find "$WORKSPACE_ROOT" -type d -exec setfacl -m u:agent-fleet-worker:rwx {} +
    find "$WORKSPACE_ROOT" -type f -exec setfacl -m u:agent-fleet-worker:rw- {} +
    find "$WORKSPACE_ROOT" -type d -exec setfacl -m d:u:agent-fleet-worker:rwx {} +
  fi
fi

runuser -u agent-fleet-worker -- test -r "$WORKSPACE_ROOT" || \
  fleet_die "agent-fleet-worker ne peut pas lire $WORKSPACE_ROOT"
if [[ $WORKSPACE_READ_ONLY == false ]]; then
  runuser -u agent-fleet-worker -- test -w "$WORKSPACE_ROOT" || \
    fleet_die "agent-fleet-worker ne peut pas écrire dans $WORKSPACE_ROOT"
fi

install -d -o root -g agent-fleet-worker -m 0750 /etc/agent-fleet
worker_config=/etc/agent-fleet/worker.yaml
if [[ ! -e $worker_config || $REPLACE_CONFIG == true ]]; then
  config_tmp=$(mktemp /etc/agent-fleet/.worker.yaml.XXXXXX)
  hostname_value=$(hostname -s)
  [[ $hostname_value =~ ^[A-Za-z0-9._-]+$ ]] || fleet_die "hostname invalide"
  {
    printf 'worker:\n'
    printf '  id: %s\n' "$WORKER_ID"
    printf '  hostname: %s\n' "$hostname_value"
    printf '  labels: [development, git, lxc]\n'
    printf '  max_sessions: 4\n'
    printf '  state_dir: /var/lib/agent-fleet-worker\n\n'
    printf 'control_plane:\n'
    printf '  url: %s\n' "$CONTROL_PLANE_URL"
    printf '  token_env: AGENT_FLEET_WORKER_TOKEN\n'
    printf '  connect_timeout_seconds: 15\n'
    printf '  heartbeat_seconds: 15\n'
    printf '  stale_after_seconds: 45\n'
    printf '  max_message_bytes: 1048576\n'
    printf '  backoff_initial_seconds: 1\n'
    printf '  backoff_max_seconds: 60\n'
    printf '  backoff_jitter_ratio: 0.2\n\n'
    printf 'harnesses:\n'
    if [[ -n $codex_executable ]]; then
      printf '  codex:\n'
      printf '    executable: %s\n' "$codex_executable"
      printf '    args: []\n'
      printf '    enabled: true\n'
      printf '    max_instances: 4\n'
      printf '    env_allowlist: [CODEX_API_KEY, OPENAI_API_KEY, NO_BROWSER]\n'
    fi
    if [[ -n $claude_executable ]]; then
      printf '  claude:\n'
      printf '    executable: %s\n' "$claude_executable"
      printf '    args: []\n'
      printf '    enabled: true\n'
      printf '    max_instances: 2\n'
      printf '    env_allowlist: [ANTHROPIC_API_KEY]\n'
    fi
    if [[ $HARNESS == fake ]]; then
      printf '  fake:\n'
      printf '    executable: /opt/agent-fleet/.venv/bin/python\n'
      printf '    args: [-m, services.worker.fake_acp]\n'
      printf '    enabled: true\n'
      printf '    max_instances: 4\n'
      printf '    env_allowlist: []\n'
      printf '    version_args: [-m, services.worker.fake_acp, --version]\n'
    fi
    printf '\nworkspaces:\n'
    printf '  - id: %s\n' "$WORKSPACE_ID"
    printf '    display_name: %s\n' "$WORKSPACE_ID"
    printf '    root: %s\n' "$WORKSPACE_ROOT"
    printf '    read_only: %s\n\n' "$WORKSPACE_READ_ONLY"
    printf 'mcp_proxy:\n'
    printf '  enabled: true\n'
    printf '  executable: /opt/agent-fleet/.venv/bin/python\n'
    printf '  args: [-m, services.fleet_mcp_proxy]\n'
    printf '  request_timeout_seconds: 60\n'
    printf '  token_ttl_seconds: 86400\n'
  } > "$config_tmp"
  install -o root -g agent-fleet-worker -m 0640 "$config_tmp" "$worker_config"
  rm -f -- "$config_tmp"
else
  fleet_log "Configuration worker existante conservée; utilisez --replace-config pour la remplacer"
fi

worker_env=/etc/agent-fleet/worker.env
if [[ ! -e $worker_env ]]; then
  env_tmp=$(mktemp /etc/agent-fleet/.worker.env.XXXXXX)
  printf 'AGENT_FLEET_WORKER_TOKEN=REPLACE_WITH_INSTALLER\n' > "$env_tmp"
  printf 'AGENT_FLEET_LOG_LEVEL=INFO\n' >> "$env_tmp"
  if [[ -n $PROVIDER_ENV_FILE ]]; then
    while IFS= read -r line || [[ -n $line ]]; do
      trimmed=${line#"${line%%[![:space:]]*}"}
      [[ -z $trimmed || $trimmed == \#* ]] && continue
      key=${line%%=*}
      [[ $line == *=* ]] || fleet_die "ligne fournisseur invalide"
      case "$key" in
        CODEX_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|NO_BROWSER) ;;
        *) fleet_die "variable fournisseur interdite: $key" ;;
      esac
      printf '%s\n' "$line" >> "$env_tmp"
    done < "$PROVIDER_ENV_FILE"
  fi
  install -o root -g agent-fleet-worker -m 0600 "$env_tmp" "$worker_env"
  rm -f -- "$env_tmp"
elif [[ -n $PROVIDER_ENV_FILE ]]; then
  fleet_die "$worker_env existe déjà; fusionnez manuellement le fichier fournisseur pour éviter d'écraser un secret"
fi

health_url=${CONTROL_PLANE_URL/wss:\/\//https:\/\/}
health_url=${health_url%/api/v1/workers/connect}/api/v1/health
fleet_log "Vérification du Control Plane: $health_url"
curl --fail --silent --show-error --max-time 15 "$health_url" >/dev/null

fleet_log "Installation et activation du worker"
"$SOURCE_DIR/scripts/install-worker.sh" \
  --source "$SOURCE_DIR" \
  --control-plane-url "$CONTROL_PLANE_URL" \
  --worker-id "$WORKER_ID" \
  --token-file "$TOKEN_FILE" \
  --activate

systemctl is-active --quiet agent-fleet-worker.service || \
  fleet_die "le service worker n'est pas actif"

fleet_log "Worker installé et démarré"
printf '\nConfiguration: %s\n' "$worker_config"
printf 'Secrets: %s\n' "$worker_env"
printf 'Vérifiez son état dans Runners et avec :\n'
printf '  journalctl -u agent-fleet-worker -f\n'
printf 'Le fichier token source est encore présent; supprimez-le après vérification.\n'
