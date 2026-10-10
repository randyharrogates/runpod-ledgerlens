"""Offline dataset generation and leakage-safe business splits."""

import argparse
import json
import random
from pathlib import Path

from PIL import Image

from ledgerlens.datagen.business import Business, generate_businesses
from ledgerlens.datagen.degrade import degrade
from ledgerlens.datagen.invoice import generate_invoice
from ledgerlens.datagen.layouts import INVOICE_LAYOUTS, LAYOUTS, render_html
from ledgerlens.datagen.ledger import generate_ledger
from ledgerlens.datagen.paginate import paginate
from ledgerlens.datagen.render import RenderError, render_png
from ledgerlens.schema import Invoice, Statement


def assign_business_splits(businesses: list[Business], seed: int) -> dict[str, str]:
    """Split identities first; small datasets still have a test business."""
    held_out = {
        business.id for business in businesses if LAYOUTS[business.layout_id].held_out
    }
    ids = [business.id for business in businesses if business.id not in held_out]
    random.Random(seed).shuffle(ids)
    test_count = max(1, len(ids) // 10) if ids else 0
    val_count = max(1, len(ids) // 10) if len(ids) >= 3 else 0
    assignments = {identity: "test" for identity in held_out}
    assignments.update(
        {
            identity: "test"
            if i < test_count
            else "val"
            if i < test_count + val_count
            else "train"
            for i, identity in enumerate(ids)
        }
    )
    names: dict[str, set[str]] = {split: set() for split in ("train", "val", "test")}
    for business in businesses:
        names[assignments[business.id]].update(
            name.casefold()
            for name in (business.name, *business.customers, *business.suppliers)
        )
    assert not (
        names["train"] & names["val"]
        or names["train"] & names["test"]
        or names["val"] & names["test"]
    ), "Counterparty leakage"
    return assignments


def generate_dataset(
    out: Path, n_businesses: int, seed: int, no_render: bool = False
) -> dict:
    """Write HTML, extraction answers, provenance, optional images and splits."""
    out.mkdir(parents=True, exist_ok=True)
    if (out / "splits.json").exists():
        raise ValueError(
            "Output already contains a dataset; choose an empty output directory"
        )
    businesses = generate_businesses(n_businesses, seed)
    assignments = assign_business_splits(businesses, seed)
    rng = random.Random(seed)
    splits: dict[str, list[str]] = {"train": [], "val": [], "test": []}
    page_info: dict[str, dict] = {}

    def write(
        page_id: str, document: Statement | Invoice, metadata: dict, split: str
    ) -> None:
        layout_id = metadata["layout_id"]
        html_path = out / f"{page_id}.html"
        html_path.write_text(render_html(document, layout_id), encoding="utf-8")
        (out / f"{page_id}.json").write_text(
            document.model_dump_json(indent=2), encoding="utf-8"
        )
        strength = rng.choice(["clean", "light", "medium", "heavy"])
        image_seed = rng.randrange(2**32)
        metadata.update(
            strength=strength,
            seed=image_seed,
            rendered=False,
            degradation={"operations": []},
        )
        if not no_render:
            image_path = out / f"{page_id}.png"
            render_png(html_path, image_path)
            with Image.open(image_path) as image:
                degraded, details = degrade(image, strength, image_seed)
            degraded.save(image_path)
            metadata.update(rendered=True, degradation=details)
        else:
            metadata["degradation"]["status"] = "not_applied_no_render"
        (out / f"{page_id}.meta.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        splits[split].append(page_id)
        page_info[page_id] = {
            "business_id": metadata["business_id"],
            "layout_id": layout_id,
        }

    for i, business in enumerate(businesses):
        split = assignments[business.id]
        ledger = generate_ledger(business, seed + i * 101)
        for page in paginate(ledger, business.id):
            layout_id = business.layout_id
            page.statement.bank_name = LAYOUTS[layout_id].bank_name
            page.metadata.update(
                layout_id=layout_id, country=business.country, uen=business.uen
            )
            write(page.id, page.statement, page.metadata, split)
        invoice_rng = random.Random(seed * 7919 + i)
        for j, layout_id in enumerate(INVOICE_LAYOUTS):
            invoice = generate_invoice(business, invoice_rng)
            write(
                f"{business.id}_inv{j + 1}",
                invoice,
                {"business_id": business.id, "layout_id": layout_id, "anomalies": []},
                split,
            )
    manifest = {
        **splits,
        "business_splits": assignments,
        "business_test_sets": {
            business.id: (
                "test_unseen_layout"
                if LAYOUTS[business.layout_id].held_out
                else "test_seen_layout"
            )
            for business in businesses
            if assignments[business.id] == "test"
        },
        "pages": page_info,
        "held_out_layouts": [key for key, layout in LAYOUTS.items() if layout.held_out],
        "seed": seed,
    }
    (out / "splits.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    """Parse the dataset command and report local rendering errors clearly."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--n-businesses", type=int, default=3)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args()
    try:
        manifest = generate_dataset(
            args.out, args.n_businesses, args.seed, args.no_render
        )
    except (RenderError, ValueError) as error:
        parser.exit(1, f"Generation failed: {error}\n")
    print(f"Wrote {len(manifest['pages'])} documents to {args.out}")
