from fastapi.testclient import TestClient


def test_owner_session_is_httponly_csrf_protected_and_revocable(client: TestClient) -> None:
    bootstrap = client.post(
        "/api/v1/auth/bootstrap",
        headers={"X-Bootstrap-Token": "test-bootstrap-token-long-enough"},
        json={
            "email": "security-owner@example.com",
            "display_name": "Security Owner",
            "password": "correct-horse-battery-staple",
            "tenant_name": "Tenant sécurité",
        },
    )
    assert bootstrap.status_code == 201, bootstrap.text
    cookies = bootstrap.headers.get_list("set-cookie")
    session_cookie = next(item for item in cookies if "agent_fleet_session=" in item)
    csrf_cookie = next(item for item in cookies if "agent_fleet_csrf=" in item)
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert "HttpOnly" not in csrf_cookie
    assert "SameSite=strict" in csrf_cookie

    assert client.get("/api/v1/spaces").status_code == 200
    mutation = client.post(
        "/api/v1/spaces",
        json={"name": "Sans CSRF", "slug": "sans-csrf", "kind": "custom"},
    )
    assert mutation.status_code == 403

    csrf_token = bootstrap.json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf_token
    mutation = client.post(
        "/api/v1/spaces",
        json={"name": "Avec CSRF", "slug": "avec-csrf", "kind": "custom"},
    )
    assert mutation.status_code == 201, mutation.text
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
