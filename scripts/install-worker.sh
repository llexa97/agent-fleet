#!/usr/bin/env bash
set -euo pipefail

umask 077

SOURCE_DIR="$(pwd -P)"
INSTALL_LINK=/opt/agent-fleet
RELEASES_DIR=/opt/agent-fleet-releases
CONFIG_DIR=/etc/agent-fleet
STATE_DIR=/var/lib/agent-fleet-worker
CACHE_DIR=/var/cache/agent-fleet-worker
PYTHON_DIR=/opt/agent-fleet-python
CONTROL_PLANE_URL=
WORKER_ID=
TOKEN_FILE=
ACTIVATE=false

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/install-worker.sh --control-plane-url WSS_URL --worker-id UUID [options]

Options:
  --source PATH              dépôt source (défaut: répertoire courant)
  --control-plane-url URL    endpoint wss://.../api/v1/workers/connect
  --worker-id UUID           UUID retourné par le Control Plane (obligatoire)
  --token-file PATH          fichier 0600 contenant le jeton sur une ligne
  --activate                 activer/démarrer (requiert --token-file ou env existant)
  --help                     afficher cette aide

Le jeton n'est jamais accepté en argument direct afin de ne pas apparaître dans `ps`.
EOF
}

die() {
  printf 'Erreur: %s\n' "$*" >&2
  exit 1
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || die "commande requise absente: $1"
}

