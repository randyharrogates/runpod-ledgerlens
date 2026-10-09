"""Page statements, retaining the true carry balance despite printed errors."""

from dataclasses import dataclass
from decimal import Decimal

from ledgerlens.datagen.ledger import Ledger
from ledgerlens.schema import Statement


@dataclass
class Page:
    """One extraction target and its provenance and anomaly metadata."""

    id: str
    statement: Statement
    metadata: dict


def paginate(ledger: Ledger, business_id: str, rows_per_page: int = 18) -> list[Page]:
    """Carry true balances forward and deliberately misprint one closing balance."""
    if rows_per_page < 1:
        raise ValueError("rows_per_page must be positive")
    statement = ledger.statement
    pages = []
    opening = statement.opening_balance
    for start in range(0, len(statement.transactions), rows_per_page):
        rows = statement.transactions[start : start + rows_per_page]
        true_closing = rows[-1].balance
        assert true_closing is not None
        anomalies = []
        printed = true_closing
        for label_id, label in enumerate(ledger.labels):
            affected = [
                i
                for i in label["transaction_indices"]
                if start <= i < start + len(rows)
            ]
            if not affected:
                continue
            local = {
                **label,
                "label_id": label_id,
                "transaction_indices": [i - start for i in affected],
                "global_transaction_indices": affected,
            }
            if label["type"] == "unreconciled_page":
                printed += Decimal("123.45")
                local.update(
                    printed_closing_balance=str(printed),
                    true_closing_balance=str(true_closing),
                )
            anomalies.append(local)
        page_statement = statement.model_copy(
            update={
                "opening_balance": opening,
                "closing_balance": printed,
                "transactions": rows,
            },
            deep=True,
        )
        pages.append(
            Page(
                f"{business_id}_p{len(pages) + 1:03d}",
                page_statement,
                {
                    "business_id": business_id,
                    "page_number": len(pages) + 1,
                    "transaction_offset": start,
                    "true_closing_balance": str(true_closing),
                    "printed_closing_balance": str(printed),
                    "anomalies": anomalies,
                },
            )
        )
        opening = true_closing
    return pages
