"""Construction invariants, layout holdouts and deterministic degradation."""

import json
from decimal import Decimal

import pytest
from PIL import Image

from ledgerlens.datagen.business import generate_businesses
from ledgerlens.datagen.cli import generate_dataset
from ledgerlens.datagen.degrade import degrade
from ledgerlens.datagen.layouts import LAYOUTS, render_html
from ledgerlens.datagen.ledger import generate_ledger
from ledgerlens.datagen.paginate import paginate
from ledgerlens.datagen.render import RenderError, find_chromium, render_png

ANOMALIES = {
    "returned_cheque",
    "round_trip",
    "large_cash_deposit",
    "customer_concentration",
    "unreconciled_page",
}


@pytest.mark.parametrize("seed", [0, 7, 42, 100])
def test_running_balances_and_labels(seed):
    business = generate_businesses(1, seed)[0]
    ledger = generate_ledger(business, seed)
    balance = ledger.statement.opening_balance
    for row in ledger.statement.transactions:
        balance += (row.credit or Decimal(0)) - (row.debit or Decimal(0))
        assert row.balance == balance
    assert ledger.statement.closing_balance == balance
    assert {row.date.month for row in ledger.statement.transactions} == set(range(1, 7))
    assert {label["type"] for label in ledger.labels} <= ANOMALIES
    for label in ledger.labels:
        assert label["transaction_indices"]
        assert all(
            0 <= index < len(ledger.statement.transactions)
            for index in label["transaction_indices"]
        )
    assert generate_businesses(1, seed) == generate_businesses(1, seed)
    assert generate_ledger(business, seed) == ledger
    inflows = sum(
        (row.credit or Decimal(0) for row in ledger.statement.transactions), Decimal(0)
    )
    for label in ledger.labels:
        if label["type"] == "customer_concentration":
            dominant = sum(
                (
                    ledger.statement.transactions[i].credit
                    for i in label["transaction_indices"]
                ),
                Decimal(0),
            )
            assert dominant / inflows > Decimal("0.5")


def test_pagination_carries_true_balance(pages, ledger):
    opening = ledger.statement.opening_balance
    incorrect = []
    for page in pages:
        assert page.statement.opening_balance == opening
        opening = page.statement.transactions[-1].balance
        assert Decimal(page.metadata["true_closing_balance"]) == opening
        if page.statement.closing_balance != opening:
            incorrect.append(page)
            label = next(
                label
                for label in page.metadata["anomalies"]
                if label["type"] == "unreconciled_page"
            )
            assert (
                Decimal(label["printed_closing_balance"])
                == page.statement.closing_balance
            )
            assert Decimal(label["true_closing_balance"]) == opening
    assert len(incorrect) == sum(
        label["type"] == "unreconciled_page" for label in ledger.labels
    )
    assert opening == ledger.statement.closing_balance
    assert [
        row for page in pages for row in page.statement.transactions
    ] == ledger.statement.transactions
    with pytest.raises(ValueError):
        paginate(ledger, "biz", 0)


@pytest.mark.parametrize("count", [1, 2, 3, 12])
def test_splits_and_html(tmp_path, count):
    manifest = generate_dataset(tmp_path, count, 7, no_render=True)
    business_sets = []
    for split in ("train", "val", "test"):
        identities = set()
        for page_id in manifest[split]:
            info = manifest["pages"][page_id]
            identities.add(info["business_id"])
            if info["layout_id"] in ("stmt_e", "stmt_f"):
                assert split == "test"
            assert (tmp_path / f"{page_id}.html").exists()
            assert (tmp_path / f"{page_id}.json").exists()
            metadata = json.loads((tmp_path / f"{page_id}.meta.json").read_text())
            assert metadata["rendered"] is False
            document = json.loads((tmp_path / f"{page_id}.json").read_text())
            if document["currency"] == "IDR" and info["layout_id"].startswith(
                "invoice"
            ):
                for field in ("subtotal", "tax", "total"):
                    assert Decimal(document[field]) % 1 == 0
                for line in document["line_items"]:
                    assert Decimal(line["unit_price"]) % 1 == 0
                    assert Decimal(line["amount"]) % 1 == 0
        business_sets.append(identities)
    assert not business_sets[0] & business_sets[1]
    assert not business_sets[0] & business_sets[2]
    assert not business_sets[1] & business_sets[2]

    assert not list(tmp_path.glob("*.png"))
    with pytest.raises(ValueError, match="already contains"):
        generate_dataset(tmp_path, count, 7, no_render=True)


def test_six_distinct_layouts_and_escape(ledger):
    statement = ledger.statement.model_copy(deep=True)
    statement.account_holder = '<script>alert("x")</script>'
    htmls = [render_html(statement, layout_id) for layout_id in LAYOUTS]
    assert len(set(htmls)) == 6
    for html in htmls:
        assert "&lt;script&gt;" in html
        assert "PingFang SC" in html
        assert "Balance carried forward" in html


