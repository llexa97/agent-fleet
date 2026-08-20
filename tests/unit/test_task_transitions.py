from apps.api.agent_fleet_api.services.task_service import ALLOWED_TASK_TRANSITIONS


def test_terminal_task_states_cannot_restart_silently() -> None:
    assert ALLOWED_TASK_TRANSITIONS["completed"] == set()
    assert ALLOWED_TASK_TRANSITIONS["cancelled"] == set()


def test_failed_task_retry_is_explicit() -> None:
    assert "queued" in ALLOWED_TASK_TRANSITIONS["failed"]
    assert "running" not in ALLOWED_TASK_TRANSITIONS["failed"]
