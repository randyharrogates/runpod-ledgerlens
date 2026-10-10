"""Compact evaluation summaries exclude per-page data."""

import json

from train.report import make_summary, summarise


def test_summary_fields_and_rounding(tmp_path):
    result = {
        "header_accuracy": 0.123456,
        "row_f1": 0.765432,
        "rows_direction_checked": {"f1": 0.876543, "pages": ["large"]},
        "page_reconciliation_rate": 0.333333,
        "valid_json_rate": 1.0,
        "anomaly_precision": None,
        "anomaly_recall": None,
        "cost_per_1000_pages": 0.234567,
        "price_per_hour": 0.72,
        "timing": {
            "pages": 3,
            "seconds_per_page": 1.234567,
            "gpu_name": "Test GPU",
            "load_seconds": 20,
        },
        "pages": {"large": "data"},
        "worst_pages": ["large"],
    }
    results = {"test_cord": {"4b": result}, "empty": {"status": "empty_set"}}
    summary = summarise(results)
    assert summary["generated_from"] == "batch evaluation, one GPU Pod"
    assert summary["split_labels"]["test_cord"] == "Real receipts (CORD v2 test)"
    assert summary["splits"]["empty"] == {"status": "empty_set"}
    assert summary["splits"]["test_cord"]["4b"] == {
        "pages": 3,
        "header_accuracy": 0.1235,
        "row_f1": 0.7654,
        "row_f1_checked": 0.8765,
        "reconciliation": 0.3333,
        "valid_json": 1.0,
        "anomaly_precision": None,
        "anomaly_recall": None,
        "seconds_per_page": 1.2346,
        "cost_per_1000_pages": 0.2346,
        "price_per_hour": 0.72,
        "gpu_name": "Test GPU",
    }
    source = tmp_path / "results.json"
    source.write_text(json.dumps(results))
    out = tmp_path / "nested" / "summary.json"
    assert make_summary(source, out) == summary
    assert out.read_text() == json.dumps(summary, indent=2, sort_keys=True) + "\n"
    assert result["header_accuracy"] == 0.123456
