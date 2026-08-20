import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def test_revoked_worker_token_cannot_open_a_new_websocket(
    authenticated_client: TestClient,
) -> None:
    client = authenticated_client
    registered = client.post(
        "/api/v1/workers",
        json={"name": "worker-revocable", "labels": ["security-test"]},
    )
    assert registered.status_code == 201, registered.text
    worker_id = registered.json()["id"]
    token = registered.json()["token"]

    revoked = client.post(f"/api/v1/workers/{worker_id}/revoke")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(
            f"/api/v1/workers/connect?worker_id={worker_id}",
            headers={"Authorization": f"Bearer {token}"},
        ):
            pass
    assert closed.value.code == 4401
