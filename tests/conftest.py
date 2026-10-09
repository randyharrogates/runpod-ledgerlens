"""Shared deterministic fixtures."""

import pytest

from ledgerlens.datagen.business import generate_businesses
from ledgerlens.datagen.ledger import generate_ledger
from ledgerlens.datagen.paginate import paginate


@pytest.fixture
def ledger():
    """Return one reproducible six-month business ledger."""
    return generate_ledger(generate_businesses(1, 42)[0], 42)


@pytest.fixture
def pages(ledger):
    """Return ledger pages with one deliberately incorrect printed balance."""
    return paginate(ledger, "biz_0000")
