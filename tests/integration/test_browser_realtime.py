from typing import cast

from fastapi.testclient import TestClient


def _direction_channel(client: TestClient) -> str:
    business = next(
        item for item in client.get("/api/v1/spaces").json() if item["slug"] == "business"
    )
    channels = client.get("/api/v1/channels", params={"space_id": business["id"]})
    assert channels.status_code == 200
    return str(next(item["id"] for item in channels.json() if item["slug"] == "direction"))


def _post_plain_message(
    client: TestClient,
    channel_id: str,
    *,
    key: str,
    content: str,
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/channels/{channel_id}/messages",
        headers={"Idempotency-Key": key},
        json={"content": content, "mentions": [], "expects_response": False},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, object], response.json())


def test_browser_websocket_replays_events_after_reconnection(
    authenticated_client: TestClient,
) -> None:
    client = authenticated_client
    channel_id = _direction_channel(client)

    first_message = _post_plain_message(
        client,
        channel_id,
        key="realtime-browser-message-0001",
        content="Premier message persistant",
    )
    history = client.get("/api/v1/events")
    assert history.status_code == 200
    first_cursor = history.json()[-1]["event_id"]

    second_message = _post_plain_message(
        client,
        channel_id,
        key="realtime-browser-message-0002",
        content="Message publié pendant la déconnexion",
    )
    with client.websocket_connect(f"/api/v1/events/ws?after={first_cursor}") as websocket:
        replayed = websocket.receive_json()
        assert replayed["event_type"] == "message.created"
        assert replayed["payload"]["message_id"] == second_message["id"]
        assert replayed["payload"]["message_id"] != first_message["id"]
        second_cursor = replayed["event_id"]
        websocket.send_text("ping")
        assert websocket.receive_text() == "pong"

    third_message = _post_plain_message(
        client,
        channel_id,
        key="realtime-browser-message-0003",
        content="État récupéré après un second rechargement",
    )
    with client.websocket_connect(f"/api/v1/events/ws?after={second_cursor}") as websocket:
        replayed = websocket.receive_json()
        assert replayed["payload"]["message_id"] == third_message["id"]
