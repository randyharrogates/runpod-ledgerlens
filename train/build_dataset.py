"""Build leakage-safe JSONL manifests from synthetic pages and optional CORD v2."""

import argparse
import json
import logging
from collections import Counter
from decimal import Decimal
from pathlib import Path

from train.common import SETS, canonical_target

LOG = logging.getLogger(__name__)


def printed_decimal(value):
    if value is None or str(value).strip() == "":
        return None
    amount = Decimal(str(value).replace(",", "").replace(" ", ""))
    if not amount.is_finite():
        raise ValueError("Non-finite CORD number")
    return amount


def cord_receipt(ground_truth: str | dict) -> dict:
    """Map printed fields only; absent fields remain null for schema validation."""
    data = json.loads(ground_truth) if isinstance(ground_truth, str) else ground_truth
    parsed = data["gt_parse"]
    menu = parsed.get("menu", [])
    if isinstance(menu, dict):
        menu = [menu] if "nm" in menu else list(menu.values())
    items = []
    for item in menu:
        items.append(
            {
                "description": item.get("nm"),
                "quantity": printed_decimal(item.get("cnt")),
                "unit_price": printed_decimal(item.get("unitprice")),
                "amount": printed_decimal(item.get("price")),
            }
        )
    subtotal = parsed.get("sub_total") or {}
    total = parsed.get("total") or {}
    return {
        "doc_type": "receipt",
        "menu_items": items,
        "subtotal": printed_decimal(subtotal.get("subtotal_price")),
        "tax": printed_decimal(subtotal.get("tax_price")),
        "total": printed_decimal(total.get("total_price")),
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
            failed = accepted = 0
            for index, example in enumerate(dataset):
                if (
                    source == "train"
                    and max_cord_train is not None
                    and accepted >= max_cord_train
                ):
                    break
                try:
                    target = canonical_target(cord_receipt(example["ground_truth"]))
                except (ValueError, TypeError, KeyError, ArithmeticError) as error:
                    failed += 1
                    LOG.warning("Skipping CORD %s/%s: %s", source, index, error)
                    continue
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
            LOG.info("CORD %s: accepted=%d mapping_failed=%d", source, accepted, failed)
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
