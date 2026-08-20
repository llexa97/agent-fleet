#!/usr/bin/env bash
set -euo pipefail

umask 077

ARCHIVE=
IDENTITY=
AGE_RECIPIENT=${AGENT_FLEET_BACKUP_AGE_RECIPIENT:-}
INTEGRITY_KEY_FILE=${AGENT_FLEET_BACKUP_INTEGRITY_KEY_FILE:-}
ENV_FILE=/etc/agent-fleet/control-plane.env
CONFIG_DIR=/etc/agent-fleet
CONFIRMED=false
RESTORE_CONFIG=false
RESTORE_APPLICATION=false
ALLOW_RUNNING=false

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/restore.sh --archive PATH --confirm-restore [options]

Options:
  --archive PATH       archive .tar.age créée par backup.sh
  --identity PATH      identité age de déchiffrement
  --recipient VALUE    destinataire age pour chiffrer le dump de sécurité
  --integrity-key-file PATH  clé HMAC utilisée lors de la sauvegarde
  --env-file PATH      base cible via AGENT_FLEET_DATABASE_URL
  --restore-config     restaurer aussi /etc/agent-fleet après copie de sécurité
  --restore-application  extraire le snapshot applicatif sans l'activer
  --allow-running      autoriser une base de test pendant que les services prod tournent
  --confirm-restore    confirmation destructive obligatoire
  --help               afficher cette aide
EOF
}

die() {
  printf 'Erreur: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --archive)
      (($# >= 2)) || die "valeur manquante pour --archive"
      ARCHIVE=$2
      shift 2
      ;;
    --identity)
      (($# >= 2)) || die "valeur manquante pour --identity"
      IDENTITY=$2
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
    --env-file)
      (($# >= 2)) || die "valeur manquante pour --env-file"
      ENV_FILE=$2
      shift 2
      ;;
    --restore-config)
      RESTORE_CONFIG=true
      shift
      ;;
    --restore-application)
      RESTORE_APPLICATION=true
      shift
      ;;
    --allow-running)
      ALLOW_RUNNING=true
      shift
      ;;
    --confirm-restore)
      CONFIRMED=true
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
[[ $CONFIRMED == true ]] || die "--confirm-restore est obligatoire"
[[ -n $ARCHIVE && -f $ARCHIVE ]] || die "archive absente"
if [[ -n $(find "$ARCHIVE" -prune -perm /077 -print) ]]; then
  die "l'archive sensible ne doit pas être accessible au groupe ou aux autres"
fi
[[ -r $ENV_FILE ]] || die "fichier d'environnement cible illisible"
if [[ -n $(find "$ENV_FILE" -prune -perm /077 -print) ]]; then
  die "le fichier d'environnement cible ne doit pas être accessible au groupe ou aux autres"
fi

for command_name in python3 pg_dump pg_restore tar sha256sum realpath age rsync getent; do
  command -v "$command_name" >/dev/null 2>&1 || die "commande requise absente: $command_name"
done

ARCHIVE=$(realpath "$ARCHIVE")
[[ $ARCHIVE == *.tar.age ]] || die "seules les sauvegardes .tar.age authentifiées sont acceptées"
[[ -n $IDENTITY && -r $IDENTITY ]] || die "--identity lisible est obligatoire"
[[ -n $AGE_RECIPIENT ]] || die "--recipient est obligatoire pour le dump de sécurité chiffré"
[[ -n $INTEGRITY_KEY_FILE ]] || die "--integrity-key-file est obligatoire"
INTEGRITY_KEY_FILE=$(realpath "$INTEGRITY_KEY_FILE")
[[ -f $INTEGRITY_KEY_FILE && -r $INTEGRITY_KEY_FILE ]] || \
  die "clé d'intégrité illisible"
if [[ -n $(find "$INTEGRITY_KEY_FILE" -prune -perm /077 -print) ]]; then
  die "la clé d'intégrité ne doit être accessible qu'à son propriétaire"
fi
[[ -f "$ARCHIVE.sha256" && ! -L "$ARCHIVE.sha256" ]] || \
  die "sidecar checksum absent ou invalide: $ARCHIVE.sha256"
[[ -f "$ARCHIVE.sha256.hmac" && ! -L "$ARCHIVE.sha256.hmac" ]] || \
  die "authentification HMAC du checksum absente"
AGENT_FLEET_INTEGRITY_KEY_FILE="$INTEGRITY_KEY_FILE" \
AGENT_FLEET_CHECKSUM_FILE="$ARCHIVE.sha256" \
AGENT_FLEET_HMAC_FILE="$ARCHIVE.sha256.hmac" \
python3 - <<'PY'
import hashlib
import hmac
import os
import re
from pathlib import Path

key = Path(os.environ["AGENT_FLEET_INTEGRITY_KEY_FILE"]).read_bytes()
if len(key) < 32:
    raise SystemExit("la clé d'intégrité doit contenir au moins 32 octets")
checksum = Path(os.environ["AGENT_FLEET_CHECKSUM_FILE"]).read_bytes()
provided = Path(os.environ["AGENT_FLEET_HMAC_FILE"]).read_text(encoding="ascii").strip()
if not re.fullmatch(r"[0-9a-f]{64}", provided):
    raise SystemExit("format HMAC invalide")
expected = hmac.new(key, checksum, hashlib.sha256).hexdigest()
if not hmac.compare_digest(provided, expected):
    raise SystemExit("authentification HMAC du checksum invalide")
PY
read -r expected_hash _ < "$ARCHIVE.sha256"
[[ $expected_hash =~ ^[0-9a-fA-F]{64}$ ]] || die "format du checksum externe invalide"
actual_hash=$(sha256sum "$ARCHIVE")
actual_hash=${actual_hash%% *}
[[ $actual_hash == "$expected_hash" ]] || die "checksum externe invalide"
unset expected_hash actual_hash

if [[ $ALLOW_RUNNING != true ]] && command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet agent-fleet-api.service 2>/dev/null || \
     systemctl is-active --quiet agent-fleet-dispatcher.service 2>/dev/null; then
    die "arrêtez l'API et le dispatcher, ou utilisez --allow-running uniquement pour une base isolée"
  fi
fi

tmp_dir=$(mktemp -d /var/tmp/agent-fleet-restore.XXXXXX)
cleanup() {
  rm -rf -- "$tmp_dir"
}
trap cleanup EXIT INT TERM

if [[ -n $(find "$IDENTITY" -prune -perm /077 -print) ]]; then
  die "l'identité age ne doit pas être accessible au groupe ou aux autres"
fi
tar_file="$tmp_dir/backup.tar"
age --decrypt --identity "$IDENTITY" --output "$tar_file" "$ARCHIVE"

AGENT_FLEET_RESTORE_TAR="$tar_file" python3 - <<'PY'
import os
import posixpath
import tarfile
from pathlib import PurePosixPath

archive = os.environ["AGENT_FLEET_RESTORE_TAR"]
with tarfile.open(archive, mode="r:") as source:
    for index, member in enumerate(source, start=1):
        if index > 100_000:
            raise SystemExit("archive avec trop d'entrées")
        parts = PurePosixPath(member.name).parts
        if member.name.startswith("/") or ".." in parts:
            raise SystemExit("chemin dangereux dans l'archive")
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise SystemExit("type de fichier spécial interdit dans l'archive")
        if member.issym():
            if member.linkname.startswith("/"):
                raise SystemExit("lien symbolique absolu interdit dans l'archive")
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(member.name), member.linkname)
            )
            if target == ".." or target.startswith("../"):
                raise SystemExit("lien symbolique sortant interdit dans l'archive")
        if member.islnk():
            target_parts = PurePosixPath(member.linkname).parts
            if member.linkname.startswith("/") or ".." in target_parts:
                raise SystemExit("lien physique sortant interdit dans l'archive")
