from fastapi.testclient import TestClient


def test_register_creates_user_and_sets_httponly_cookie(auth_client: TestClient) -> None:
    response = auth_client.post("/auth/register", json={"username": "alice", "password": "a"})
    assert response.status_code == 201
    assert response.json()["username"] == "alice"

    set_cookie = response.headers.get("set-cookie", "")
    assert "ledgerline_session=" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()


def test_duplicate_username_registration_is_rejected(auth_client: TestClient) -> None:
    auth_client.post("/auth/register", json={"username": "bob", "password": "x"})
    response = auth_client.post("/auth/register", json={"username": "bob", "password": "y"})
    assert response.status_code == 409


def test_empty_username_is_rejected(auth_client: TestClient) -> None:
    response = auth_client.post("/auth/register", json={"username": "", "password": "x"})
    assert response.status_code == 422


def test_empty_password_is_rejected(auth_client: TestClient) -> None:
    response = auth_client.post("/auth/register", json={"username": "carol", "password": ""})
    assert response.status_code == 422


def test_single_character_password_is_accepted(auth_client: TestClient) -> None:
    response = auth_client.post("/auth/register", json={"username": "dave", "password": "a"})
    assert response.status_code == 201


def test_login_with_correct_credentials_succeeds(auth_client: TestClient) -> None:
    auth_client.post("/auth/register", json={"username": "erin", "password": "correct-horse"})
    auth_client.cookies.clear()

    response = auth_client.post("/auth/login", json={"username": "erin", "password": "correct-horse"})
    assert response.status_code == 200
    assert response.json()["username"] == "erin"


def test_login_with_wrong_password_is_rejected(auth_client: TestClient) -> None:
    auth_client.post("/auth/register", json={"username": "frank", "password": "right"})
    auth_client.cookies.clear()

    response = auth_client.post("/auth/login", json={"username": "frank", "password": "wrong"})
    assert response.status_code == 401


def test_login_with_unknown_username_is_rejected(auth_client: TestClient) -> None:
    response = auth_client.post("/auth/login", json={"username": "nobody", "password": "x"})
    assert response.status_code == 401


def test_me_without_cookie_is_unauthorized(auth_client: TestClient) -> None:
    response = auth_client.get("/auth/me")
    assert response.status_code == 401


def test_me_with_valid_session_returns_the_logged_in_user(auth_client: TestClient) -> None:
    auth_client.post("/auth/register", json={"username": "grace", "password": "x"})
    response = auth_client.get("/auth/me")
    assert response.status_code == 200
    assert response.json()["username"] == "grace"


def test_logout_revokes_the_session_immediately(auth_client: TestClient) -> None:
    auth_client.post("/auth/register", json={"username": "heidi", "password": "x"})
    assert auth_client.get("/auth/me").status_code == 200

    logout_response = auth_client.post("/auth/logout")
    assert logout_response.status_code == 204

    assert auth_client.get("/auth/me").status_code == 401


def test_invalid_session_cookie_is_unauthorized(auth_client: TestClient) -> None:
    auth_client.cookies.set("ledgerline_session", "not-a-real-session-id")
    response = auth_client.get("/auth/me")
    assert response.status_code == 401


def test_login_rate_limited_by_ip(auth_client: TestClient) -> None:
    for _ in range(10):
        auth_client.post("/auth/login", json={"username": "nobody", "password": "x"})
    response = auth_client.post("/auth/login", json={"username": "nobody", "password": "x"})
    assert response.status_code == 429


def test_registering_creates_no_data(auth_client: TestClient) -> None:
    """An account opens empty. The corpus a run is scored against carries a
    seed, a size and an answer key the user chose, so registration is not the
    place to invent one on their behalf -- the console's tour is."""
    auth_client.post("/auth/register", json={"username": "newcomer", "password": "correct horse"})

    assert auth_client.get("/datasets").json() == []
    assert auth_client.get("/runs").json() == []


def test_registering_does_not_queue_background_work(auth_client: TestClient) -> None:
    """Signing up creates a user and a session and nothing else: no dataset to
    build, so registration answers in the time a password hash takes."""
    response = auth_client.post(
        "/auth/register", json={"username": "quick", "password": "correct horse"}
    )

    assert response.status_code == 201
    assert set(response.json()) == {"id", "username"}
