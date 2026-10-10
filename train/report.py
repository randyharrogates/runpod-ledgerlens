"""Rebuild gold files and compare model predictions with the shared evaluator."""

import argparse
import json
import shutil
import sys
from pathlib import Path

from ledgerlens.eval.score import score_batch
from train.common import canonical_target, read_rows


def cost_per_1000(seconds_per_page: float, price_per_hour: float) -> float:
    if seconds_per_page < 0 or price_per_hour < 0:
        raise ValueError("Time and price must be non-negative")
    return seconds_per_page * 1000 * price_per_hour / 3600


def error_types(page: dict) -> str:
    if page.get("error"):
        return "missing_or_invalid_document"
    errors = []
    if page["header_accuracy"] < 1:
        errors.append("header")
    if page["rows"]["f1"] < 1:
        errors.append("rows")
    if page.get("page_reconciles") is False:
        errors.append("reconciliation")
    return ", ".join(errors) or "none"


def assignments(values: list[str]) -> dict:
    result = {}
    for value in values:
        name, content = value.split("=", 1)
        if name in result:
            raise ValueError(f"Duplicate model: {name}")
        result[name] = content
    return result


SPLIT_LABELS = {
    "test_unseen_layout": "Unseen statement layouts",
    "test_seen_layout": "Seen layouts, new businesses",
    "test_cord": "Real receipts (CORD v2 test)",
}


def summarise(results: dict) -> dict:
    """Keep aggregate metrics only; page-level scores stay in the full report."""
    splits = {}
    for split, models in results.items():
        if models.get("status") == "empty_set":
            splits[split] = {"status": "empty_set"}
            continue
        splits[split] = {}
        for model, result in models.items():
            timing = result["timing"]
            values = {
                "pages": timing["pages"],
                "header_accuracy": result.get("header_accuracy"),
                "row_f1": result.get("row_f1"),
                "row_precision": result.get("row_precision"),
                "row_recall": result.get("row_recall"),
                "row_f1_checked": result.get("rows_direction_checked", {}).get("f1"),
                "reconciliation": result.get("page_reconciliation_rate"),
                "valid_json": result.get("valid_json_rate"),
                "anomaly_precision": result.get("anomaly_precision"),
                "anomaly_recall": result.get("anomaly_recall"),
                "seconds_per_page": timing.get("seconds_per_page"),
                "cost_per_1000_pages": result.get("cost_per_1000_pages"),
                "price_per_hour": result.get("price_per_hour"),
                "gpu_name": timing.get("gpu_name"),
            }
            splits[split][model] = {
                key: round(value, 4) if isinstance(value, float) else value
                for key, value in values.items()
            }
    return {
        "generated_from": "batch evaluation, one GPU Pod",
        "split_labels": SPLIT_LABELS.copy(),
        "splits": splits,
    }


