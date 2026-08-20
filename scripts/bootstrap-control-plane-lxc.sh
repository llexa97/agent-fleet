#!/usr/bin/env bash
set -euo pipefail

umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=scripts/lib/lxc-bootstrap-common.sh
source "$SCRIPT_DIR/lib/lxc-bootstrap-common.sh"

SOURCE_DIR=$(pwd -P)
DOMAIN=
POSTGRES_USER=agent_fleet
POSTGRES_DB=agent_fleet
POSTGRES_PASSWORD_FILE=
UV_VERSION=0.12.5
PNPM_VERSION=11.19.0
TEMP_DB_PASSWORD_FILE=/root/.agent-fleet-postgres-password
INTERNAL_TLS=false

usage() {
  cat <<'EOF'
Usage:
  sudo ./scripts/bootstrap-control-plane-lxc.sh \
    --domain fleet.example.net [options]

Installe et démarre sur Debian/Ubuntu : PostgreSQL, Redis, Caddy, Node.js,
pnpm, uv, l'API, le dispatcher et l'interface Agent Fleet.

Options :
  --source PATH                    dépôt Agent Fleet (défaut: répertoire courant)
  --domain FQDN                   domaine HTTPS du Control Plane (obligatoire)
  --postgres-user NAME            rôle PostgreSQL (défaut: agent_fleet)
  --postgres-db NAME              base PostgreSQL (défaut: agent_fleet)
  --postgres-password-file PATH   fichier 0600 avec un mot de passe URL-safe
  --internal-tls                  CA Caddy interne pour un domaine DNS privé
  --uv-version VERSION            version uv épinglée (défaut: 0.12.5)
  --pnpm-version VERSION          version pnpm épinglée (défaut: 11.19.0)
  --help                          afficher cette aide

Le script ne publie aucun secret. Sur une installation neuve, il génère le
mot de passe PostgreSQL, le secret de session et le token de bootstrap.
EOF
}

