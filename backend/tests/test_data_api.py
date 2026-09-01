import json

from fastapi.testclient import TestClient

from app.routers.data import get_mapper_gateway
from llm.cache import ResponseCache
from llm.client import FakeClient
from llm.gateway import LlmGateway
from llm.governor import Governor
from llm.models import BACKUP_MODEL, PRIMARY_MODEL


def _override_gateway(client: TestClient, fixtures: dict[str, str]) -> None:
    from app.main import app

    def _factory() -> LlmGateway:
        redis_client = app.state.redis_client
        governor = Governor(
            redis_client=redis_client,
            rpm_limits={PRIMARY_MODEL: 1000, BACKUP_MODEL: 1000},
            rpd_limits={PRIMARY_MODEL: 1000, BACKUP_MODEL: 1000},
            user_daily_quota=1000,
        )
        return LlmGateway(
            client=FakeClient(fixtures),
            governor=governor,
            cache=ResponseCache(redis_client),
            schema_version="mapper-v1",
        )

    app.dependency_overrides[get_mapper_gateway] = _factory


def _register(client: TestClient, username: str) -> None:
    response = client.post("/auth/register", json={"username": username, "password": "x"})
    assert response.status_code == 201, response.text


def _mapping_fixture_response() -> str:
    return json.dumps(
        {
            "fields": [
                {"source_header": "Txn Date", "canonical_field": "value_date", "confidence": 0.9},
                {"source_header": "Description", "canonical_field": "narration", "confidence": 0.9},
                {"source_header": "Amount", "canonical_field": "credit", "confidence": 0.8},
            ]
        }
    )


def test_preview_returns_headers_sample_rows_and_llm_mapping(runs_client: TestClient) -> None:
    _register(runs_client, "preview-user")

    from ingest.mapper import build_prompt
    from ingest.tabular import ParsedTable

    table = ParsedTable(
        headers=["Txn Date", "Description", "Amount"],
        rows=[{"Txn Date": "01-Jan-24", "Description": "NEFT", "Amount": "100.00"}],
    )
    prompt = build_prompt("bank", table)
    _override_gateway(runs_client, {prompt: _mapping_fixture_response()})

    csv_bytes = b"Txn Date,Description,Amount\n01-Jan-24,NEFT,100.00\n"
    response = runs_client.post(
        "/data/preview",
        data={"role": "bank"},
        files={"file": ("statement.csv", csv_bytes, "text/csv")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["headers"] == ["Txn Date", "Description", "Amount"]
    assert len(body["sample_rows"]) == 1
    mapped = {f["source_header"]: f["canonical_field"] for f in body["mapping"]}
    assert mapped["Txn Date"] == "value_date"
    assert mapped["Description"] == "narration"
    assert "id" in body["canonical_fields"]


def test_preview_rejects_unknown_role(runs_client: TestClient) -> None:
    _register(runs_client, "bad-role-user")

    response = runs_client.post(
        "/data/preview",
        data={"role": "not-a-real-role"},
        files={"file": ("statement.csv", b"a,b\n1,2\n", "text/csv")},
    )

    assert response.status_code == 422


def test_validate_reports_valid_rows_and_row_errors(runs_client: TestClient) -> None:
    _register(runs_client, "validate-user")

    csv_bytes = b"Id,Value Date,Narration,Credit\nBNK1,01-Jan-24,NEFT CREDIT,100.00\nBNK2,not-a-date,NEFT,50.00\n"
    mapping = json.dumps(
        [
            {"source_header": "Id", "canonical_field": "id", "confidence": 1.0},
            {"source_header": "Value Date", "canonical_field": "value_date", "confidence": 1.0},
            {"source_header": "Narration", "canonical_field": "narration", "confidence": 1.0},
            {"source_header": "Credit", "canonical_field": "credit", "confidence": 1.0},
        ]
    )

    response = runs_client.post(
        "/data/validate",
        data={"role": "bank", "mapping": mapping},
        files={"file": ("statement.csv", csv_bytes, "text/csv")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_rows"] == 2
    assert body["valid_count"] == 1
    assert len(body["errors"]) == 1
    assert body["errors"][0]["row_number"] == 2


def test_validate_rejects_malformed_mapping_payload(runs_client: TestClient) -> None:
    _register(runs_client, "malformed-mapping-user")

    response = runs_client.post(
        "/data/validate",
        data={"role": "bank", "mapping": "not json"},
        files={"file": ("statement.csv", b"Id\nBNK1\n", "text/csv")},
    )

    assert response.status_code == 422
