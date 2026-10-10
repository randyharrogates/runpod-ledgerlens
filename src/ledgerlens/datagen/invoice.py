"""Varied supplier invoices: items, quantities, prices, tax and numbering differ per document."""

import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from ledgerlens.datagen.business import Business
from ledgerlens.schema import Invoice, InvoiceLine

# Items per industry with a typical SGD unit price; food items may be sold by weight.
CATALOGUE = {
    "food wholesale": [
        ("Jasmine rice 5kg 香米", "18.90", False),
        ("Cooking oil 2L / Minyak masak", "7.40", False),
        ("Chicken thigh (kg) 鸡腿肉", "6.80", True),
        ("Kecap manis 600ml", "3.20", False),
        ("Frozen prawns 1kg / Udang beku", "16.50", False),
        ("Shallots (kg) / Bawang merah", "4.10", True),
        ("Instant noodles carton 40 pcs", "21.00", False),
        ("Palm sugar 1kg / Gula melaka", "5.60", False),
        ("Delivery charge 运费", "35.00", False),
    ],
    "textiles": [
        ("Batik cotton fabric (m)", "12.40", True),
        ("Polyester lining 150cm (m)", "3.85", True),
        ("Thread spool 5000m 缝纫线", "4.20", False),
        ("Songket panel / Kain songket", "95.00", False),
        ("Zips 20cm, pack of 50", "11.50", False),
        ("Dyeing service per kg / Upah celup", "6.30", True),
        ("Buttons assorted 1000 pcs 纽扣", "14.80", False),
        ("Courier / Kurier", "22.00", False),
    ],
    "office supplies": [
        ("A4 paper 80gsm, 5 reams / Kertas", "24.50", False),
        ("Toner cartridge 碳粉盒", "89.00", False),
        ("Ballpoint pens box of 50", "9.90", False),
        ("Lever arch files 文件夹", "2.75", False),
        ("Whiteboard markers set", "6.40", False),
        ("Office chair / Kerusi pejabat", "149.00", False),
        ("Printer servicing / Servis pencetak", "120.00", False),
        ("Delivery charge 运费", "15.00", False),
    ],
}
# Rough unit-price scale against SGD, and the sales tax each country prints on invoices.
SCALE = {"SGD": Decimal(1), "MYR": Decimal("3.4"), "IDR": Decimal(12000)}
TAX = {
    "SG": ("GST", Decimal("0.09")),
    "MY": ("SST", Decimal("0.08")),
    "ID": ("PPN", Decimal("0.11")),
}


def _round(value: Decimal, currency: str) -> Decimal:
    step = Decimal(1) if currency == "IDR" else Decimal("0.01")
    return value.quantize(step, rounding=ROUND_HALF_UP)


def tax_label(country: str) -> str:
    name, rate = TAX[country]
    return f"{name} {rate * 100:.0f}%"


# A name that says what the business sells wins over its random industry (e.g. "... Tekstil").
NAME_INDUSTRY = {
    "Foods": "food wholesale",
    "食品": "food wholesale",
    "Makanan": "food wholesale",
    "Textiles": "textiles",
    "纺织": "textiles",
    "Tekstil": "textiles",
    "Supplies": "office supplies",
    "材料": "office supplies",
    "Bekalan": "office supplies",
}


def industry_of(business: Business) -> str:
    for suffix, industry in NAME_INDUSTRY.items():
        if business.name.endswith(suffix):
            return industry
    return business.industry


def generate_invoice(business: Business, rng: random.Random) -> Invoice:
    """One invoice from this business to one of its customers, with exact totals."""
    currency = business.currency
    items = rng.sample(
        CATALOGUE[industry_of(business)], rng.choice([1, 2, 2, 3, 3, 4, 4, 5, 6, 7])
    )
    lines = []
    for description, base, by_weight in items:
        quantity = (
            Decimal(rng.randint(10, 400)) / 10
            if by_weight
            else Decimal(rng.choice([1, 1, 2, 3, 5, 6, 10, 12, 20, 24, 50]))
        )
        price = Decimal(base) * SCALE[currency] * Decimal(rng.uniform(0.8, 1.3))
        # Prices end in whole cents (whole hundreds of rupiah).
        price = (
            (price / 100).quantize(Decimal(1)) * 100
            if currency == "IDR"
            else (price * 20).quantize(Decimal(1)) / 20
        )
        price = _round(price, currency)
        lines.append(
            InvoiceLine(
                description=description,
                quantity=quantity,
                unit_price=price,
                amount=_round(quantity * price, currency),
            )
        )
    subtotal = sum((line.amount for line in lines), Decimal(0))
    tax = _round(subtotal * TAX[business.country][1], currency)
    issued = date(2026, 1, 1) + timedelta(days=rng.randrange(181))
    serial = rng.randint(1, 9999)
    invoice_number = rng.choice(
        [
            f"INV-{issued:%Y}-{serial:04d}",
            f"{(''.join(c for c in business.name if c.isascii() and c.isalpha())[:3] or 'SI').upper()}"
            f"/{issued:%y%m}/{serial:03d}",
            f"No. {serial:05d}",
        ]
    )
    return Invoice(
        issuer=business.name,
        recipient=rng.choice(business.customers),
        invoice_number=invoice_number,
        date=issued,
        currency=currency,
        line_items=lines,
        subtotal=subtotal,
        tax=tax,
        total=subtotal + tax,
    )
