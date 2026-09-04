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


def test_registering_seeds_a_ready_demo_dataset(auth_client: TestClient) -> None:
    """An account opens on something the engine can be run against. An empty
    console is an accurate description of a new account and a poor description
    of what the product does."""
    auth_client.post("/auth/register", json={"username": "newcomer", "password": "correct horse"})

    datasets = auth_client.get("/datasets").json()

    assert len(datasets) == 1
    demo = datasets[0]
    assert demo["name"] == "Demo corpus"
    assert demo["source"] == "generated"
    # Ready means all four roles carry records: it can be run without any
    # further setup, which is the whole point of seeding it.
    assert demo["status"] == "ready"
    assert all(f["valid_count"] > 0 for f in demo["files"])
    assert demo["run_count"] == 0


def test_the_demo_corpus_is_the_same_books_for_everyone(auth_client: TestClient) -> None:
    """A fixed seed, so a figure quoted from one demo is one anyone else can
    reproduce."""
    auth_client.post("/auth/register", json={"username": "first-user", "password": "correct horse"})
    first = auth_client.get("/datasets").json()[0]
    auth_client.post("/auth/logout")
    auth_client.post("/auth/register", json={"username": "second-user", "password": "correct horse"})
    second = auth_client.get("/datasets").json()[0]

    assert first["seed"] == second["seed"]
    assert first["size"] == second["size"]
    assert [f["valid_count"] for f in first["files"]] == [f["valid_count"] for f in second["files"]]
    # Separate accounts, separate rows: seeding must not hand two users the
    # same dataset id.
    assert first["id"] != second["id"]


def test_signing_in_again_does_not_seed_another_demo(auth_client: TestClient) -> None:
    """Seeding belongs to registration. Logging in is not the moment to create
    data, and a user who deleted the demo should not have it come back."""
    auth_client.post("/auth/register", json={"username": "returning", "password": "correct horse"})
    demo_id = auth_client.get("/datasets").json()[0]["id"]
    auth_client.delete(f"/datasets/{demo_id}")
    auth_client.post("/auth/logout")

    auth_client.post("/auth/login", json={"username": "returning", "password": "correct horse"})

    assert auth_client.get("/datasets").json() == []
