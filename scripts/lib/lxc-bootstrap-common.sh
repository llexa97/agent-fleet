#!/usr/bin/env bash

# Fonctions communes aux deux installateurs LXC. Ce fichier est sourcé et
# n'est pas destiné à être exécuté directement.

fleet_log() {
  printf '[Agent Fleet] %s\n' "$*"
}

fleet_die() {
  printf '[Agent Fleet] ERREUR: %s\n' "$*" >&2
  exit 1
}

fleet_need_command() {
  command -v "$1" >/dev/null 2>&1 || fleet_die "commande requise absente: $1"
}

fleet_require_root() {
  [[ ${EUID} -eq 0 ]] || fleet_die "exécutez ce script avec sudo ou root"
}

fleet_require_supported_system() {
  local virtualization
  [[ -r /etc/os-release ]] || fleet_die "/etc/os-release est absent"
  # shellcheck disable=SC1091
  source /etc/os-release
  case "${ID:-}" in
    debian|ubuntu) ;;
    *) fleet_die "système non supporté: ${ID:-inconnu}; utilisez Debian ou Ubuntu" ;;
  esac
  [[ -d /run/systemd/system ]] || fleet_die "systemd doit être actif dans le LXC"
  fleet_need_command apt-get
  fleet_need_command systemctl
  if command -v systemd-detect-virt >/dev/null 2>&1; then
    virtualization=$(systemd-detect-virt --container 2>/dev/null || true)
    if [[ -n ${virtualization} && ${virtualization} != lxc ]]; then
      fleet_log "Avertissement: environnement détecté: ${virtualization}; LXC est recommandé."
    fi
  fi
}

fleet_install_base_packages() {
  fleet_log "Installation des paquets système de base"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install --yes --no-install-recommends \
    apt-transport-https \
    ca-certificates \
    curl \
    debian-archive-keyring \
    debian-keyring \
    git \
    gnupg \
    jq \
    openssl \
    rsync
}

fleet_install_uv() {
  local version=$1
  if command -v uv >/dev/null 2>&1 && uv --version | grep -Fq "uv ${version}"; then
    fleet_log "uv ${version} est déjà installé"
    return
  fi
  fleet_log "Installation de uv ${version} dans /usr/local/bin"
  curl -LsSf "https://astral.sh/uv/${version}/install.sh" |
    env UV_UNMANAGED_INSTALL=/usr/local/bin sh
  fleet_need_command uv
  uv --version
}

fleet_install_node() {
  local minimum_major=$1
  local pnpm_version=$2
  local current_major=0
  local key_tmp
  local architecture
  if command -v node >/dev/null 2>&1; then
    current_major=$(node -p 'Number(process.versions.node.split(".")[0])')
  fi
  if ((current_major < minimum_major)); then
    fleet_log "Installation de Node.js ${minimum_major}.x depuis le dépôt NodeSource signé"
    install -d -m 0755 /usr/share/keyrings
    key_tmp=$(mktemp)
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key -o "$key_tmp"
    gpg --batch --yes --dearmor \
      --output /usr/share/keyrings/nodesource.gpg "$key_tmp"
    rm -f -- "$key_tmp"
    chmod 0644 /usr/share/keyrings/nodesource.gpg
    architecture=$(dpkg --print-architecture)
    case "$architecture" in
      amd64|arm64) ;;
      *) fleet_die "architecture NodeSource non supportée: $architecture" ;;
    esac
    cat > /etc/apt/sources.list.d/nodesource.sources <<EOF
Types: deb
URIs: https://deb.nodesource.com/node_${minimum_major}.x
Suites: nodistro
Components: main
Architectures: ${architecture}
Signed-By: /usr/share/keyrings/nodesource.gpg
EOF
    apt-get update
    apt-get install --yes --no-install-recommends nodejs
  fi
  current_major=$(node -p 'Number(process.versions.node.split(".")[0])')
  ((current_major >= minimum_major)) || fleet_die "Node.js ${minimum_major}+ est requis"
  fleet_need_command npm
  fleet_log "Installation de pnpm ${pnpm_version}"
  npm install --global "pnpm@${pnpm_version}"
  node --version
  pnpm --version
}

fleet_install_caddy() {
  local key_tmp
  local list_tmp
  if command -v caddy >/dev/null 2>&1; then
    fleet_log "Caddy est déjà installé"
    return
  fi
  fleet_log "Installation de Caddy depuis le dépôt stable officiel"
  install -d -m 0755 /usr/share/keyrings
  key_tmp=$(mktemp)
  list_tmp=$(mktemp)
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key -o "$key_tmp"
  gpg --batch --yes --dearmor \
    --output /usr/share/keyrings/caddy-stable-archive-keyring.gpg "$key_tmp"
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt -o "$list_tmp"
  install -m 0644 "$list_tmp" /etc/apt/sources.list.d/caddy-stable.list
  chmod 0644 /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  rm -f -- "$key_tmp" "$list_tmp"
  apt-get update
  apt-get install --yes --no-install-recommends caddy
  caddy version
}

fleet_assert_secret_file() {
  local path=$1
  [[ -f $path ]] || fleet_die "fichier secret absent: $path"
  [[ ! -L $path ]] || fleet_die "un fichier secret ne peut pas être un lien symbolique: $path"
  if [[ -n $(find "$path" -prune -perm /077 -print) ]]; then
    fleet_die "le fichier secret doit être en mode 0600: $path"
  fi
}

fleet_secure_remove() {
  local path=$1
  [[ -e $path ]] || return 0
  if command -v shred >/dev/null 2>&1; then
    shred -u -- "$path" 2>/dev/null || rm -f -- "$path"
  else
    rm -f -- "$path"
  fi
}
