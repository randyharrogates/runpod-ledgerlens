"""The model reads digits better than direction; the printed running balance decides.
Move debit/credit only when the balance change matches the amount exactly.
"""

from ledgerlens.schema import Statement


def check_direction(statement: Statement) -> tuple[Statement, list[dict]]:
    checked = statement.model_copy(deep=True)
    corrections = []
    for i, row in enumerate(statement.transactions):
        previous = (
            statement.opening_balance
            if i == 0
            else statement.transactions[i - 1].balance
        )
        if previous is None or row.balance is None:
            continue
        if (row.debit is None) == (row.credit is None):
            continue
        source = "debit" if row.debit is not None else "credit"
        amount = getattr(row, source)
        delta = row.balance - previous
        if delta != (amount if source == "debit" else -amount):
            continue
        destination = "credit" if source == "debit" else "debit"
        setattr(checked.transactions[i], source, None)
        setattr(checked.transactions[i], destination, amount)
        corrections.append(
            {"row": i, "from": source, "to": destination, "amount": str(amount)}
        )
    return checked, corrections
