from prometheus_client import Counter, Gauge, Histogram

workers_connected = Gauge("agent_fleet_workers_connected", "Workers connectés")
active_sessions = Gauge("agent_fleet_sessions_active", "Sessions ACP actives")
pending_deliveries = Gauge("agent_fleet_deliveries_pending", "Livraisons en attente")
pending_permissions = Gauge("agent_fleet_permissions_pending", "Approbations en attente")
failed_deliveries = Counter("agent_fleet_delivery_failures_total", "Livraisons échouées")
prompt_duration = Histogram(
    "agent_fleet_prompt_duration_seconds",
    "Durée des prompts ACP",
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 900, 3600),
)
tool_calls = Counter("agent_fleet_tool_calls_total", "Appels d’outils fleet.*", ["tool"])
