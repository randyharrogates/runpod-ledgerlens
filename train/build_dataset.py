"""Build leakage-safe JSONL manifests from synthetic pages and optional CORD v2."""

import argparse
import json
import logging
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from train.common import SETS, canonical_target

LOG = logging.getLogger(__name__)


def cord_number(value, *, quantity=False) -> Decimal | None:
    if value is None:
        return None
    text = re.sub(r"(?i)rp\.?|idr|sgd|myr|usd|rm|[@$€£¥\s*]", "", str(value))
    # A printed dash means "nothing here", not zero.
    if not text or set(text) <= {"-"}:
        return None
    if quantity:
        # Quantities print as "2", "2.00", "x2", "1Prs", "4 Pcs": keep the leading number.
        match = re.match(r"x?(\d+(?:[.,]\d+)?)", text, re.IGNORECASE)
        if not match:
            raise ValueError(f"Unparseable CORD number: {value!r}")
        return Decimal(match.group(1).replace(",", "."))
    # Discounts print as "-60.000" or "(60.000)"; parse the digits, then restore the sign.
    sign = ""
    if text.startswith("(") and text.endswith(")"):
        sign, text = "-", text[1:-1]
    elif text[0] in "+-":
        sign, text = text[0].replace("+", ""), text[1:]
    text = text.rstrip(".,")
    if "." in text and "," in text:
        # Both separators: the last one is the decimal point ("181,500.00", "8.500,00").
        decimal = "." if text.rfind(".") > text.rfind(",") else ","
        grouping = "," if decimal == "." else "."
        text = text.replace(grouping, "").replace(decimal, ".")
    elif re.fullmatch(r"\d{1,3}([.,]\d{3})+", text):
        # Rupiah has no cents in CORD; a lone . or , before three digits groups thousands.
        text = text.replace(".", "").replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        number = Decimal(sign + text)
    except InvalidOperation as error:
        raise ValueError(f"Unparseable CORD number: {value!r}") from error
    if not number.is_finite():
        raise ValueError(f"Non-finite CORD number: {value!r}")
    return number


def cord_receipt(ground_truth: str | dict, *, stats: Counter | None = None) -> dict:
    """Map printed fields only, flattening add-ons in their printed order."""
    stats = stats if stats is not None else Counter()
    data = json.loads(ground_truth) if isinstance(ground_truth, str) else ground_truth
    parsed = data["gt_parse"]
    items = []

    def add_items(menu):
        if isinstance(menu, dict):
            menu = [menu]
        for item in menu or []:
            if not isinstance(item, dict):
                stats["items_skipped"] += 1
                continue
            name = item.get("nm")
            if not isinstance(name, str) or not name.strip():
                stats["items_skipped"] += 1
            else:
                items.append(
                    {
                        "description": name,
                        "quantity": cord_number(item.get("cnt"), quantity=True),
                        "unit_price": cord_number(item.get("unitprice")),
                        "amount": cord_number(item.get("price")),
                    }
                )
            add_items(item.get("sub", []))

    def first_dict(value):
        if isinstance(value, list):
            return next((entry for entry in value if isinstance(entry, dict)), {})
        return value or {}

    add_items(parsed.get("menu", []))
    if not items:
        raise ValueError("No receipt items")
    subtotal = first_dict(parsed.get("sub_total"))
    total = first_dict(parsed.get("total"))
    return {
        "doc_type": "receipt",
        "menu_items": items,
        "subtotal": cord_number(subtotal.get("subtotal_price")),
        "tax": cord_number(subtotal.get("tax_price")),
        "total": cord_number(total.get("total_price")),
    }


def build_dataset(
    synth: Path, out: Path, cord: bool = False, max_cord_train: int | None = None
):
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((synth / "splits.json").read_text())
    splits = {name: [] for name in SETS}
    businesses = {}
    for source in ("train", "val", "test"):
        for page_id in manifest[source]:
            metadata_path = synth / f"{page_id}.meta.json"
            metadata = json.loads(metadata_path.read_text())
            business = metadata["business_id"]
            if business in businesses and businesses[business] != source:
                raise ValueError(f"Business leakage: {business}")
            businesses[business] = source
            split = (
                manifest["business_test_sets"][business] if source == "test" else source
            )
            image = (synth / f"{page_id}.png").resolve()
            if not image.is_file():
                raise FileNotFoundError(image)
            target = canonical_target((synth / f"{page_id}.json").read_text())
            splits[split].append(
                {
                    "id": page_id,
                    "doc_type": json.loads(target)["doc_type"],
                    "image": str(image),
                    "target": target,
                }
            )
    if cord:
        from datasets import load_dataset

        image_dir = out / "cord_images"
        image_dir.mkdir(exist_ok=True)
        for source, destination in (
            ("train", "train"),
            ("validation", "val"),
            ("test", "test_cord"),
        ):
            dataset = load_dataset("naver-clova-ix/cord-v2", split=source)
            accepted = 0
            failures = Counter()
            stats = Counter()
            missing = Counter(
                dict.fromkeys(
                    ("quantity", "unit_price", "amount", "subtotal", "tax", "total"), 0
                )
            )
            for index, example in enumerate(dataset):
                if (
                    source == "train"
                    and max_cord_train is not None
                    and accepted >= max_cord_train
                ):
                    break
                try:
                    receipt = cord_receipt(example["ground_truth"], stats=stats)
                    target = canonical_target(receipt)
                except (ValueError, TypeError, KeyError, ArithmeticError) as error:
                    failures[f"{type(error).__name__}: {error}"] += 1
                    LOG.warning("Skipping CORD %s/%s: %s", source, index, error)
                    continue
                for field in missing:
                    absent = (
                        any(item[field] is None for item in receipt["menu_items"])
                        if field in {"quantity", "unit_price", "amount"}
                        else receipt[field] is None
                    )
                    missing[field] += int(absent)
                page_id = f"cord_{source}_{index:06d}"
                image = (image_dir / f"{page_id}.png").resolve()
                example["image"].convert("RGB").save(image)
                splits[destination].append(
                    {
                        "id": page_id,
                        "doc_type": "receipt",
                        "image": str(image),
                        "target": target,
                    }
                )
                accepted += 1
            LOG.info(
                "CORD %s: accepted=%d mapping_failed=%d failed_by_reason=%s "
                "items_skipped=%d receipts_missing=%s",
                source,
                accepted,
                sum(failures.values()),
                dict(failures),
                stats["items_skipped"],
                dict(missing),
            )
    all_ids = [row["id"] for rows in splits.values() for row in rows]
    assert len(all_ids) == len(set(all_ids)), "ID overlap across splits"
    for split, rows in splits.items():
        (out / f"{split}.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        )
        print(f"{split}: {len(rows)} {dict(Counter(row['doc_type'] for row in rows))}")
    # Keep labels outside JSONL's intentionally small public record format.
    (out / "synth_source.json").write_text(json.dumps({"path": str(synth.resolve())}))
    return splits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synth", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cord", action="store_true")
    parser.add_argument("--max-cord-train", type=int)
    args = parser.parse_args()
    if args.max_cord_train is not None and args.max_cord_train < 0:
        parser.error("--max-cord-train must be non-negative")
    logging.basicConfig(level=logging.INFO)
    build_dataset(args.synth, args.out, args.cord, args.max_cord_train)


if __name__ == "__main__":
    main()
