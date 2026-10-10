"""Variable six-month cash flows with optional, data-backed anomaly labels."""

import calendar
import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from ledgerlens.analytics import payer_key, top_payer_concentration
from ledgerlens.datagen.business import Business
from ledgerlens.datagen.layouts import LAYOUTS
from ledgerlens.schema import Statement, Transaction


@dataclass
class Ledger:
    """Full statement and labels indexed into ordered transactions."""

    statement: Statement
    labels: list[dict]


def generate_ledger(business: Business, seed: int, year: int = 2026) -> Ledger:
    """Sample schedules and independent events, with about a quarter clean."""
    rng = random.Random(seed)
    scale = business.revenue_scale * (1000 if business.currency == "IDR" else 1)
    event_rng = random.Random(f"{seed}:anomalies:{business.id}")
    active = event_rng.random() >= 0.25
    selected = {
        kind
        for kind in (
            "returned_cheque",
            "round_trip",
            "large_cash_deposit",
            "customer_concentration",
            "unreconciled_page",
        )
        if active and event_rng.random() < 0.55
    }
    rows: list[Transaction] = []
    events: list[tuple[str, list[Transaction]]] = []

    def money(value: float) -> Decimal:
        return Decimal(str(value)).quantize(
            Decimal("1") if business.currency == "IDR" else Decimal("0.01")
        )

    def add(day, description, debit=None, credit=None):
        row = Transaction(
            date=day,
            description=description,
            debit=money(debit) if debit else None,
            credit=money(credit) if credit else None,
        )
        rows.append(row)
        return row

    rails = {
        "SG": ("PayNow", "FAST", "GIRO"),
        "MY": ("DuitNow", "IBG", "JomPAY"),
        "ID": ("BI-FAST", "SKN", "RTGS"),
    }[business.country]

    def description(name, *, incoming=False):
        ref = rng.randrange(100000, 999999)
        direction = "FROM" if incoming else "TO"
        return f"{rng.choice(rails)} {direction} {name} REF {ref}"

    def random_date():
        return date(year, 1, 1) + timedelta(days=rng.randrange(2, 170))

    for month in range(1, 7):
        last = calendar.monthrange(year, month)[1]

        def day():
            return date(year, month, rng.randint(1, last))

        count = rng.randint(max(4, len(business.customers)), 20)
        unit = scale * rng.uniform(0.85, 1.2) / count
        for j in range(count):
            # Balanced rotation prevents accidental concentration in negative examples.
            customer = business.customers[j % len(business.customers)]
            if "customer_concentration" in selected:
                customer = business.customers[0] if j % 5 else business.customers[-1]
            add(
                day(),
                description(customer, incoming=True),
                credit=unit * rng.uniform(0.85, 1.15),
            )
        for j, payment_day in enumerate(
            range(rng.randint(1, 7), last + 1, business.supplier_cadence)
        ):
            add(
                date(year, month, payment_day),
                description(business.suppliers[j % len(business.suppliers)]),
                debit=scale * rng.uniform(0.06, 0.13),
            )
        add(
            date(year, month, business.payday),
            f"PAYROLL {rails[-1]} Gaji",
            debit=scale * rng.uniform(0.15, 0.24),
        )
        add(
            date(year, month, business.rent_day),
            "RENT Sewa 租金",
            debit=scale * rng.uniform(0.04, 0.08),
        )
        for text, fraction in [
            ("GST TAX Cukai", 0.025),
            ("LOAN REPAYMENT", 0.04),
            ("BANK FEES", 0.001),
            ("ATM WITHDRAWAL", 0.004),
            ("FX CONVERSION USD", 0.008),
            ("CUSTOMER REFUND", 0.003),
        ]:
            add(day(), text, debit=scale * fraction * rng.uniform(0.7, 1.3))
        add(
            day(),
            "CARD SETTLEMENT BATCH " + str(rng.randrange(10000, 99999)),
            credit=unit * rng.uniform(0.3, 0.6),
        )
        add(day(), "INTEREST CREDIT", credit=scale * rng.uniform(0.0001, 0.0003))
    if "returned_cheque" in selected:
        ref = rng.randrange(100000, 999999)
        text = rng.choice(
            [
                f"RTN CHQ {ref}",
                "CHQ RETURNED - INSUFFICIENT FUNDS",
                f"退票 支票 {ref}",
                f"CEK DIKEMBALIKAN {ref}",
                "RETURNED CHEQUE",
            ]
        )
        when, amount = random_date(), scale * rng.uniform(0.01, 0.08)
        if rng.random() < 0.5:
            add(when - timedelta(days=2), f"CHQ DEPOSIT {ref}", credit=amount)
        events.append(("returned_cheque", [add(when, text, debit=amount)]))
    if "round_trip" in selected:
        when, amount = random_date(), money(scale * rng.uniform(0.02, 0.15))
        name = rng.choice(
            [business.director, business.name.rsplit(" ", 1)[0] + " Holdings"]
        )
        outgoing = add(when, description(name), debit=amount)
        incoming = add(
            when + timedelta(days=rng.randint(3, 10)),
            description(name, incoming=True),
            credit=money(amount * Decimal(str(rng.uniform(0.9901, 1)))),
        )
        events.append(("round_trip", [outgoing, incoming]))
    if "large_cash_deposit" in selected:
        reference = median(row.credit for row in rows if row.credit)
        amount = money(reference * Decimal(str(rng.uniform(3.2, 8))))
        events.append(
            (
                "large_cash_deposit",
                [
                    add(
                        random_date(),
                        rng.choice(
                            [
                                "CASH DEPOSIT",
                                "TUNAI MASUK",
                                "现金存款",
                                "CASH LODGEMENT",
                            ]
                        ),
                        credit=amount,
                    )
                ],
            )
        )
    rows.sort(key=lambda row: row.date)
    balance = opening = money(scale * rng.uniform(0.5, 2))
    for row in rows:
        balance += (row.credit or Decimal(0)) - (row.debit or Decimal(0))
        row.balance = balance
    statement = Statement(
        bank_name=LAYOUTS[business.layout_id].bank_name,
        account_holder=business.name,
        account_number_masked=f"****{rng.randrange(10000):04d}",
        period_start=date(year, 1, 1),
        period_end=date(year, 6, 30),
        currency=business.currency,
        opening_balance=opening,
        closing_balance=balance,
        transactions=rows,
    )
    concentration = top_payer_concentration([statement])
    if concentration["share"] > Decimal(".5"):
        events.append(
            (
                "customer_concentration",
                [
                    row
                    for row in rows
                    if row.credit
                    and payer_key(row.description) == concentration["payer"].casefold()
                ],
            )
        )
    if "unreconciled_page" in selected:
        events.append(("unreconciled_page", [rng.choice(rows)]))
    indices = {id(row): i for i, row in enumerate(rows)}
    labels = [
        {"type": kind, "transaction_indices": [indices[id(row)] for row in affected]}
        for kind, affected in events
    ]
    return Ledger(statement, labels)