@pytest.mark.parametrize("strength", ["clean", "light", "medium", "heavy"])
def test_degradation_deterministic(strength):
    image = Image.new("RGB", (240, 320), "white")
    first, metadata = degrade(image, strength, 17)
    second, other = degrade(image, strength, 17)
    assert first.tobytes() == second.tobytes()
    assert metadata == other
    assert image.size == (240, 320)
    if strength == "clean":
        assert not metadata["operations"]
        assert first.tobytes() == image.tobytes()
    else:
        assert len(metadata["operations"]) == 7
        changed, _ = degrade(image, strength, 18)
        assert first.tobytes() != changed.tobytes()


def test_renderer_when_available(tmp_path):
    if find_chromium() is None:
        pytest.skip("No local Chromium binary")
    html = tmp_path / "page.html"
    html.write_text("<html><body>Offline rendering test</body></html>")
    image_path = tmp_path / "page.png"
    try:
        render_png(html, image_path)
    except RenderError as error:
        pytest.skip(str(error))
    with Image.open(image_path) as image:
        assert image.size == (1240, 1754)


def test_renderer_missing_binary(tmp_path, monkeypatch):
    monkeypatch.setattr("ledgerlens.datagen.render.find_chromium", lambda: None)
    with pytest.raises(RenderError, match="No local Chromium"):
        render_png(tmp_path / "page.html", tmp_path / "page.png")


