#!/usr/bin/env bash
set -euo pipefail

umask 077

SOURCE_DIR="$(pwd -P)"
INSTALL_LINK=/opt/agent-fleet
RELEASES_DIR=/opt/agent-fleet-releases
CONFIG_DIR=/etc/agent-fleet
STATE_DIR=/var/lib/agent-fleet
CACHE_DIR=/var/cache/agent-fleet
PYTHON_DIR=/opt/agent-fleet-python
DOMAIN=
ACTIVATE=false

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/install-control-plane.sh --domain FQDN [options]

Options:
  --source PATH       dépôt source (défaut: répertoire courant)
  --domain FQDN       domaine HTTPS public (obligatoire)
  --activate          activer/démarrer après validation de la configuration
  --help              afficher cette aide

Le script ne configure pas le mot de passe PostgreSQL et ne journalise aucun secret.
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
    --domain)
      (($# >= 2)) || die "valeur manquante pour --domain"
      DOMAIN=$2
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
[[ -n ${DOMAIN} ]] || die "--domain est obligatoire"
[[ ${DOMAIN} =~ ^[A-Za-z0-9.-]+$ ]] || die "domaine invalide"

SOURCE_DIR=$(realpath "$SOURCE_DIR")
[[ -f "$SOURCE_DIR/pyproject.toml" && -f "$SOURCE_DIR/uv.lock" ]] || \
  die "le répertoire source n'est pas un dépôt Agent Fleet"
[[ -f "$SOURCE_DIR/apps/web/package.json" ]] || die "frontend apps/web absent"

for command_name in realpath rsync uv node pnpm systemctl install openssl caddy curl; do
  need_command "$command_name"
done
node_major=$(node -p 'Number(process.versions.node.split(".")[0])')
[[ $node_major =~ ^[0-9]+$ && $node_major -ge 20 ]] || die "Node.js 20 ou plus récent est requis"
unset node_major

control_plane_was_active=false
if systemctl is-active --quiet agent-fleet-api.service 2>/dev/null || \
   systemctl is-active --quiet agent-fleet-dispatcher.service 2>/dev/null; then
  control_plane_was_active=true
fi
if [[ $control_plane_was_active == true && $ACTIVATE != true ]]; then
  die "le Control Plane est actif; relancez avec --activate pour effectuer un basculement contrôlé"
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
env \
  UV_CACHE_DIR="$CACHE_DIR/uv" \
  UV_PYTHON_INSTALL_DIR="$PYTHON_DIR" \
  uv --directory "$release_dir" sync --frozen --no-dev

env \
  PNPM_HOME="$CACHE_DIR/pnpm" \
  pnpm --dir "$release_dir" install --frozen-lockfile
env \
  PNPM_HOME="$CACHE_DIR/pnpm" \
  pnpm --dir "$release_dir/apps/web" build

if [[ ! -e "$CONFIG_DIR/control-plane.env" ]]; then
  session_secret=$(openssl rand -hex 48)
  bootstrap_token=$(openssl rand -hex 32)
  env_tmp=$(mktemp "$CONFIG_DIR/.control-plane.env.XXXXXX")
  while IFS= read -r line || [[ -n $line ]]; do
    case "$line" in
      AGENT_FLEET_PUBLIC_URL=*)
        printf 'AGENT_FLEET_PUBLIC_URL=https://%s\n' "$DOMAIN"
        ;;
      AGENT_FLEET_WEB_ORIGIN=*)
        printf 'AGENT_FLEET_WEB_ORIGIN=https://%s\n' "$DOMAIN"
        ;;
      AGENT_FLEET_TRUSTED_HOSTS=*)
        printf 'AGENT_FLEET_TRUSTED_HOSTS=%s,localhost,127.0.0.1\n' "$DOMAIN"
        ;;
      AGENT_FLEET_SESSION_SECRET=*)
        printf 'AGENT_FLEET_SESSION_SECRET=%s\n' "$session_secret"
        ;;
      AGENT_FLEET_BOOTSTRAP_TOKEN=*)
        printf 'AGENT_FLEET_BOOTSTRAP_TOKEN=%s\n' "$bootstrap_token"
        ;;
      *)
        printf '%s\n' "$line"
        ;;
    esac
  done < "$release_dir/infra/systemd/control-plane.env.example" > "$env_tmp"
  chown root:root "$env_tmp"
  chmod 0600 "$env_tmp"
  mv "$env_tmp" "$CONFIG_DIR/control-plane.env"
  unset session_secret bootstrap_token
fi
chown root:root "$CONFIG_DIR/control-plane.env"
chmod 0600 "$CONFIG_DIR/control-plane.env"
if [[ $ACTIVATE == true ]]; then
  PYTHONPATH="$release_dir" "$release_dir/.venv/bin/python" - \
    "$CONFIG_DIR/control-plane.env" "$DOMAIN" <<'PY'
from pathlib import Path
import sys
from urllib.parse import urlsplit

path = Path(sys.argv[1])
domain = sys.argv[2].lower()
values: dict[str, str] = {}
try:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise ValueError
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value
except (OSError, UnicodeError, ValueError):
    raise SystemExit("Préflight refusé: fichier d'environnement invalide") from None


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"Préflight refusé: {message}")


