"""Dataset exploration in local and committed modes."""

import json

import pytest
from fastapi.testclient import TestClient

from app.backend import insights, main
from ledgerlens.insights import dataset_summary, training_summary


@pytest.fixture
def tiny(tmp_path):
    splits = {"train": ["a"], "val": ["b"], "test": ["c"]}
    (tmp_path / "splits.json").write_text(json.dumps(splits))
    for i, identity in enumerate("abc"):
        gold = {
            "doc_type": "statement",
            "currency": "SGD",
            "transactions": [{"date": "2026-01-01", "credit": "100", "debit": None}],
        }
        meta = {
            "business_id": "shared" if i < 2 else "other",
            "layout_id": "held" if i == 2 else "seen",
            "strength": ["clean", "light", "heavy"][i],
            "country": "SG",
            "anomalies": [] if i == 0 else [{"type": "round_trip"}],
        }
        (tmp_path / f"{identity}.json").write_text(json.dumps(gold))
        (tmp_path / f"{identity}.meta.json").write_text(json.dumps(meta))
        (tmp_path / f"{identity}.html").write_text(
            "<html><style>body{color:red}</style>sample</html>"
        )
    return tmp_path


@pytest.mark.parametrize("local", [True, False])
def test_routes(tiny, monkeypatch, local):
    if local:
        monkeypatch.setenv("LEDGERLENS_DATA", str(tiny))
        identity = "a"
    else:
        monkeypatch.delenv("LEDGERLENS_DATA", raising=False)
        identity = next(
            (insights.ROOT / "app/sample/dataset").glob("*.meta.json")
        ).name.removesuffix(".meta.json")
    client = TestClient(main.app)
    source = "local-data" if local else "committed-summary"
    assert client.get("/api/dataset/summary").json()["source"] == source
    listing = client.get("/api/dataset/pages?limit=2").json()
    assert listing["source"] == source
    assert len(listing["pages"]) == 2
    assert client.get(f"/api/dataset/pages/{identity}").json()["source"] == source
    preview = client.get(f"/api/dataset/pages/{identity}/preview")
    assert preview.status_code == 200
    assert (
        preview.headers["content-security-policy"]
        == "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"
    )
    assert preview.headers["x-content-type-options"] == "nosniff"
    for route in ("training/summary", "results/breakdown"):
        assert client.get("/api/" + route).json()["source"] == "committed-summary"
    for identity in ("bad-id", "%2E%2E%2Foutside", "..", "missing"):
        assert client.get(f"/api/dataset/pages/{identity}/preview").status_code == 404
    assert client.get("/api/dataset/pages?limit=101").status_code == 422


def test_filters_and_compute(tiny, monkeypatch):
    monkeypatch.setenv("LEDGERLENS_DATA", str(tiny))
    client = TestClient(main.app)
    for query, expected in [
        ("anomaly=none", ["a"]),
        ("anomaly=any", ["b", "c"]),
        ("anomaly=round_trip&min_strength=3", ["c"]),
        ("split=val&layout=seen&doc_type=statement", ["b"]),
        ("max_strength=0", ["a"]),
        ("offset=2", ["c"]),
    ]:
        assert [
            p["id"] for p in client.get("/api/dataset/pages?" + query).json()["pages"]
        ] == expected
    summary = dataset_summary(tiny)
    assert summary["totals"] == {"pages": 3, "businesses": 2}
    assert summary["leakage"] == {
        "businesses_in_multiple_splits": 1,
        "test_only_layouts": ["held"],
    }
    assert summary["transaction_amount_distribution"]["money_in"] == {"2": 3}
    outside = tiny.parent / "outside.html"
    outside.write_text("outside")
    (tiny / "escape.html").symlink_to(outside)
    assert client.get("/api/dataset/pages/escape/preview").status_code == 404


def test_local_training_and_results(tiny, monkeypatch):
    monkeypatch.setenv("LEDGERLENS_DATA", str(tiny))
    root = tiny.parent / "eval-final"
    (root / "main").mkdir(parents=True)
    (root / "eval").mkdir()
    training = root / "main/train_summary.json"
    training.write_text(
        json.dumps(
            {
                "steps": 10,
                "args": {"lr": 0.001},
                "log_history": [
                    {"step": 10, "loss": 0.1},
                    {"step": 10, "eval_loss": 0.2},
                ],
            }
        )
    )
    score = {
        "rows": {"f1": 0.5},
        "header_accuracy": 1,
        "page_reconciles": True,
        "reconciliation_accuracy": 1,
    }
    (root / "eval/results.json").write_text(
        json.dumps({"test": {"4b-ft": {"pages": {"c": score}, "valid_json_rate": 1}}})
    )
    client = TestClient(main.app)
    assert client.get("/api/training/summary").json()["source"] == "local-data"
    result = client.get("/api/results/breakdown").json()
    assert result["source"] == "local-data"
    assert result["splits"]["test"]["hardest_pages"][0]["id"] == "c"
    assert training_summary(training)["eval_loss"] == [{"step": 10, "loss": 0.2}]