def test_diversity_and_gold_precision():
    from collections import Counter

    from ledgerlens.analytics import detect_anomalies
    from ledgerlens.eval.score import anomaly_recall

    businesses = generate_businesses(30, 7)
    sequences = set()
    cents = total = clean = diversified = 0
    event_values = {kind: set() for kind in ANOMALIES}
    observed = Counter()
    for i, business in enumerate(businesses):
        ledger = generate_ledger(business, 7 + i * 101)
        rows = ledger.statement.transactions
        sequences.add(tuple((row.debit, row.credit) for row in rows))
        for name in (
            business.name,
            business.director,
            *business.customers,
            *business.suppliers,
        ):
            assert not any(char.isdigit() for char in name)
            assert "fictional" not in name.casefold()
            assert "related" not in name.casefold()
        for row in rows:
            assert "related" not in row.description.casefold()
            assert "fictional" not in row.description.casefold()
            amount = row.credit or row.debit
            total += 1
            cents += amount % 1 != 0
        counts = Counter(row.date.month for row in rows)
        assert all(12 <= count <= 45 for count in counts.values())
        clean += not ledger.labels
        diversified += not any(
            label["type"] == "customer_concentration" for label in ledger.labels
        )
        pages = paginate(ledger, business.id, 8)
        ids = [page.id for page in pages]
        labels = []
        for label in ledger.labels:
            observed[label["type"]] += 1
            indices = label["transaction_indices"]
            event_values[label["type"]].add(
                tuple((rows[j].date, rows[j].debit, rows[j].credit) for j in indices)
            )
            labels.append(
                {
                    "type": label["type"],
                    "citations": [
                        {"page_id": ids[j // 8], "row_index": j % 8} for j in indices
                    ],
                }
            )
        report = anomaly_recall([page.statement for page in pages], ids, labels)
        assert report["recall"] == report["precision"] == 1, (business.id, report)
        assert {f["type"] for f in detect_anomalies([p.statement for p in pages])} == {
            label["type"] for label in ledger.labels
        }
        balance = ledger.statement.opening_balance
        for page in pages:
            assert page.statement.opening_balance == balance
            balance = page.statement.transactions[-1].balance
    assert len(sequences) == 30
    assert cents / total > 0.6
    assert clean and diversified
    assert set(observed) == ANOMALIES
    assert all(len(values) > 1 for values in event_values.values())


def test_business_layout_and_name_isolation(tmp_path):
    manifest = generate_dataset(tmp_path, 30, 7, no_render=True)
    businesses = generate_businesses(30, 7)
    names = {split: set() for split in ("train", "val", "test")}
    for business in businesses:
        split = manifest["business_splits"][business.id]
        names[split].update((business.name, *business.customers, *business.suppliers))
        bank_names = set()
        layouts = set()
        for page_id, info in manifest["pages"].items():
            if info["business_id"] == business.id and info["layout_id"].startswith(
                "stmt_"
            ):
                layouts.add(info["layout_id"])
                bank_names.add(
                    json.loads((tmp_path / f"{page_id}.json").read_text())["bank_name"]
                )
        assert layouts == {business.layout_id}
        assert len(bank_names) == 1
        assert all("Fictional" not in bank for bank in bank_names)
        if business.layout_id in ("stmt_e", "stmt_f"):
            assert split == "test"
            assert manifest["business_test_sets"][business.id] == "test_unseen_layout"
        elif split == "test":
            assert manifest["business_test_sets"][business.id] == "test_seen_layout"
    assert not (
        names["train"] & names["val"]
        or names["train"] & names["test"]
        or names["val"] & names["test"]
    )


@pytest.mark.parametrize("seed", [0, 7, 42])
def test_country_amounts_rails_and_names(seed):
    import re

    from ledgerlens.analytics import payer

    rails = {
        "SG": {"PayNow", "FAST", "GIRO"},
        "MY": {"DuitNow", "IBG", "JomPAY"},
        "ID": {"BI-FAST", "SKN", "RTGS"},
    }
    observed_countries = set()
    observed_rails = {country: set() for country in rails}
    fractional_currencies = set()
    cjk_names = 0
    for i, business in enumerate(generate_businesses(60, seed)):
        observed_countries.add(business.country)
        for name in (
            business.name,
            business.director,
            *business.customers,
            *business.suppliers,
        ):
            words = name.casefold().split()
            assert all(a != b for a, b in zip(words, words[1:]))
            if re.search(r"[\u4e00-\u9fff]", name):
                cjk_names += 1
                assert not re.search(r"[\u4e00-\u9fff]\s+[\u4e00-\u9fff]", name)
                assert " " not in name
        ledger = generate_ledger(business, seed + i * 101)
        for row in ledger.statement.transactions:
            directions = re.findall(r"\b(FROM|TO)\b", row.description)
            if directions:
                assert directions == ["FROM" if row.credit else "TO"]
                rail = row.description.split()[0]
                assert rail in rails[business.country]
                observed_rails[business.country].add(rail)
                expected = row.description.split(f" {directions[0]} ", 1)[1]
                assert (
                    payer(row.description) == expected.rsplit(" REF ", 1)[0].casefold()
                )
            for rail in set.union(*rails.values()):
                if rail in row.description.split():
                    assert rail in rails[business.country]
            if (row.credit or row.debit) % 1:
                fractional_currencies.add(business.currency)
        for page in paginate(ledger, business.id):
            if business.currency == "IDR":
                amounts = [
                    page.statement.opening_balance,
                    page.statement.closing_balance,
                ]
                for row in page.statement.transactions:
                    amounts.extend([row.debit, row.credit, row.balance])
                assert all(value % 1 == 0 for value in amounts if value is not None)
    assert observed_countries == set(rails)
    assert observed_rails == rails
    assert fractional_currencies == {"SGD", "MYR"}
    assert cjk_names


@pytest.mark.parametrize(
    ("currency", "heading"),
    [
        ("SGD", "STATEMENT OF ACCOUNT"),
        ("MYR", "PENYATA AKAUN / STATEMENT"),
        ("IDR", "MUTASI REKENING"),
    ],
)
def test_statement_heading_by_country(ledger, currency, heading):
    statement = ledger.statement.model_copy(update={"currency": currency})
    for layout_id in LAYOUTS:
        html = render_html(statement, layout_id)
        assert heading in html
        assert "Fictional synthetic document" in html


def test_invoices_vary_and_add_up():
    import random
    from decimal import ROUND_HALF_UP, Decimal

    from ledgerlens.datagen.invoice import TAX, generate_invoice

    businesses = generate_businesses(30, 7)
    rng = random.Random(1)
    invoices = [generate_invoice(b, rng) for b in businesses for _ in range(2)]
    # Not a template: totals, line counts and dates differ between invoices.
    assert len({i.total for i in invoices}) > 50
    assert len({len(i.line_items) for i in invoices}) > 3
    assert len({i.date for i in invoices}) > 30
    for invoice, business in zip(invoices, [b for b in businesses for _ in range(2)]):
        step = Decimal(1) if invoice.currency == "IDR" else Decimal("0.01")
        for line in invoice.line_items:
            assert line.amount == (line.quantity * line.unit_price).quantize(
                step, rounding=ROUND_HALF_UP
            )
        assert invoice.subtotal == sum(line.amount for line in invoice.line_items)
        rate = TAX[business.country][1]
        assert abs(invoice.tax - invoice.subtotal * rate) <= step / 2
        assert invoice.total == invoice.subtotal + invoice.tax
        if invoice.currency == "IDR":
            assert all(line.unit_price % 100 == 0 for line in invoice.line_items)


def test_invoice_items_match_the_business_name():
    import random

    from ledgerlens.datagen.invoice import CATALOGUE, generate_invoice

    for business in generate_businesses(60, 3):
        invoice = generate_invoice(business, random.Random(0))
        names = {name for name, _, _ in CATALOGUE["textiles"]}
        if business.name.endswith(("Tekstil", "Textiles", "纺织")):
            assert all(line.description in names for line in invoice.line_items)
        assert invoice.invoice_number.isascii()
