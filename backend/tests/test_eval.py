import json

from fastapi.testclient import TestClient

from app.main import app
from contracts.models import RunMetrics
from datagen.generator import generate_corpus
from engine.pipeline import match
from scripts.eval import GOLDEN_SEEDS, GOLDEN_SIZE, golden_path, run_and_score, score_run

_AUTO_RATE_REGRESSION_TOLERANCE = 0.02


def test_golden_metrics_exist_for_all_committed_seeds() -> None:
    for seed in GOLDEN_SEEDS:
        assert golden_path(seed).exists(), f"missing golden metrics for seed {seed}"


def test_regression_gate_auto_rate_and_false_matches() -> None:
    for seed in GOLDEN_SEEDS:
        golden = RunMetrics.model_validate_json(golden_path(seed).read_text())
        current = run_and_score(seed, GOLDEN_SIZE)

        assert current.false_matches == 0, f"seed={seed}: false matches must stay at 0"
        assert current.auto_rate >= golden.auto_rate - _AUTO_RATE_REGRESSION_TOLERANCE, (
            f"seed={seed}: auto_rate regressed from {golden.auto_rate} to {current.auto_rate}"
        )


def test_no_truth_path_reports_none_for_truth_dependent_fields() -> None:
    corpus, _ = generate_corpus(1001, 150)
    result = match(corpus)
    metrics = score_run(corpus, result, truth=None, elapsed_seconds=0.01)

    assert metrics.precision is None
    assert metrics.recall is None
    assert metrics.false_matches is None
    assert metrics.by_class is None
    assert metrics.records > 0
    assert metrics.auto_rate > 0


def test_run_metrics_with_none_precision_round_trips_through_api() -> None:
    corpus, _ = generate_corpus(1001, 150)
    result = match(corpus)
    metrics = score_run(corpus, result, truth=None, elapsed_seconds=0.01)

    payload = json.loads(metrics.model_dump_json())
    assert payload["precision"] is None
    restored = RunMetrics.model_validate(payload)
    assert restored == metrics


def test_run_metrics_serializes_cleanly_through_a_route() -> None:
    corpus, truth = generate_corpus(1001, 150)
    result = match(corpus)
    metrics = score_run(corpus, result, truth, elapsed_seconds=0.01)

    @app.get("/_test/metrics", response_model=RunMetrics)
    async def _metrics() -> RunMetrics:
        return metrics

    client = TestClient(app)
    response = client.get("/_test/metrics")
    assert response.status_code == 200
    assert response.json()["output_hash"] == metrics.output_hash

    app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != "/_test/metrics"]
