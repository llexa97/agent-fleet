# ADR-0002 — Connexion worker sortante et ACP local

Statut : acceptée
Date : 20 août 2026

## Contexte

Les harness doivent travailler près des projets dans différents LXC sans exposer de ports internes ni donner au plan de contrôle un accès SSH général.

## Décision

Chaque worker ouvre une connexion WSS permanente vers le plan de contrôle. Le protocole interne transporte les commandes et événements versionnés. Le worker lance le harness et parle ACP v1 sur `stdin/stdout` localement. Aucun port ACP n’est publié.

## Alternatives

- SSH par commande : rejeté, surface de privilège et reprise de session médiocres.
- ACP distant directement sur WebSocket : rejeté comme fondation de production tant que ce transport et ACP v2 évoluent.
- polling worker HTTP uniquement : possible mais streaming/permissions moins efficaces ; conservé comme stratégie de secours future.

## Conséquences

Le worker doit gérer heartbeat, backoff, journal local et supervision. Le firewall peut néanmoins interdire toute entrée sur les LXC worker et les workspaces restent locaux.