required = {
    "AGENT_FLEET_ENVIRONMENT",
    "AGENT_FLEET_DATABASE_URL",
    "AGENT_FLEET_REDIS_URL",
    "AGENT_FLEET_PUBLIC_URL",
    "AGENT_FLEET_WEB_ORIGIN",
    "AGENT_FLEET_SESSION_SECRET",
    "AGENT_FLEET_BOOTSTRAP_TOKEN",
    "AGENT_FLEET_COOKIE_SECURE",
    "AGENT_FLEET_TRUSTED_HOSTS",
}
require(required <= values.keys(), "variables obligatoires absentes")
require(values["AGENT_FLEET_ENVIRONMENT"] == "production", "environnement non production")
require(values["AGENT_FLEET_COOKIE_SECURE"].lower() == "true", "cookie non sécurisé")
require(
    values["AGENT_FLEET_DATABASE_URL"].startswith("postgresql+asyncpg://"),
    "URL PostgreSQL asynchrone obligatoire",
)
require(
    values["AGENT_FLEET_REDIS_URL"].startswith(("redis://", "rediss://")),
    "URL Redis invalide",
)

expected_origin = f"https://{domain}"
for key in ("AGENT_FLEET_PUBLIC_URL", "AGENT_FLEET_WEB_ORIGIN"):
    parsed = urlsplit(values[key])
    require(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == domain
        and parsed.port in (None, 443)
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment,
        f"{key} doit désigner {expected_origin}",
    )

trusted_hosts = {item.strip().lower() for item in values["AGENT_FLEET_TRUSTED_HOSTS"].split(",")}
require(domain in trusted_hosts, "domaine absent des hôtes de confiance")
session_secret = values["AGENT_FLEET_SESSION_SECRET"]
bootstrap_token = values["AGENT_FLEET_BOOTSTRAP_TOKEN"]
require(len(session_secret) >= 48 and "replace" not in session_secret.lower(), "secret de session faible")
require(len(bootstrap_token) >= 32 and "replace" not in bootstrap_token.lower(), "jeton bootstrap faible")
require(session_secret != bootstrap_token, "secrets réutilisés")
require(
    values.get("AGENT_FLEET_EMBEDDED_DISPATCHER", "false").lower() == "false",
    "dispatcher embarqué interdit avec le service dédié",
)
PY
fi

if [[ ! -e "$CONFIG_DIR/caddy.env" ]]; then
  printf 'AGENT_FLEET_DOMAIN=%s\n' "$DOMAIN" > "$CONFIG_DIR/caddy.env"
  chown root:root "$CONFIG_DIR/caddy.env"
  chmod 0644 "$CONFIG_DIR/caddy.env"
fi
chown root:root "$CONFIG_DIR/caddy.env"
chmod 0644 "$CONFIG_DIR/caddy.env"
caddy_domain=$(sed -n 's/^AGENT_FLEET_DOMAIN=//p' "$CONFIG_DIR/caddy.env" | tail -n 1)
[[ $caddy_domain == "$DOMAIN" ]] || \
  die "--domain ne correspond pas à $CONFIG_DIR/caddy.env"
env AGENT_FLEET_DOMAIN="$caddy_domain" \
  caddy validate --config "$release_dir/infra/caddy/Caddyfile" >/dev/null
if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify \
    "$release_dir/infra/systemd/agent-fleet-migrate.service" \
    "$release_dir/infra/systemd/agent-fleet-api.service" \
    "$release_dir/infra/systemd/agent-fleet-dispatcher.service" \
    "$release_dir/infra/systemd/agent-fleet-control-plane.target" >/dev/null
fi

for unit in \
  agent-fleet-api.service \
  agent-fleet-dispatcher.service \
  agent-fleet-migrate.service \
  agent-fleet-control-plane.target; do
  install -m 0644 "$release_dir/infra/systemd/$unit" "/etc/systemd/system/$unit"
done

install -m 0644 "$release_dir/infra/caddy/Caddyfile" /etc/caddy/Caddyfile
install -d -m 0755 /etc/systemd/system/caddy.service.d
install -m 0644 "$release_dir/infra/caddy/agent-fleet.conf" \
  /etc/systemd/system/caddy.service.d/agent-fleet.conf

next_link="${INSTALL_LINK}.next"
rm -f -- "$next_link"
ln -s "$release_dir" "$next_link"
if [[ -e "$INSTALL_LINK" && ! -L "$INSTALL_LINK" ]]; then
  die "$INSTALL_LINK existe et n'est pas un lien symbolique; migration manuelle requise"
fi
mv -Tf "$next_link" "$INSTALL_LINK"

systemctl daemon-reload
env AGENT_FLEET_DOMAIN="$caddy_domain" caddy validate --config /etc/caddy/Caddyfile >/dev/null

if [[ $ACTIVATE == true ]]; then
  systemctl enable --now postgresql redis-server caddy
  # Le paquet Caddy peut déjà être actif avec sa configuration par défaut.
  # Un simple `enable --now` ne recharge alors pas le Caddyfile installé.
  systemctl restart caddy
  systemctl enable --now agent-fleet-control-plane.target
  if [[ $control_plane_was_active == true ]]; then
    systemctl restart agent-fleet-api agent-fleet-dispatcher
  fi
  for required_service in caddy agent-fleet-api agent-fleet-dispatcher; do
    systemctl is-active --quiet "$required_service.service" || \
      die "service non actif après bascule: $required_service"
  done
  readiness_ok=false
  for _ in $(seq 1 30); do
    if curl --fail --silent --max-time 2 \
      http://127.0.0.1:8000/api/v1/readiness >/dev/null; then
      readiness_ok=true
      break
    fi
    sleep 2
  done
  [[ $readiness_ok == true ]] || die "readiness API en échec après 60 secondes"
fi

printf 'Control Plane installé dans %s\n' "$release_dir"
printf 'Configuration protégée: %s/control-plane.env\n' "$CONFIG_DIR"
printf 'Validez PostgreSQL et la configuration, puis démarrez agent-fleet-control-plane.target.\n'
