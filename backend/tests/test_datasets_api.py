from fastapi.testclient import TestClient

LEDGER_CSV = b"Id,Amount,Issued\nINV1,100.00,01-Jan-24\n"
LEDGER_MAPPING = [
    {"source_header": "Id", "canonical_field": "id", "confidence": 1.0},
    {"source_header": "Amount", "canonical_field": "amount", "confidence": 1.0},
    {"source_header": "Issued", "canonical_field": "issued_at", "confidence": 1.0},
]
GATEWAY_CSV = b"Id,Gross,Captured\nPAY1,100.00,01-Jan-24\n"
GATEWAY_MAPPING = [
    {"source_header": "Id", "canonical_field": "id", "confidence": 1.0},
    {"source_header": "Gross", "canonical_field": "gross", "confidence": 1.0},
    {"source_header": "Captured", "canonical_field": "captured_at", "confidence": 1.0},
]
SETTLEMENT_CSV = b"Id,Payout,Settled\nSTL1,100.00,03-Jan-24\n"
SETTLEMENT_MAPPING = [
    {"source_header": "Id", "canonical_field": "id", "confidence": 1.0},
    {"source_header": "Payout", "canonical_field": "payout", "confidence": 1.0},
    {"source_header": "Settled", "canonical_field": "settled_at", "confidence": 1.0},
]
BANK_CSV = b"Id,Date\nBNK1,03-Jan-24\n"
BANK_MAPPING = [
    {"source_header": "Id", "canonical_field": "id", "confidence": 1.0},
    {"source_header": "Date", "canonical_field": "value_date", "confidence": 1.0},
]

_ROLE_FIXTURES = {
    "ledger": (LEDGER_CSV, LEDGER_MAPPING),
    "gateway": (GATEWAY_CSV, GATEWAY_MAPPING),
    "settlement": (SETTLEMENT_CSV, SETTLEMENT_MAPPING),
    "bank": (BANK_CSV, BANK_MAPPING),
}


def _register(client: TestClient, username: str) -> None:
    response = client.post("/auth/register", json={"username": username, "password": "x"})
    assert response.status_code == 201, response.text


def _upload_role(client: TestClient, dataset_id: str, role: str, csv_bytes: bytes, mapping: list[dict[str, object]]):  # type: ignore[no-untyped-def]
    import json

    return client.post(
        f"/datasets/{dataset_id}/files",
        data={"role": role, "mapping": json.dumps(mapping)},
        files={"file": (f"{role}.csv", csv_bytes, "text/csv")},
    )


def _upload_all_roles(client: TestClient, dataset_id: str) -> None:
    for role, (csv_bytes, mapping) in _ROLE_FIXTURES.items():
        response = _upload_role(client, dataset_id, role, csv_bytes, mapping)
        assert response.status_code == 200, response.text