while (($#)); do
  case "$1" in
    --source)
      (($# >= 2)) || fleet_die "valeur manquante pour --source"
      SOURCE_DIR=$2
      shift 2
      ;;
    --domain)
      (($# >= 2)) || fleet_die "valeur manquante pour --domain"
      DOMAIN=$2
      shift 2
      ;;
    --postgres-user)
      (($# >= 2)) || fleet_die "valeur manquante pour --postgres-user"
      POSTGRES_USER=$2
      shift 2
      ;;
    --postgres-db)
      (($# >= 2)) || fleet_die "valeur manquante pour --postgres-db"
      POSTGRES_DB=$2
      shift 2
      ;;
    --postgres-password-file)
      (($# >= 2)) || fleet_die "valeur manquante pour --postgres-password-file"
      POSTGRES_PASSWORD_FILE=$2
      shift 2
      ;;
    --internal-tls)
      INTERNAL_TLS=true
      shift
      ;;
    --uv-version)
      (($# >= 2)) || fleet_die "valeur manquante pour --uv-version"
      UV_VERSION=$2
      shift 2
      ;;
    --pnpm-version)
      (($# >= 2)) || fleet_die "valeur manquante pour --pnpm-version"
      PNPM_VERSION=$2
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

[[ -n $DOMAIN ]] || fleet_die "--domain est obligatoire"
[[ $DOMAIN =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || fleet_die "domaine invalide"
[[ $POSTGRES_USER =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || fleet_die "rôle PostgreSQL invalide"
[[ $POSTGRES_DB =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || fleet_die "nom de base invalide"
[[ $UV_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fleet_die "version uv invalide"
[[ $PNPM_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fleet_die "version pnpm invalide"

SOURCE_DIR=$(realpath "$SOURCE_DIR")
[[ -x $SOURCE_DIR/scripts/install-control-plane.sh ]] || \
  fleet_die "install-control-plane.sh est absent ou non exécutable dans $SOURCE_DIR"
[[ -f $SOURCE_DIR/uv.lock && -f $SOURCE_DIR/apps/web/package.json ]] || \
  fleet_die "le dépôt Agent Fleet est incomplet"

if [[ -n $POSTGRES_PASSWORD_FILE ]]; then
  POSTGRES_PASSWORD_FILE=$(realpath "$POSTGRES_PASSWORD_FILE")
  fleet_assert_secret_file "$POSTGRES_PASSWORD_FILE"
fi

fleet_install_base_packages
export DEBIAN_FRONTEND=noninteractive
apt-get install --yes --no-install-recommends \
  age \
  iproute2 \
  postgresql \
  postgresql-client \
  redis-server
fleet_install_caddy
fleet_install_node 22 "$PNPM_VERSION"
fleet_install_uv "$UV_VERSION"

fleet_log "Configuration locale de PostgreSQL et Redis"
systemctl enable --now postgresql redis-server
runuser -u postgres -- psql --quiet --no-psqlrc \
  --command="ALTER SYSTEM SET listen_addresses TO '127.0.0.1';"
runuser -u postgres -- psql --quiet --no-psqlrc \
  --command="ALTER SYSTEM SET password_encryption TO 'scram-sha-256';"
systemctl restart postgresql

redis_conf=/etc/redis/redis.conf
[[ -f $redis_conf ]] || fleet_die "configuration Redis absente: $redis_conf"
if grep -Eq '^[[:space:]]*#?[[:space:]]*bind[[:space:]]+' "$redis_conf"; then
  sed -Ei 's/^[[:space:]]*#?[[:space:]]*bind[[:space:]].*/bind 127.0.0.1 -::1/' "$redis_conf"
else
  printf '\nbind 127.0.0.1 -::1\n' >> "$redis_conf"
fi
if grep -Eq '^[[:space:]]*#?[[:space:]]*protected-mode[[:space:]]+' "$redis_conf"; then
  sed -Ei 's/^[[:space:]]*#?[[:space:]]*protected-mode[[:space:]].*/protected-mode yes/' "$redis_conf"
else
  printf 'protected-mode yes\n' >> "$redis_conf"
fi
systemctl restart redis-server

config_file=/etc/agent-fleet/control-plane.env
new_configuration=false
if [[ ! -e $config_file ]]; then
  new_configuration=true
  if [[ -n $POSTGRES_PASSWORD_FILE ]]; then
    db_password=$(head -n 1 "$POSTGRES_PASSWORD_FILE")
  elif [[ -f $TEMP_DB_PASSWORD_FILE ]]; then
    fleet_assert_secret_file "$TEMP_DB_PASSWORD_FILE"
    db_password=$(head -n 1 "$TEMP_DB_PASSWORD_FILE")
  else
    db_password=$(openssl rand -hex 32)
    printf '%s\n' "$db_password" > "$TEMP_DB_PASSWORD_FILE"
    chmod 0600 "$TEMP_DB_PASSWORD_FILE"
  fi
  [[ $db_password =~ ^[A-Za-z0-9._~-]{24,256}$ ]] || \
    fleet_die "le mot de passe PostgreSQL doit être URL-safe et contenir 24 à 256 caractères"

  role_exists=$(runuser -u postgres -- psql --quiet --tuples-only --no-align --no-psqlrc \
    --command="SELECT 1 FROM pg_roles WHERE rolname = '${POSTGRES_USER}'")
  if [[ $role_exists == 1 ]]; then
    if [[ ! -f $TEMP_DB_PASSWORD_FILE && -z $POSTGRES_PASSWORD_FILE ]]; then
      fleet_die "le rôle $POSTGRES_USER existe déjà; fournissez son secret avec --postgres-password-file"
    fi
    printf "ALTER ROLE %s WITH LOGIN PASSWORD '%s';\n" "$POSTGRES_USER" "$db_password" |
      runuser -u postgres -- psql --quiet --no-psqlrc
  else
    printf "CREATE ROLE %s WITH LOGIN PASSWORD '%s';\n" "$POSTGRES_USER" "$db_password" |
      runuser -u postgres -- psql --quiet --no-psqlrc
  fi

  db_owner=$(runuser -u postgres -- psql --quiet --tuples-only --no-align --no-psqlrc \
    --command="SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '${POSTGRES_DB}'")
  if [[ -z $db_owner ]]; then
    runuser -u postgres -- createdb --owner="$POSTGRES_USER" --encoding=UTF8 "$POSTGRES_DB"
  elif [[ $db_owner != "$POSTGRES_USER" ]]; then
    fleet_die "la base $POSTGRES_DB existe mais appartient à $db_owner"
  fi

  hba_file=$(runuser -u postgres -- psql --quiet --tuples-only --no-align --no-psqlrc \
    --command="SHOW hba_file")
  [[ -f $hba_file ]] || fleet_die "pg_hba.conf introuvable: $hba_file"
  hba_rule="host ${POSTGRES_DB} ${POSTGRES_USER} 127.0.0.1/32 scram-sha-256"
  if ! grep -Fqx "$hba_rule" "$hba_file"; then
    hba_tmp=$(mktemp)
    printf '%s\n' "$hba_rule" > "$hba_tmp"
    cat "$hba_file" >> "$hba_tmp"
    install -o postgres -g postgres -m 0640 "$hba_tmp" "$hba_file"
    rm -f -- "$hba_tmp"
  fi
  systemctl reload postgresql

  install -d -o root -g root -m 0700 /etc/agent-fleet
  env_tmp=$(mktemp /etc/agent-fleet/.control-plane.env.XXXXXX)
  session_secret=$(openssl rand -hex 48)
  bootstrap_token=$(openssl rand -hex 32)
  while IFS= read -r line || [[ -n $line ]]; do
    case "$line" in
      AGENT_FLEET_DATABASE_URL=*)
        printf 'AGENT_FLEET_DATABASE_URL=postgresql+asyncpg://%s:%s@127.0.0.1:5432/%s\n' \
          "$POSTGRES_USER" "$db_password" "$POSTGRES_DB"
        ;;
      AGENT_FLEET_PUBLIC_URL=*) printf 'AGENT_FLEET_PUBLIC_URL=https://%s\n' "$DOMAIN" ;;
      AGENT_FLEET_WEB_ORIGIN=*) printf 'AGENT_FLEET_WEB_ORIGIN=https://%s\n' "$DOMAIN" ;;
      AGENT_FLEET_TRUSTED_HOSTS=*)
        printf 'AGENT_FLEET_TRUSTED_HOSTS=%s,localhost,127.0.0.1\n' "$DOMAIN"
        ;;
      AGENT_FLEET_SESSION_SECRET=*) printf 'AGENT_FLEET_SESSION_SECRET=%s\n' "$session_secret" ;;
      AGENT_FLEET_BOOTSTRAP_TOKEN=*)
        printf 'AGENT_FLEET_BOOTSTRAP_TOKEN=%s\n' "$bootstrap_token"
        ;;
      *) printf '%s\n' "$line" ;;
    esac
  done < "$SOURCE_DIR/infra/systemd/control-plane.env.example" > "$env_tmp"
  install -o root -g root -m 0600 "$env_tmp" "$config_file"
  rm -f -- "$env_tmp"
  unset db_password session_secret bootstrap_token
else
  fleet_log "Configuration existante conservée: $config_file"
fi

fleet_log "Installation et activation du Control Plane"
install_arguments=(
  --source "$SOURCE_DIR"
  --domain "$DOMAIN"
  --activate
)
if [[ $INTERNAL_TLS == true ]]; then
  install_arguments+=(--internal-tls)
fi
"$SOURCE_DIR/scripts/install-control-plane.sh" "${install_arguments[@]}"

curl --fail --silent --show-error --max-time 10 \
  http://127.0.0.1:8000/api/v1/readiness >/dev/null

if [[ $new_configuration == true ]]; then
  fleet_secure_remove "$TEMP_DB_PASSWORD_FILE"
fi

fleet_log "Control Plane installé et prêt"
printf '\nURL: https://%s\n' "$DOMAIN"
printf 'Configuration: %s\n' "$config_file"
printf 'Créez le premier propriétaire dans la web app avec le token de bootstrap contenu dans ce fichier.\n'
printf 'Diagnostic: journalctl -u agent-fleet-api -u agent-fleet-dispatcher -f\n'