def make_summary(results: Path, out: Path) -> dict:
    summary = summarise(json.loads(results.read_text()))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def make_report(
    data: Path,
    models: dict[str, Path],
    prices: dict[str, float],
    out: Path,
    sets: list[str],
):
    source_file = data / "synth_source.json"
    source = (
        Path(json.loads(source_file.read_text())["path"])
        if source_file.exists()
        else None
    )
    results = {}
    lines = [
        "# LedgerLens batch evaluation",
        "",
        "Timing includes image loading, batch inference and output writes; model load is recorded separately. Costs use the supplied hourly prices.",
        "",
    ]
    for split in sets:
        rows = read_rows(data, split)
        if not rows:
            results[split] = {"status": "empty_set"}
            lines.extend([f"## {split}", "", "No pages in this set.", ""])
            continue
        gold = out / "gold" / split
        gold.mkdir(parents=True, exist_ok=True)
        for stale in gold.glob("*.json"):
            stale.unlink()
        for row in rows:
            (gold / f"{row['id']}.json").write_text(canonical_target(row["target"]))
            metadata = source / f"{row['id']}.meta.json" if source else None
            if metadata and metadata.exists():
                shutil.copyfile(metadata, gold / metadata.name)
            elif row["doc_type"] == "statement":
                raise ValueError(f"Missing anomaly metadata for {row['id']}")
        results[split] = {}
        lines.extend(
            [
                f"## {split}",
                "",
                "| Model | Header accuracy | Row F1 (raw model) | Row F1 (after balance check) | Reconciliation | Valid JSON | Anomaly precision | Anomaly recall | Seconds/page | Cost/1,000 pages |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        worst_lines = []
        for name, directory in models.items():
            prediction = directory / split
            timing = json.loads((prediction / "_timing.json").read_text())
            expected_ids = {row["id"] for row in rows}
            if timing["pages"] != len(rows) or set(timing["page_ids"]) != expected_ids:
                raise ValueError(
                    f"{name}/{split}: partial inference cannot be reported as a full evaluation"
                )
            result = score_batch(prediction, gold)
            # Invalid marker files are JSON envelopes, not valid extraction JSON.
            invalid = sum(
                json.loads(path.read_text()).get("_invalid", False)
                for path in (prediction / f"{row['id']}.json" for row in rows)
                if path.exists()
            )
            result["valid_json_rate"] = max(
                0, result["valid_json_rate"] - invalid / len(rows)
            )
            if not any(row["doc_type"] == "statement" for row in rows):
                result["anomaly_precision"] = result["anomaly_recall"] = None
            result["timing"] = timing
            result["price_per_hour"] = prices[name]
            result["cost_per_1000_pages"] = cost_per_1000(
                timing["seconds_per_page"], prices[name]
            )
            worst = sorted(
                result["pages"].items(),
                key=lambda pair: (
                    pair[1]["header_accuracy"] + pair[1]["rows"]["f1"],
                    pair[0],
                ),
            )[:10]
            result["worst_pages"] = [
                {"id": page_id, "error_type": error_types(page)}
                for page_id, page in worst
            ]
            results[split][name] = result
            values = [
                result[key]
                for key in (
                    "header_accuracy",
                    "row_f1",
                    "page_reconciliation_rate",
                    "valid_json_rate",
                    "anomaly_precision",
                    "anomaly_recall",
                )
            ]
            values.insert(2, result["rows_direction_checked"]["f1"])
            values.extend([timing["seconds_per_page"], result["cost_per_1000_pages"]])
            lines.append(
                f"| {name} | "
                + " | ".join(
                    "N/A" if value is None else f"{value:.4f}" for value in values
                )
                + " |"
            )
            worst_lines.extend(
                [
                    "",
                    f"### {name}: 10 lowest-scoring pages",
                    "",
                    *[
                        f"- {entry['id']}: {entry['error_type']}"
                        for entry in result["worst_pages"]
                    ],
                ]
            )
        lines.extend(worst_lines + [""])
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=2))
    make_summary(out / "results.json", out / "summary.json")
    (out / "results.md").write_text("\n".join(lines) + "\n")
    return results


def main():
    if sys.argv[1:2] == ["summary"]:
        parser = argparse.ArgumentParser(
            description="Write a compact evaluation summary"
        )
        parser.add_argument("--results", type=Path, required=True)
        parser.add_argument("--out", type=Path, default=Path("eval/summary.json"))
        args = parser.parse_args(sys.argv[2:])
        make_summary(args.results, args.out)
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument(
        "--model", action="append", required=True, help="NAME=PREDICTION_DIR (repeat)"
    )
    parser.add_argument(
        "--price", action="append", required=True, help="NAME=HOURLY_USD (repeat)"
    )
    parser.add_argument("--out", type=Path, default=Path("eval"))
    parser.add_argument(
        "--sets", default="test_unseen_layout,test_seen_layout,test_cord"
    )
    args = parser.parse_args()
    models = {key: Path(value) for key, value in assignments(args.model).items()}
    prices = {key: float(value) for key, value in assignments(args.price).items()}
    if models.keys() != prices.keys():
        parser.error("Each model needs exactly one price")
    make_report(args.data, models, prices, args.out, args.sets.split(","))


if __name__ == "__main__":
    main()