while (($#)); do
  case "$1" in
    --source)
      (($# >= 2)) || die "valeur manquante pour --source"
      SOURCE_DIR=$2
      shift 2
      ;;
    --control-plane-url)
      (($# >= 2)) || die "valeur manquante pour --control-plane-url"
      CONTROL_PLANE_URL=$2
      shift 2
      ;;
    --worker-id)
      (($# >= 2)) || die "valeur manquante pour --worker-id"
      WORKER_ID=$2
      shift 2
      ;;
    --token-file)
      (($# >= 2)) || die "valeur manquante pour --token-file"
      TOKEN_FILE=$2
      shift 2
      ;;
    --activate)
      ACTIVATE=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "option inconnue: $1"
      ;;
  esac
done

[[ ${EUID} -eq 0 ]] || die "ce script doit être exécuté avec sudo/root"
[[ $CONTROL_PLANE_URL == wss://* ]] || die "--control-plane-url doit utiliser wss://"
[[ -n $WORKER_ID ]] || die "--worker-id retourné par le Control Plane est obligatoire"

SOURCE_DIR=$(realpath "$SOURCE_DIR")
[[ -f "$SOURCE_DIR/pyproject.toml" && -f "$SOURCE_DIR/uv.lock" ]] || \
  die "le répertoire source n'est pas un dépôt Agent Fleet"

for command_name in realpath rsync uv systemctl install; do
  need_command "$command_name"
done

worker_was_active=false
if systemctl is-active --quiet agent-fleet-worker.service 2>/dev/null; then
  worker_was_active=true
fi
if [[ $worker_was_active == true && $ACTIVATE != true ]]; then
  die "le worker est actif; relancez avec --activate pour effectuer un basculement contrôlé"
fi

[[ $WORKER_ID =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$ ]] || \
  die "UUID worker invalide"

if [[ -n $TOKEN_FILE ]]; then
  TOKEN_FILE=$(realpath "$TOKEN_FILE")
  [[ -f $TOKEN_FILE ]] || die "fichier de jeton absent"
  if [[ -n $(find "$TOKEN_FILE" -prune -perm /077 -print) ]]; then
    die "le fichier de jeton ne doit être lisible que par son propriétaire"
  fi
  worker_token=$(head -n 1 "$TOKEN_FILE")
  [[ $worker_token =~ ^[A-Za-z0-9._~-]{32,512}$ ]] || \
    die "le jeton doit être URL-safe et contenir 32 à 512 caractères"
fi

install -d -o root -g root -m 0755 "$RELEASES_DIR"
install -d -o root -g root -m 0755 "$PYTHON_DIR"
install -d -o root -g root -m 0700 "$STATE_DIR" "$CACHE_DIR" "$CONFIG_DIR"

release_id=$(date -u +%Y%m%dT%H%M%SZ)
release_dir="$RELEASES_DIR/$release_id"
[[ ! -e "$release_dir" ]] || die "release déjà existante: $release_dir"
install -d -o root -g root -m 0755 "$release_dir"

rsync -a --delete \
  --exclude=.git \
  --exclude=.venv \
  --exclude=.env \
  --exclude=node_modules \
  --exclude=playwright-report \
  --exclude=test-results \
  --exclude='*.db' \
  "$SOURCE_DIR/" "$release_dir/"
env \
  UV_CACHE_DIR="$CACHE_DIR/uv" \
  UV_PYTHON_INSTALL_DIR="$PYTHON_DIR" \
  uv python install 3.12
chown -R root:root "$PYTHON_DIR"
chmod -R u+rwX,go-rwx "$PYTHON_DIR"
env \
  UV_CACHE_DIR="$CACHE_DIR/uv" \
  UV_PYTHON_INSTALL_DIR="$PYTHON_DIR" \
  uv --directory "$release_dir" sync --frozen --no-dev
chown -R root:root "$release_dir/.venv"
chmod -R u+rwX,go+rX "$release_dir/.venv"

if [[ ! -e "$CONFIG_DIR/worker.yaml" ]]; then
  config_tmp=$(mktemp "$CONFIG_DIR/.worker.yaml.XXXXXX")
  while IFS= read -r line || [[ -n $line ]]; do
    case "$line" in
      '  id: 00000000-0000-4000-8000-000000000001')
        printf '  id: %s\n' "$WORKER_ID"
        ;;
      '  url: wss://agent-fleet.example.net/api/v1/workers/connect')
        printf '  url: %s\n' "$CONTROL_PLANE_URL"
        ;;
      *)
        printf '%s\n' "$line"
        ;;
    esac
  done < "$release_dir/infra/systemd/worker.example.yaml" > "$config_tmp"
  chown root:root "$config_tmp"
  chmod 0600 "$config_tmp"
  mv "$config_tmp" "$CONFIG_DIR/worker.yaml"
fi
chown root:root "$CONFIG_DIR/worker.yaml"
chmod 0600 "$CONFIG_DIR/worker.yaml"

if [[ ! -e "$CONFIG_DIR/worker.env" ]]; then
  env_tmp=$(mktemp "$CONFIG_DIR/.worker.env.XXXXXX")
  if [[ -n ${worker_token:-} ]]; then
    printf 'AGENT_FLEET_WORKER_TOKEN=%s\n' "$worker_token" > "$env_tmp"
    printf 'AGENT_FLEET_LOG_LEVEL=INFO\n' >> "$env_tmp"
  else
    install -m 0600 "$release_dir/infra/systemd/worker.env.example" "$env_tmp"
  fi
  chown root:root "$env_tmp"
  chmod 0600 "$env_tmp"
  mv "$env_tmp" "$CONFIG_DIR/worker.env"
elif [[ -n ${worker_token:-} ]]; then
  env_tmp=$(mktemp "$CONFIG_DIR/.worker.env.XXXXXX")
  token_written=false
  while IFS= read -r line || [[ -n $line ]]; do
    case "$line" in
      AGENT_FLEET_WORKER_TOKEN=*)
        printf 'AGENT_FLEET_WORKER_TOKEN=%s\n' "$worker_token"
        token_written=true
        ;;
      *)
        printf '%s\n' "$line"
        ;;
    esac
  done < "$CONFIG_DIR/worker.env" > "$env_tmp"
  if [[ $token_written != true ]]; then
    printf 'AGENT_FLEET_WORKER_TOKEN=%s\n' "$worker_token" >> "$env_tmp"
  fi
  chown root:root "$env_tmp"
  chmod 0600 "$env_tmp"
  mv "$env_tmp" "$CONFIG_DIR/worker.env"
fi
unset worker_token || true
chown root:root "$CONFIG_DIR/worker.env"
chmod 0600 "$CONFIG_DIR/worker.env"
if [[ $ACTIVATE == true ]] && grep -q 'REPLACE_WITH' "$CONFIG_DIR/worker.env"; then
  die "jeton absent; installez-le via --token-file avant --activate"
fi

env \
  PYTHONPATH="$release_dir" \
  AGENT_FLEET_EXPECTED_WORKER_ID="$WORKER_ID" \
  AGENT_FLEET_EXPECTED_CONTROL_URL="$CONTROL_PLANE_URL" \
  AGENT_FLEET_CONFIG_PATH="$CONFIG_DIR/worker.yaml" \
  "$release_dir/.venv/bin/python" -c '
import os
from pathlib import Path
from services.worker.config import load_worker_config

config = load_worker_config(Path(os.environ["AGENT_FLEET_CONFIG_PATH"]))
if str(config.worker.id) != os.environ["AGENT_FLEET_EXPECTED_WORKER_ID"]:
    raise SystemExit("worker.id ne correspond pas à --worker-id")
if config.control_plane.url != os.environ["AGENT_FLEET_EXPECTED_CONTROL_URL"]:
    raise SystemExit("control_plane.url ne correspond pas à --control-plane-url")
' >/dev/null

if command -v systemd-analyze >/dev/null 2>&1; then
  systemd_verify_dir=$(mktemp -d)
  sed "s|/opt/agent-fleet|$release_dir|g" \
    "$release_dir/infra/systemd/agent-fleet-worker.service" \
    > "$systemd_verify_dir/agent-fleet-worker.service"
  if ! systemd-analyze verify \
    "$systemd_verify_dir/agent-fleet-worker.service" >/dev/null; then
    rm -r -- "$systemd_verify_dir"
    die "validation de l'unité systemd du worker en échec"
  fi
  rm -r -- "$systemd_verify_dir"
fi

install -m 0644 "$release_dir/infra/systemd/agent-fleet-worker.service" \
  /etc/systemd/system/agent-fleet-worker.service

next_link="${INSTALL_LINK}.next"
rm -f -- "$next_link"
ln -s "$release_dir" "$next_link"
if [[ -e "$INSTALL_LINK" && ! -L "$INSTALL_LINK" ]]; then
  die "$INSTALL_LINK existe et n'est pas un lien symbolique; migration manuelle requise"
fi
mv -Tf "$next_link" "$INSTALL_LINK"

systemctl daemon-reload

if [[ $ACTIVATE == true ]]; then
  systemctl enable --now agent-fleet-worker.service
  if [[ $worker_was_active == true ]]; then
    systemctl restart agent-fleet-worker.service
  fi
fi

printf 'Worker installé dans %s\n' "$release_dir"
printf 'Worker ID: %s\n' "$WORKER_ID"
printf 'Éditez %s/worker.yaml, validez les workspaces/harness, puis démarrez le service.\n' "$CONFIG_DIR"
