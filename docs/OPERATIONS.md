# Runbook d’exploitation

## Ordre de démarrage

1. PostgreSQL ;
2. Redis ;
3. migration Alembic ;
4. API ;
5. dispatcher ;
6. Caddy ;
7. workers.

L’arrêt inverse commence par dispatcher pour ne plus affecter de travail, puis API. Les workers peuvent terminer un tour pendant une maintenance courte mais leurs buffers sont bornés.

## Vérifications quotidiennes

```bash
systemctl --failed
systemctl status agent-fleet-api agent-fleet-dispatcher --no-pager
curl --fail --silent http://127.0.0.1:8000/api/v1/readiness
journalctl -u agent-fleet-api -u agent-fleet-dispatcher --since today -p warning
```

Dans l’UI/métriques : workers offline, livraisons `retry_scheduled`/`failed`, permissions anciennes, sessions actives anormalement longues, coût/tokens et espace disque.

## Worker hors ligne

1. La livraison doit rester pending/retry, jamais disparaître.
2. Vérifier révocation/expiration du credential dans Runners.
3. Sur le worker : heure, DNS, certificat et `journalctl`.
4. Tester uniquement HTTPS public, sans désactiver TLS.
5. Vérifier executable/workspace et capacité.
6. Redémarrer le service ; la réconciliation doit dédupliquer les commandes.

## Harness en échec

Inspecter l’erreur structurée et la version, puis exécuter `health_check` comme utilisateur du service sans afficher l’environnement. Un crash doit terminer/recréer le groupe selon la politique de retry. Ne relancez pas manuellement une tâche terminale avec la même clé.

## Livraison bloquée

Vérifier statut, `available_at`, lease, worker/session, budget et permission. Un lease expiré est repris automatiquement. Ne modifiez pas les tables directement ; utiliser pause/reprise/réassignation ou une commande administrative auditée.

## Redis indisponible

Les messages restent dans PostgreSQL. Le temps réel et les réveils ralentissent ; le polling du dispatcher continue. Restaurer Redis, puis vérifier backlog et reconnexion navigateur. Aucune restauration Redis n’est nécessaire.

## PostgreSQL indisponible

Readiness échoue et les mutations doivent refuser proprement. Ne laissez pas le dispatcher consommer sans source de vérité. Restaurer PostgreSQL, vérifier migrations et intégrité, puis redémarrer API/dispatcher.

## Rotation d’un worker

Créer un nouveau credential, l’installer sans l’afficher, redémarrer et vérifier la connexion. Révoquer ensuite l’ancien et confirmer qu’une tentative avec celui-ci échoue. La période de chevauchement doit rester courte.

## Incident de secret

Isoler la machine, révoquer le secret côté fournisseur/Control Plane, conserver les audits, tourner les secrets adjacents, rechercher les usages sans copier les valeurs, puis restaurer si l’intégrité est douteuse. Ne publiez jamais le secret compromis dans un channel Agent Fleet.

## Maintenance

Sauvegarder, arrêter dispatcher, laisser/annuler proprement les sessions, appliquer la release/migration, redémarrer API puis dispatcher et vérifier un worker canari. Le rollback suit [DEPLOYMENT_LXC.md](../DEPLOYMENT_LXC.md#rollback).
