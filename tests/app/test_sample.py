"""The demo selection remains representative without needing a browser in tests."""

import json

import pytest

from app import make_sample
from ledgerlens.analytics import analyze
from ledgerlens.datagen.business import generate_businesses
from ledgerlens.datagen.layouts import LAYOUTS
from ledgerlens.datagen.ledger import generate_ledger
from ledgerlens.datagen.paginate import paginate
from ledgerlens.datagen.render import RenderError


def test_sample_selection_is_consecutive_sgd_and_contains_complete_anomalies():
    business = generate_businesses(1, make_sample.SEED)[0]
    assert business.currency == "SGD"
    assert not LAYOUTS[business.layout_id].held_out
    pages = paginate(generate_ledger(business, make_sample.SEED), business.id)
    selected = pages[:4]
    assert [p.id for p in selected] == make_sample.STATEMENTS
    findings = analyze([p.statement for p in selected])["findings"]
    assert {"returned_cheque", "round_trip", "large_cash_deposit"} <= {
        finding["type"] for finding in findings
    }


def test_sample_render_failure_is_clear_and_preserves_previous_sample(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(make_sample, "OUT", tmp_path)
    previous = tmp_path / "manifest.json"
    previous.write_text(json.dumps(["previous"]))

    def unavailable(*args):
        raise RenderError("Chromium unavailable")

    monkeypatch.setattr(make_sample, "generate_dataset", unavailable)
    with pytest.raises(SystemExit, match="requires working headless Chromium"):
        make_sample.main()
    assert json.loads(previous.read_text()) == ["previous"]
