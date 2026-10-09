"""Seeded fictional SME profiles and multilingual counterparties."""

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Business:
    """Identity, operating scale and recurring payment schedule."""

    id: str
    name: str
    industry: str
    uen: str
    country: str
    currency: str
    customers: tuple[str, ...]
    suppliers: tuple[str, ...]
    director: str
    size_class: str
    revenue_scale: float
    payday: int
    rent_day: int
    supplier_cadence: int
    layout_id: str


def generate_businesses(n: int, seed: int) -> list[Business]:
    """Generate unique names from word pools without index-bearing identities."""
    if n < 1:
        raise ValueError("n must be positive")
    rng = random.Random(seed)
    used: set[str] = set()
    pools = [
        (
            "Amber Willow Cedar Moss Coral Silver Moon Fern Jade Dawn".split(),
            "Brook Meadow Grove Vale Ridge Cove Petal Reed Cloud Pine".split(),
            "Trading Supplies Foods Textiles Services Commerce".split(),
        ),
        (
            "青 云 星 金 银 春 秋 玉 海 竹".split(),
            "桥 林 叶 山 河 花 光 月 泉 峰".split(),
            "商贸 材料 食品 纺织 服务 供应".split(),
        ),
        (
            "Awan Rimba Senja Bintang Pelangi Bukit Seri Bayu Mutiara Cahaya".split(),
            "Indah Jaya Murni Makmur Damai Hijau Emas Utama Sentosa Permai".split(),
            "Niaga Bekalan Dagang Tekstil Makanan Usaha".split(),
        ),
    ]

    def name() -> str:
        while True:
            pool = rng.choice(pools)
            middle = rng.sample(pool[1], 2)
            words = [rng.choice(pool[0]), *middle, rng.choice(pool[2])]
            value = ("" if pool is pools[1] else " ").join(words)
            if value.casefold() not in used:
                used.add(value.casefold())
                return value

    businesses = []
    for i in range(n):
        country, currency = rng.choice([("SG", "SGD"), ("MY", "MYR"), ("ID", "IDR")])
        size = rng.choice(["micro", "small", "medium"])
        low, high = {
            "micro": (3000, 15000),
            "small": (15000, 75000),
            "medium": (75000, 350000),
        }[size]
        businesses.append(
            Business(
                id=f"biz_{i:04d}",
                name=name(),
                industry=rng.choice(["food wholesale", "textiles", "office supplies"]),
                uen=f"2026{i:05d}{chr(65 + rng.randrange(26))}",
                country=country,
                currency=currency,
                customers=tuple(name() for _ in range(rng.randint(2, 8))),
                suppliers=tuple(name() for _ in range(rng.randint(2, 6))),
                director=rng.choice(["Lim", "Tan", "Wong", "Nur", "Aiman"])
                + " "
                + " ".join(
                    rng.sample(
                        "Mei Ling Wei Jun Hana Farah Amir Zaki Rui Lian".split(), 3
                    )
                ),
                size_class=size,
                revenue_scale=10 ** rng.uniform(math.log10(low), math.log10(high)),
                payday=rng.randint(18, 28),
                rent_day=rng.randint(1, 15),
                supplier_cadence=rng.randint(7, 15),
                layout_id=rng.choice(
                    ["stmt_a", "stmt_b", "stmt_c", "stmt_d", "stmt_e", "stmt_f"]
                ),
            )
        )
    return businesses
