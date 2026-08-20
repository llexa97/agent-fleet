#!/usr/bin/env bash
set -euo pipefail

umask 077

ENV_FILE=/etc/agent-fleet/control-plane.env
OUTPUT_DIR=/var/backups/agent-fleet
CONFIG_DIR=/etc/agent-fleet
AGE_RECIPIENT=${AGENT_FLEET_BACKUP_AGE_RECIPIENT:-}
INTEGRITY_KEY_FILE=${AGENT_FLEET_BACKUP_INTEGRITY_KEY_FILE:-}

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/backup.sh [options]

Options:
  --env-file PATH            environnement Control Plane
  --output-dir PATH          destination (défaut: /var/backups/agent-fleet)
  --recipient AGE_RECIPIENT  destinataire age public
  --integrity-key-file PATH  clé HMAC de 32+ octets, fournie hors dépôt
  --help                     afficher cette aide
EOF
}

die() {
  printf 'Erreur: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --env-file)
      (($# >= 2)) || die "valeur manquante pour --env-file"
      ENV_FILE=$2
      shift 2
      ;;
    --output-dir)
      (($# >= 2)) || die "valeur manquante pour --output-dir"
      OUTPUT_DIR=$2
      shift 2
      ;;
    --recipient)
      (($# >= 2)) || die "valeur manquante pour --recipient"
      AGE_RECIPIENT=$2
      shift 2
      ;;
    --integrity-key-file)
      (($# >= 2)) || die "valeur manquante pour --integrity-key-file"
      INTEGRITY_KEY_FILE=$2
      shift 2
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
[[ -r $ENV_FILE ]] || die "fichier d'environnement illisible"
if [[ -n $(find "$ENV_FILE" -prune -perm /077 -print) ]]; then
  die "le fichier d'environnement ne doit pas être accessible au groupe ou aux autres"
fi
command -v python3 >/dev/null 2>&1 || die "python3 absent"
command -v pg_dump >/dev/null 2>&1 || die "pg_dump absent"
command -v tar >/dev/null 2>&1 || die "tar absent"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum absent"
command -v rsync >/dev/null 2>&1 || die "rsync absent"
command -v realpath >/dev/null 2>&1 || die "realpath absent"

[[ -n $AGE_RECIPIENT ]] || die "un destinataire age est obligatoire"
command -v age >/dev/null 2>&1 || die "age absent"
[[ -n $INTEGRITY_KEY_FILE ]] || die "--integrity-key-file est obligatoire"
INTEGRITY_KEY_FILE=$(realpath "$INTEGRITY_KEY_FILE")
[[ -f $INTEGRITY_KEY_FILE && -r $INTEGRITY_KEY_FILE ]] || \
  die "clé d'intégrité illisible"
if [[ -n $(find "$INTEGRITY_KEY_FILE" -prune -perm /077 -print) ]]; then
  die "la clé d'intégrité ne doit être accessible qu'à son propriétaire"
fi
AGENT_FLEET_INTEGRITY_KEY_FILE="$INTEGRITY_KEY_FILE" python3 - <<'PY'
import os
from pathlib import Path

key = Path(os.environ["AGENT_FLEET_INTEGRITY_KEY_FILE"]).read_bytes()
if len(key) < 32:
    raise SystemExit("la clé d'intégrité doit contenir au moins 32 octets")
PY

database_url=$(sed -n 's/^AGENT_FLEET_DATABASE_URL=//p' "$ENV_FILE" | tail -n 1)
database_url=${database_url%$'\r'}
database_url=${database_url#\"}
database_url=${database_url%\"}
[[ -n $database_url ]] || die "AGENT_FLEET_DATABASE_URL absente"

mapfile -d '' -t pg_fields < <(
  AGENT_FLEET_DATABASE_URL="$database_url" python3 - <<'PY'
import os
import sys
from urllib.parse import parse_qs, unquote, urlsplit

url = os.environ["AGENT_FLEET_DATABASE_URL"]
url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
parts = urlsplit(url)
if parts.scheme != "postgresql" or not parts.hostname or not parts.username or not parts.path[1:]:
    raise SystemExit("URL PostgreSQL invalide")
query = parse_qs(parts.query)
values = (
    parts.hostname,
    str(parts.port or 5432),
    unquote(parts.path[1:]),
    unquote(parts.username),
    unquote(parts.password or ""),
    query.get("sslmode", [""])[0],
)
for value in values:
    sys.stdout.write(value)
    sys.stdout.write("\0")
PY
)
unset database_url
[[ ${#pg_fields[@]} -eq 6 ]] || die "impossible d'analyser l'URL PostgreSQL"
export PGHOST=${pg_fields[0]} PGPORT=${pg_fields[1]} PGDATABASE=${pg_fields[2]}
export PGUSER=${pg_fields[3]} PGPASSWORD=${pg_fields[4]} PGCONNECT_TIMEOUT=10
if [[ -n ${pg_fields[5]} ]]; then
  export PGSSLMODE=${pg_fields[5]}
fi
unset pg_fields

install -d -m 0700 "$OUTPUT_DIR"
tmp_dir=$(mktemp -d "$OUTPUT_DIR/.backup.XXXXXX")
cleanup() {
  rm -rf -- "$tmp_dir"
}
trap cleanup EXIT INT TERM

stage_dir="$tmp_dir/stage"
install -d -m 0700 "$stage_dir/database" "$stage_dir/config"

pg_dump --format=custom --compress=9 --no-owner --no-acl \
  --file="$stage_dir/database/postgresql.dump" "$PGDATABASE"

if [[ -d $CONFIG_DIR ]]; then
  cp -a "$CONFIG_DIR" "$stage_dir/config/agent-fleet"
fi
if [[ -f /etc/caddy/Caddyfile ]]; then
  install -d -m 0700 "$stage_dir/config/caddy"
  cp -a /etc/caddy/Caddyfile "$stage_dir/config/caddy/Caddyfile"
  if [[ -f /etc/systemd/system/caddy.service.d/agent-fleet.conf ]]; then
    cp -a /etc/systemd/system/caddy.service.d/agent-fleet.conf \
      "$stage_dir/config/caddy/agent-fleet.conf"
  fi
fi
install -d -m 0700 "$stage_dir/config/systemd"
for unit_path in /etc/systemd/system/agent-fleet-*.service \
                 /etc/systemd/system/agent-fleet-*.target; do
  [[ -f $unit_path && ! -L $unit_path ]] || continue
  cp -a "$unit_path" "$stage_dir/config/systemd/"
done
if [[ -d /opt/agent-fleet ]]; then
  install -d -m 0700 "$stage_dir/application"
  rsync -a \
    --exclude=.git \
    --exclude=.venv \
    --exclude=.env \
    --exclude=node_modules \
    --exclude='*.db' \
    --exclude=playwright-report \
    --exclude=test-results \
    /opt/agent-fleet/ "$stage_dir/application/"
fi

timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
product_version=unknown
if [[ -r /opt/agent-fleet/pyproject.toml ]]; then
  product_version=$(sed -n 's/^version = "\([^"]*\)"/\1/p' \
    /opt/agent-fleet/pyproject.toml | head -n 1)
  product_version=${product_version:-unknown}
fi
{
  printf 'format_version=1\n'
  printf 'created_at=%s\n' "$timestamp"
  printf 'hostname=%s\n' "$(hostname)"
  printf 'product_version=%s\n' "$product_version"
  printf 'database_format=postgresql-custom\n'
} > "$stage_dir/MANIFEST"
checksums_tmp="$tmp_dir/SHA256SUMS"
(cd "$stage_dir" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "$checksums_tmp"
mv "$checksums_tmp" "$stage_dir/SHA256SUMS"

archive_base="agent-fleet-$(date -u +%Y%m%dT%H%M%SZ)"
tar_file="$tmp_dir/$archive_base.tar"
tar -C "$stage_dir" -cf "$tar_file" .

final_path="$OUTPUT_DIR/$archive_base.tar.age"
[[ ! -e $final_path && ! -e "$final_path.sha256" && ! -e "$final_path.sha256.hmac" ]] || \
  die "une sauvegarde avec ce timestamp existe déjà"
age --recipient "$AGE_RECIPIENT" --output "$tmp_dir/final.age" "$tar_file"
install -m 0600 "$tmp_dir/final.age" "$final_path"

(cd "$OUTPUT_DIR" && sha256sum "$(basename "$final_path")" > "$(basename "$final_path").sha256")
AGENT_FLEET_INTEGRITY_KEY_FILE="$INTEGRITY_KEY_FILE" \
AGENT_FLEET_CHECKSUM_FILE="$final_path.sha256" \
AGENT_FLEET_HMAC_FILE="$final_path.sha256.hmac" \
python3 - <<'PY'
import hashlib
import hmac
import os
from pathlib import Path

key = Path(os.environ["AGENT_FLEET_INTEGRITY_KEY_FILE"]).read_bytes()
checksum = Path(os.environ["AGENT_FLEET_CHECKSUM_FILE"]).read_bytes()
digest = hmac.new(key, checksum, hashlib.sha256).hexdigest()
Path(os.environ["AGENT_FLEET_HMAC_FILE"]).write_text(digest + "\n", encoding="ascii")
PY
chmod 0600 "$final_path" "$final_path.sha256" "$final_path.sha256.hmac"
unset PGPASSWORD

printf 'Sauvegarde créée: %s\n' "$final_path"
printf 'Checksum: %s.sha256\n' "$final_path"
printf 'Authentification du checksum: %s.sha256.hmac\n' "$final_path"