def test_generated_dataset_is_immediately_ready_with_no_raw_files(runs_client: TestClient) -> None:
    _register(runs_client, "gen-user")

    response = runs_client.post(
        "/datasets", json={"name": "seed corpus", "source": "generated", "seed": 1001, "size": 30}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["seed"] == 1001
    assert len(body["files"]) == 4
    assert all(f["has_raw"] is False for f in body["files"])
    assert all(f["valid_count"] > 0 for f in body["files"])


def test_uploaded_dataset_starts_incomplete_and_becomes_ready_after_all_four_files(runs_client: TestClient) -> None:
    _register(runs_client, "upload-user")

    create = runs_client.post("/datasets", json={"name": "my books", "source": "uploaded"})
    assert create.status_code == 201, create.text
    dataset_id = create.json()["id"]
    assert create.json()["status"] == "incomplete"

    upload = _upload_role(runs_client, dataset_id, "ledger", LEDGER_CSV, LEDGER_MAPPING)
    assert upload.status_code == 200, upload.text
    assert upload.json()["valid_count"] == 1
    assert upload.json()["dataset"]["status"] == "incomplete"

    _upload_role(runs_client, dataset_id, "gateway", GATEWAY_CSV, GATEWAY_MAPPING)
    _upload_role(runs_client, dataset_id, "settlement", SETTLEMENT_CSV, SETTLEMENT_MAPPING)
    final = _upload_role(runs_client, dataset_id, "bank", BANK_CSV, BANK_MAPPING)

    assert final.json()["dataset"]["status"] == "ready"


def test_reuploading_the_same_role_overwrites_rather_than_duplicates(runs_client: TestClient) -> None:
    _register(runs_client, "reupload-user")
    dataset_id = runs_client.post("/datasets", json={"name": "d", "source": "uploaded"}).json()["id"]

    _upload_role(runs_client, dataset_id, "ledger", LEDGER_CSV, LEDGER_MAPPING)
    second_csv = b"Id,Amount,Issued\nINV1,100.00,01-Jan-24\nINV2,200.00,02-Jan-24\n"
    response = _upload_role(runs_client, dataset_id, "ledger", second_csv, LEDGER_MAPPING)

    assert response.json()["valid_count"] == 2
    detail = runs_client.get(f"/datasets/{dataset_id}").json()
    ledger_file = next(f for f in detail["files"] if f["role"] == "ledger")
    assert ledger_file["valid_count"] == 2  # replaced, not appended


def test_dataset_across_users_is_404_not_403(runs_client: TestClient) -> None:
    _register(runs_client, "owner")
    dataset_id = runs_client.post("/datasets", json={"name": "d", "source": "generated", "seed": 1}).json()["id"]
    runs_client.post("/auth/logout")

    _register(runs_client, "attacker")
    response = runs_client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 404


def test_get_dataset_file_records_supports_pagination(runs_client: TestClient) -> None:
    _register(runs_client, "records-user")
    dataset_id = runs_client.post(
        "/datasets", json={"name": "d", "source": "generated", "seed": 1001, "size": 30}
    ).json()["id"]

    first_page = runs_client.get(f"/datasets/{dataset_id}/files/ledger/records", params={"offset": 0, "limit": 2})
    assert first_page.status_code == 200, first_page.text
    body = first_page.json()
    assert body["role"] == "ledger"
    assert body["total"] > 2
    assert len(body["records"]) == 2

    second_page = runs_client.get(f"/datasets/{dataset_id}/files/ledger/records", params={"offset": 2, "limit": 2})
    assert second_page.json()["records"][0]["id"] != body["records"][0]["id"]


def test_get_dataset_file_raw_returns_the_original_upload(runs_client: TestClient) -> None:
    _register(runs_client, "raw-user")
    dataset_id = runs_client.post("/datasets", json={"name": "d", "source": "uploaded"}).json()["id"]
    _upload_role(runs_client, dataset_id, "ledger", LEDGER_CSV, LEDGER_MAPPING)

    response = runs_client.get(f"/datasets/{dataset_id}/files/ledger/raw")

    assert response.status_code == 200
    assert response.content == LEDGER_CSV


def test_get_dataset_file_raw_is_404_for_a_generated_dataset(runs_client: TestClient) -> None:
    _register(runs_client, "no-raw-user")
    dataset_id = runs_client.post("/datasets", json={"name": "d", "source": "generated", "seed": 1}).json()["id"]

    response = runs_client.get(f"/datasets/{dataset_id}/files/ledger/raw")

    assert response.status_code == 404


def test_run_against_an_incomplete_dataset_is_rejected(runs_client: TestClient) -> None:
    _register(runs_client, "incomplete-runner")
    dataset_id = runs_client.post("/datasets", json={"name": "d", "source": "uploaded"}).json()["id"]

    response = runs_client.post("/runs", json={"source": "dataset", "dataset_id": dataset_id})

    assert response.status_code == 422


def test_run_against_an_unknown_dataset_is_404(runs_client: TestClient) -> None:
    _register(runs_client, "unknown-runner")

    response = runs_client.post("/runs", json={"source": "dataset", "dataset_id": "does-not-exist"})

    assert response.status_code == 404


def test_run_against_another_users_dataset_is_404(runs_client: TestClient) -> None:
    _register(runs_client, "dataset-owner")
    dataset_id = runs_client.post("/datasets", json={"name": "d", "source": "generated", "seed": 1}).json()["id"]
    runs_client.post("/auth/logout")

    _register(runs_client, "dataset-attacker")
    response = runs_client.post("/runs", json={"source": "dataset", "dataset_id": dataset_id})

    assert response.status_code == 404


def test_run_against_a_ready_dataset_is_accepted(runs_client: TestClient) -> None:
    _register(runs_client, "ready-runner")
    dataset_id = runs_client.post(
        "/datasets", json={"name": "d", "source": "generated", "seed": 1001, "size": 30}
    ).json()["id"]

    response = runs_client.post("/runs", json={"source": "dataset", "dataset_id": dataset_id})

    assert response.status_code == 202
    assert response.json()["source"] == "dataset"


def test_dataset_name_must_be_unique_per_user_but_not_across_users(runs_client: TestClient) -> None:
    _register(runs_client, "name-user-a")
    first = runs_client.post("/datasets", json={"name": "Synthetic-1", "source": "uploaded"})
    assert first.status_code == 201, first.text

    duplicate = runs_client.post("/datasets", json={"name": "  Synthetic-1  ", "source": "uploaded"})
    assert duplicate.status_code == 422, duplicate.text
    assert "already have a dataset named" in duplicate.json()["detail"]

    # The same name under a different tenant is fine -- uniqueness is per user.
    _register(runs_client, "name-user-b")
    other_tenant = runs_client.post("/datasets", json={"name": "Synthetic-1", "source": "uploaded"})
    assert other_tenant.status_code == 201, other_tenant.text