PY

extract_dir="$tmp_dir/extracted"
install -d -m 0700 "$extract_dir"
tar --no-same-owner --no-same-permissions -C "$extract_dir" -xf "$tar_file"
[[ -f "$extract_dir/MANIFEST" ]] || die "MANIFEST absent"
grep -qx 'format_version=1' "$extract_dir/MANIFEST" || die "format de sauvegarde incompatible"
[[ -f "$extract_dir/SHA256SUMS" ]] || die "checksums internes absents"
(cd "$extract_dir" && sha256sum --check --quiet SHA256SUMS) || die "checksum interne invalide"
[[ -f "$extract_dir/database/postgresql.dump" ]] || die "dump PostgreSQL absent"
pg_restore --list "$extract_dir/database/postgresql.dump" >/dev/null || die "catalogue pg_restore invalide"

if [[ $RESTORE_CONFIG == true ]]; then
  [[ -d "$extract_dir/config/agent-fleet" ]] || die "configuration absente de l'archive"
  [[ -f "$extract_dir/config/agent-fleet/control-plane.env" ]] || \
    die "control-plane.env absent de l'archive"
  [[ -f "$extract_dir/config/caddy/Caddyfile" ]] || die "Caddyfile absent de l'archive"
  for required_unit in agent-fleet-api.service agent-fleet-dispatcher.service \
                       agent-fleet-migrate.service agent-fleet-control-plane.target; do
    [[ -f "$extract_dir/config/systemd/$required_unit" ]] || \
      die "unité requise absente de l'archive: $required_unit"
  done
  if find "$extract_dir/config" -type l -print -quit | grep -q .; then
    die "les liens symboliques sont interdits dans la configuration restaurée"
  fi
  for unit_path in "$extract_dir"/config/systemd/agent-fleet-*; do
    [[ -e $unit_path ]] || continue
    case "$(basename "$unit_path")" in
      agent-fleet-*.service|agent-fleet-*.target) ;;
      *) die "nom d'unité inattendu dans l'archive" ;;
    esac
  done
fi

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

