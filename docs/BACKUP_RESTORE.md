# Sauvegarde et restauration

## Périmètre

Une sauvegarde Control Plane contient :

- dump PostgreSQL au format custom ;
- `/etc/agent-fleet` (configuration et références/secrets locaux) ;
- Caddy et unités Agent Fleet utiles au redéploiement ;
- snapshot portable de la release active, sans `.venv`, `node_modules`, `.git` ni fichier `.env` ;
- manifeste de version et checksums.

Les workspaces des workers suivent leur propre stratégie Git/stockage. Redis n’est pas sauvegardé. Les credentials fournisseurs restent sur les workers et doivent être gérés par leur politique de secrets.

## Chiffrement

Le script exige toujours un destinataire [age](https://age-encryption.org/). Il n’existe pas de mode en clair : le dump PostgreSQL et `/etc/agent-fleet` contiennent nécessairement des données sensibles. Conserver la clé privée hors du Control Plane, idéalement dans un coffre et une copie de secours hors ligne. Tester le déchiffrement de la clé avant de considérer une archive comme valide.

Une clé HMAC indépendante authentifie le sidecar checksum. Elle contient au moins 32 octets, reste hors Git et hors archive et doit être disponible lors d’une restauration. Idéalement, la fournir à chaque exécution depuis un coffre ou un credential systemd temporaire. Exemple initial :

```bash
sudo install -m 0600 /dev/null /root/agent-fleet-backup-integrity.key
sudo openssl rand -out /root/agent-fleet-backup-integrity.key 32
```

```bash
sudo install -d -m 0700 /var/backups/agent-fleet
sudo AGENT_FLEET_BACKUP_AGE_RECIPIENT='age1...' \
  ./scripts/backup.sh \
    --integrity-key-file /root/agent-fleet-backup-integrity.key \
    --output-dir /var/backups/agent-fleet
```

Le résultat comprend `.tar.age`, `.sha256` et `.sha256.hmac`, tous en mode restrictif. Le script ne journalise ni URL de base ni secret. La restauration exige les trois fichiers : le HMAC authentifie le sidecar, le checksum détecte une corruption avant déchiffrement et `age` assure confidentialité et intégrité du contenu.

## Planification

Exemple systemd/cron : sauvegarde quotidienne, rétention locale courte, copie hors LXC du trio archive/checksum/HMAC. Ne placez ni identité `age` privée ni clé HMAC directement dans l’unité ; utilisez un gestionnaire de credentials. Surveiller code de sortie, taille, ancienneté et espace disque.

Politique minimale indicative :

- 7 quotidiennes ;
- 5 hebdomadaires ;
- 12 mensuelles ;
- une copie hors site ;
- test de restauration trimestriel.

## Préparation d’une restauration

Une restauration écrase l’état de la base cible. Toujours :

1. isoler le LXC cible du trafic et arrêter API/dispatcher ;
2. vérifier version applicative, migration attendue et espace disque ;
3. copier archive, checksum, HMAC, identité age et clé HMAC localement en mode `0600` ;
4. vérifier l’authentification HMAC puis le checksum ;
5. effectuer d’abord une restauration dans une base/LXC temporaire ;
6. documenter l’autorisation et le point de retour.

## Test temporaire recommandé

Créer une base vide distincte et un fichier d’environnement temporaire. Le script accepte un autre fichier d’environnement :

```bash
sudo ./scripts/restore.sh \
  --archive /var/backups/agent-fleet/agent-fleet-<date>.tar.age \
  --identity /root/backup-identity.txt \
  --recipient 'age1...' \
  --integrity-key-file /root/agent-fleet-backup-integrity.key \
  --env-file /etc/agent-fleet/restore-test.env \
  --allow-running \
  --confirm-restore
```

Pointer `restore-test.env` vers une base isolée, jamais la production pendant le test. Vérifier ensuite :

- `alembic current` et migrations ;
- nombre de tenants, messages, livraisons et audits ;
- contraintes et index ;
- login test révoqué après validation ;
- absence de worker production reconnecté au test.

## Restauration de production

```bash
sudo systemctl stop agent-fleet-dispatcher agent-fleet-api
sudo ./scripts/restore.sh \
  --archive /var/backups/agent-fleet/agent-fleet-<date>.tar.age \
  --identity /root/backup-identity.txt \
  --recipient 'age1...' \
  --integrity-key-file /root/agent-fleet-backup-integrity.key \
  --env-file /etc/agent-fleet/control-plane.env \
  --confirm-restore
sudo systemctl start agent-fleet-api agent-fleet-dispatcher
curl --fail --silent http://127.0.0.1:8000/api/v1/readiness
```

`--recipient` est la clé publique utilisée pour chiffrer immédiatement le point de retour local ; elle peut être la même que pour la sauvegarde source. Le script crée ce dump de sécurité avant le `pg_restore --clean --if-exists`, et s’arrête si sa création ou son chiffrement échoue. Aucun dump clair n’est conservé.

La configuration n’est restaurée qu’avec `--restore-config`. Avant modification, `/etc/agent-fleet`, le Caddyfile/drop-in et les unités `agent-fleet-*` sont regroupés dans une archive de sécurité `age`. La copie restaurée est exacte : les fichiers Agent Fleet absents de la sauvegarde sont supprimés, les liens symboliques de configuration sont refusés et les services ne sont pas redémarrés automatiquement. Le script recharge seulement systemd.

`--restore-application` extrait en plus le snapshot sous `/opt/agent-fleet-restored/<date>` sans modifier le lien actif. Inspecter ce répertoire, recréer les dépendances avec `uv sync --frozen`/pnpm, puis utiliser la procédure d’installation ou de rollback ; le script de restauration ne lance jamais du code provenant d’une archive.

## Après restauration

- appliquer uniquement les migrations de la version déployée ;
- révoquer/faire tourner les secrets si l’incident concernait une fuite ;
- vérifier les livraisons avec lease expiré et les sessions actives ;
- reconnecter un worker canari ;
- vérifier les audits, métriques et erreurs ;
- remettre le trafic après validation fonctionnelle ;
- conserver les points de retour chiffrés jusqu’à clôture.

## Rétention et suppression

La suppression d’archives est une action séparée et validée par l’opérateur. Aucun script fourni ne supprime automatiquement des sauvegardes. Appliquer la rétention seulement après vérification qu’au moins une archive récente a réussi un test de restauration.
