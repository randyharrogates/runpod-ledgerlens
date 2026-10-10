"""Small, deterministic summaries of local dataset and evaluation artefacts."""

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

STRENGTHS = ("clean", "light", "medium", "heavy")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dataset_pages(data_dir):
    root = Path(data_dir)
    splits = read_json(root / "splits.json")
    membership = {p: s for s in ("train", "val", "test") for p in splits.get(s, [])}
    for path in sorted(root.glob("*.meta.json")):
        identity = path.name.removesuffix(".meta.json")
        yield (
            identity,
            membership.get(identity, "unknown"),
            read_json(path),
            read_json(root / f"{identity}.json"),
        )


def page_index(data_dir):
    return {
        "pages": [
            {
                "id": identity,
                "split": split,
                "doc_type": gold["doc_type"],
                "layout_id": meta.get("layout_id"),
                "strength": meta.get("strength"),
                "anomalies": sorted({a["type"] for a in meta.get("anomalies", [])}),
                "business_id": meta.get("business_id"),
                "row_count": len(gold.get("transactions", gold.get("line_items", []))),
            }
            for identity, split, meta, gold in dataset_pages(data_dir)
        ]
    }


def dataset_summary(data_dir):
    counts = {
        k: Counter()
        for k in ("split", "doc_type", "strength", "currency", "country", "rows")
    }
    layouts, businesses = defaultdict(set), defaultdict(set)
    layout_counts, anomalies = Counter(), Counter()
    amounts = {direction: Counter() for direction in ("money_in", "money_out")}
    dates, none, pages = [], 0, 0
    for _, split, meta, gold in dataset_pages(data_dir):
        pages += 1
        businesses[meta.get("business_id")].add(split)
        layout = meta.get("layout_id", "unknown")
        layouts[layout].add(split)
        layout_counts[layout] += 1
        rows = gold.get("transactions", gold.get("line_items", []))
        for key, value in (
            ("split", split),
            ("doc_type", gold["doc_type"]),
            ("strength", meta.get("strength", "unknown")),
            ("currency", gold.get("currency", "unknown")),
            ("country", meta.get("country", "unknown")),
            ("rows", str(len(rows))),
        ):
            counts[key][value] += 1
        labels = meta.get("anomalies", [])
        anomalies.update(a["type"] for a in labels)
        none += not labels
        dates.extend(
            gold[k] for k in ("date", "period_start", "period_end") if gold.get(k)
        )
        for row in rows:
            if row.get("date"):
                dates.append(row["date"])
            for field, direction in (("credit", "money_in"), ("debit", "money_out")):
                value = abs(float(row.get(field) or 0))
                if value:
                    exponent = math.floor(math.log10(value))
                    amounts[direction][str(exponent)] += 1
    return {
        "totals": {"pages": pages, "businesses": len(businesses)},
        "pages_by_split": dict(counts["split"]),
        "pages_by_doc_type": dict(counts["doc_type"]),
        "pages_by_layout_id": {
            k: {"pages": layout_counts[k], "splits": sorted(v)}
            for k, v in sorted(layouts.items())
        },
        "strength_histogram": {k: counts["strength"][k] for k in STRENGTHS},
        "anomaly_counts": dict(anomalies),
        "no_anomalies_share": none / pages if pages else 0,
        "rows_per_page_histogram": dict(counts["rows"]),
        "currency_counts": dict(counts["currency"]),
        "country_counts": dict(counts["country"]),
        "transaction_amount_distribution": {
            "bin_definition": "key k means 10^k <= absolute amount < 10^(k+1), in document currency; zero excluded",
            **{k: dict(v) for k, v in amounts.items()},
        },
        "date_range": {
            "start": min(dates) if dates else None,
            "end": max(dates) if dates else None,
        },
        "leakage": {
            "businesses_in_multiple_splits": sum(
                len(v) > 1 for v in businesses.values()
            ),
            "test_only_layouts": sorted(k for k, v in layouts.items() if v == {"test"}),
        },
    }


def training_summary(train_summary_path):
    raw = read_json(train_summary_path)
    keys = (
        "steps",
        "final_train_loss",
        "average_train_loss",
        "final_eval_loss",
        "best_eval_loss",
        "wall_seconds",
        "gpu_name",
        "peak_memory_bytes",
        "max_pixels",
    )
    args = (
        "model",
        "lr",
        "learning_rate",
        "epochs",
        "rank",
        "lora_alpha",
        "lora_dropout",
        "batch",
        "grad_accum",
        "load_in_4bit",
        "finetune_vision",
        "max_pixels",
        "max_seq_length",
    )
    return {
        **{k: raw[k] for k in keys if k in raw},
        "args": {k: raw["args"][k] for k in args if k in raw.get("args", {})},
        "training_loss": [
            {"step": r["step"], "loss": r["loss"]}
            for r in raw.get("log_history", [])
            if "loss" in r and "step" in r
        ],
        "eval_loss": [
            {"step": r["step"], "loss": r["eval_loss"]}
            for r in raw.get("log_history", [])
            if "eval_loss" in r and "step" in r
        ],
    }


def aggregate(pages):
    def average(values):
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else None

    return {
        "pages": len(pages),
        "row_f1": average([p["rows"]["f1"] for p in pages]),
        "header_accuracy": average([p["header_accuracy"] for p in pages]),
        "reconciliation_accuracy": average(
            [
                p.get("reconciliation_accuracy")
                for p in pages
                if p.get("page_reconciles") is not None
            ]
        ),
        "page_reconciles": average([p.get("page_reconciles") for p in pages]),
        "valid_json": None,
        "valid_json_note": "Per-page JSON validity is not recorded; error may also mean schema validation failed.",
    }


def results_breakdown(results_path, data_dir):
    raw = read_json(results_path)
    metadata = {i: m for i, _, m, _ in dataset_pages(data_dir)}
    output = {}
    for split, models in raw.items():
        entries = {}
        for model, report in models.items():
            groups = {"layout_id": defaultdict(list), "strength": defaultdict(list)}
            for identity, score in report["pages"].items():
                meta = metadata.get(identity, {})
                for key in groups:
                    groups[key][meta.get(key, "unknown")].append(score)
            entries[model] = {
                "overall": {
                    **aggregate(list(report["pages"].values())),
                    "valid_json": report["valid_json_rate"],
                },
                **{
                    f"by_{key}": {k: aggregate(v) for k, v in buckets.items()}
                    for key, buckets in groups.items()
                },
            }
        ft = models.get("4b-ft", models.get("ft", {})).get("pages", {})
        hardest = sorted(ft, key=lambda p: (ft[p]["rows"]["f1"], p))[:20]
        output[split] = {
            "models": entries,
            "hardest_pages": [
                {
                    "id": p,
                    "row_f1": {
                        m: r["pages"][p]["rows"]["f1"] if p in r["pages"] else None
                        for m, r in models.items()
                    },
                    "meta": metadata.get(p),
                    "metadata_available": p in metadata,
                }
                for p in hardest
            ],
        }
    return {
        "splits": output,
        "aggregation": "Per-page macro averages; reconciliation excludes non-statements.",
    }