safety_dir=/var/backups/agent-fleet/pre-restore
install -d -m 0700 "$safety_dir"
safety_name="$(date -u +%Y%m%dT%H%M%SZ)-database.dump.age"
safety_dump="$safety_dir/$safety_name"
[[ ! -e $safety_dump ]] || die "dump de sécurité déjà existant: $safety_dump"
safety_plain="$tmp_dir/pre-restore-database.dump"
pg_dump --format=custom --compress=9 --no-owner --no-acl \
  --file="$safety_plain" "$PGDATABASE"
age --recipient "$AGE_RECIPIENT" --output "$tmp_dir/$safety_name" "$safety_plain"
install -m 0600 "$tmp_dir/$safety_name" "$safety_dump"
rm -f -- "$safety_plain"

pg_restore --clean --if-exists --no-owner --no-acl --exit-on-error \
  --single-transaction --dbname="$PGDATABASE" "$extract_dir/database/postgresql.dump"

if [[ $RESTORE_CONFIG == true ]]; then
  config_safety_stage="$tmp_dir/pre-restore-config"
  install -d -m 0700 "$config_safety_stage/agent-fleet" \
    "$config_safety_stage/caddy" "$config_safety_stage/systemd"
  if [[ -d $CONFIG_DIR ]]; then
    cp -a "$CONFIG_DIR/." "$config_safety_stage/agent-fleet/"
  fi
  if [[ -f /etc/caddy/Caddyfile ]]; then
    cp -a /etc/caddy/Caddyfile "$config_safety_stage/caddy/Caddyfile"
  fi
  if [[ -f /etc/systemd/system/caddy.service.d/agent-fleet.conf ]]; then
    cp -a /etc/systemd/system/caddy.service.d/agent-fleet.conf \
      "$config_safety_stage/caddy/agent-fleet.conf"
  fi
  for current_unit in /etc/systemd/system/agent-fleet-*.service \
                      /etc/systemd/system/agent-fleet-*.target; do
    [[ -e $current_unit || -L $current_unit ]] || continue
    cp -a "$current_unit" "$config_safety_stage/systemd/"
  done
  config_safety_name="$(date -u +%Y%m%dT%H%M%SZ)-config.tar.age"
  config_safety="$safety_dir/$config_safety_name"
  [[ ! -e $config_safety ]] || die "sauvegarde de configuration déjà existante"
  tar -C "$config_safety_stage" -cf "$tmp_dir/pre-restore-config.tar" .
  age --recipient "$AGE_RECIPIENT" --output "$tmp_dir/$config_safety_name" \
    "$tmp_dir/pre-restore-config.tar"
  install -m 0600 "$tmp_dir/$config_safety_name" "$config_safety"

  install -d -m 0750 "$CONFIG_DIR"
  rsync -a --delete "$extract_dir/config/agent-fleet/." "$CONFIG_DIR/"
  chown root:root "$CONFIG_DIR"
  chmod 0700 "$CONFIG_DIR"
  chown root:root "$CONFIG_DIR/control-plane.env"
  chmod 0600 "$CONFIG_DIR/control-plane.env"
  install -d -m 0755 /etc/caddy
  install -m 0644 "$extract_dir/config/caddy/Caddyfile" /etc/caddy/Caddyfile
  install -d -m 0755 /etc/systemd/system/caddy.service.d
  if [[ -f "$extract_dir/config/caddy/agent-fleet.conf" ]]; then
    install -m 0644 "$extract_dir/config/caddy/agent-fleet.conf" \
      /etc/systemd/system/caddy.service.d/agent-fleet.conf
  else
    rm -f -- /etc/systemd/system/caddy.service.d/agent-fleet.conf
  fi
  for current_unit in /etc/systemd/system/agent-fleet-*.service \
                      /etc/systemd/system/agent-fleet-*.target; do
    [[ -e $current_unit || -L $current_unit ]] || continue
    rm -f -- "$current_unit"
  done
  for unit_path in "$extract_dir"/config/systemd/agent-fleet-*; do
    [[ -e $unit_path ]] || continue
    unit_name=$(basename "$unit_path")
    install -m 0644 "$unit_path" "/etc/systemd/system/$unit_name"
  done
  if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
  fi
fi

if [[ $RESTORE_APPLICATION == true ]]; then
  [[ -d "$extract_dir/application" ]] || die "snapshot applicatif absent de l'archive"
  application_restore_dir="/opt/agent-fleet-restored/$(date -u +%Y%m%dT%H%M%SZ)"
  [[ ! -e $application_restore_dir ]] || die "destination applicative déjà existante"
  install -d -m 0755 "$application_restore_dir"
  cp -a "$extract_dir/application/." "$application_restore_dir/"
fi

unset PGPASSWORD
printf 'Restauration PostgreSQL terminée. Dump de sécurité: %s\n' "$safety_dump"
if [[ $RESTORE_CONFIG == true ]]; then
  printf 'Configuration restaurée; copie précédente: %s\n' "$config_safety"
fi
if [[ $RESTORE_APPLICATION == true ]]; then
  printf 'Snapshot applicatif extrait sans activation: %s\n' "$application_restore_dir"
fi
printf 'Exécutez les migrations de la version déployée puis vérifiez la readiness avant le trafic.\n'
