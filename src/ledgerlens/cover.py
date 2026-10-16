"""Code-owned illustration prompts derived from document categories."""

import re
import unicodedata
from collections import Counter

CLAUSE = "No people, no faces, no text, no logos, no documents, no numbers."
TRADES = {
    "textiles": (
        "fabric batik songket textile tekstil kain yarn thread button zip dye garment 布 纽扣",
        "a shop interior with shelves of folded fabric and bolts of batik cloth",
    ),
    "food wholesale": (
        "rice beras sugar gula oil minyak flour tepung noodle coffee kopi egg telur chicken ayam makanan prawn udang shallot bawang frozen 米 糖 食品",
        "a tidy storeroom with sacks of rice and sealed food containers",
    ),
    "office supplies": (
        "paper kertas toner ink pen stapler folder envelope printer 纸",
        "shelves of stationery and neatly stacked paper supplies",
    ),
    "building supplies": (
        "cement concrete timber plywood brick screw hammer pipe",
        "a tidy hardware shop with timber and building materials",
    ),
    "electronics": (
        "circuit capacitor resistor cable sensor semiconductor",
        "shelves of electronic components and a tidy workbench",
    ),
    "printing": (
        "brochure leaflet lithograph offset binding",
        "a tidy print workshop with blank paper and printing equipment",
    ),
    "logistics": (
        "freight shipping courier haulage delivery trucking",
        "a small dispatch warehouse with parcels and loading equipment",
    ),
    "café": (
        "espresso latte cappuccino pastry croissant barista",
        "a quiet café interior with ceramic cups and an espresso machine",
    ),
}
COUNTRIES = dict(
    SGD="Singapore",
    MYR="Malaysia",
    IDR="Indonesia",
    THB="Thailand",
    PHP="the Philippines",
    VND="Vietnam",
)


def cover_prompt(documents: list[dict]) -> dict:
    scores = Counter()
    hits = {}
    for industry, (words, _) in TRADES.items():
        matched = Counter()
        for doc in documents:
            statement = doc.get("doc_type") == "statement"
            rows = doc.get("transactions" if statement else "line_items") or []
            for row in rows:
                description = str(row.get("description", "")).lower()
                for word in words.split():
                    pattern = (
                        re.escape(word)
                        if not word.isascii()
                        else rf"(?<!\w){word}(?:s|es)?(?!\w)"
                    )
                    matched[word] += len(re.findall(pattern, description)) * (
                        1 if statement else 3
                    )
        scores[industry] = sum(matched.values())
        hits[industry] = matched
    winners = [
        name
        for name, score in scores.items()
        if score == max(scores.values()) and score
    ]
    industry = winners[0] if len(winners) == 1 else "general trading"
    currencies = Counter(
        doc.get("currency") for doc in documents if doc.get("currency")
    )
    country = COUNTRIES.get(
        currencies.most_common(1)[0][0] if currencies else None, "Southeast Asia"
    )
    scene = (
        TRADES[industry][1]
        if industry in TRADES
        else "a tidy small warehouse with shelves of boxed goods"
    )
    return {
        "prompt": f"Editorial photograph of a small {industry} business in {country}: {scene}. Natural light, realistic, calm, wide shot. No people, no faces, no text, no signage, no logos, no documents, no numbers.",
        "industry": industry,
        "country": country,
        "basis": [
            word
            for word, count in hits.get(industry, Counter()).most_common(3)
            if count
        ],
    }


def user_prompt(text: str) -> str:
    text = "".join(
        c for c in text if not unicodedata.category(c).startswith("C") or c.isspace()
    )
    text = " ".join(text.split()).replace(CLAUSE, "").strip()[:400].strip()
    if not text:
        raise ValueError("Prompt must contain text")
    return text + " " + CLAUSE
