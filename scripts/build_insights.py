"""Build compact demo insights and a browsable synthetic subset."""

import argparse
import json
import shutil
from pathlib import Path

from ledgerlens.insights import (
    compact_page_index,
    dataset_summary,
    page_index,
    results_breakdown,
    training_summary,
)

BASE = Path(__file__).resolve().parents[1] / "data"
ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, separators=(",", ":"), ensure_ascii=False) + "\n")


def compare_examples(data_dir, results_path, breakdown):
    """Bundle original predictions and the grader's one-to-one row matches."""
    results = json.loads(results_path.read_text())
    candidates = [("test_unseen_layout", "biz_0007_p006")]
    candidates += [
        ("test_unseen_layout", p["id"])
        for p in breakdown["splits"]["test_unseen_layout"]["hardest_pages"][:2]
    ]
    for split, predicate in [
        ("test_seen_layout", lambda name: "_p" in name),
        ("test_unseen_layout", lambda name: "_inv" in name),
        ("test_cord", lambda name: True),
    ]:
        names = results.get(split, {}).get("8b", {}).get("pages", {})
        candidates += [(split, name) for name in names if predicate(name)][:1]
    examples, skipped = [], []
    for split, name in candidates:
        gold_paths = [data_dir / (name + ".json")]
        gold_paths += list((results_path.parents[1] / "gold").rglob(name + ".json"))
        gold_path = next((p for p in gold_paths if p.is_file()), None)
        if gold_path is None:
            skipped.append(name + ": gold not available locally")
            continue
        gold = json.loads(gold_path.read_text())
        rows = gold.get("transactions", gold.get("line_items", []))
        meta_path = data_dir / (name + ".meta.json")
        entry = {
            "id": name,
            "split": split,
            "meta": json.loads(meta_path.read_text()) if meta_path.exists() else {},
            "currency": gold.get("currency"),
            "doc_type": gold["doc_type"],
            "gold_rows": rows,
            "models": {},
        }
        for model in ("8b", "4b", "4b-ft"):
            prediction = (
                results_path.parents[1]
                / "predictions"
                / model
                / split
                / (name + ".json")
            )
            doc = json.loads(prediction.read_text())
            score = results[split][model]["pages"][name]["rows"]
            matches = {m["gold_row"]: m["predicted_row"] for m in score["matches"]}
            entry["models"][model] = {
                "rows": doc.get("transactions", doc.get("line_items", [])),
                "matched": [i in matches for i in range(len(rows))],
                "matched_indices": [matches.get(i) for i in range(len(rows))],
                "row_f1": score["f1"],
                "status": "recorded",
            }
        examples.append(entry)
    payload = {
        "examples": examples,
        "skipped": skipped,
        "source": "committed-final-test",
    }
    if len(json.dumps(payload, ensure_ascii=False).encode()) >= 300_000:
        raise ValueError("Compare examples exceed 300 KB")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=BASE / "synth400")
    parser.add_argument(
        "--results", type=Path, default=BASE / "eval-final/eval/results.json"
    )
    parser.add_argument(
        "--training", type=Path, default=BASE / "eval-final/main/train_summary.json"
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "eval")
    parser.add_argument("--sample-dir", type=Path, default=ROOT / "app/sample/dataset")
    args = parser.parse_args()
    write(
        args.output_dir / "compare_examples.json",
        compare_examples(
            args.data_dir, args.results, results_breakdown(args.results, args.data_dir)
        ),
    )
    index = page_index(args.data_dir)
    pages = index["pages"]
    chosen, covered = [], set()
    while len(chosen) < min(24, len(pages)):

        def tags(p):
            return {(k, p[k]) for k in ("split", "layout_id", "doc_type", "strength")}

        candidate = max(
            (p for p in pages if p not in chosen),
            key=lambda p: (
                len(tags(p) - covered),
                -sum(c["split"] == p["split"] for c in chosen),
                -sum(c["layout_id"] == p["layout_id"] for c in chosen),
                -sum(c["strength"] == p["strength"] for c in chosen),
            ),
        )
        chosen.append(candidate)
        covered.update(tags(candidate))
    args.sample_dir.mkdir(parents=True, exist_ok=True)
    selected_names = {
        p["id"] + suffix for p in chosen for suffix in (".html", ".json", ".meta.json")
    }
    for existing in args.sample_dir.iterdir():
        if (
            existing.name != "splits.json"
            and existing.name not in selected_names
            and existing.suffix in (".html", ".json")
        ):
            existing.unlink()
    total = 0
    for page in chosen:
        for suffix in (".html", ".json", ".meta.json"):
            source = args.data_dir / (page["id"] + suffix)
            total += source.stat().st_size
            if total >= 2_000_000:
                raise ValueError("Sample exceeds 2 MB")
            shutil.copyfile(source, args.sample_dir / source.name)
    write(
        args.sample_dir / "splits.json",
        {
            s: [p["id"] for p in chosen if p["split"] == s]
            for s in ("train", "val", "test")
        },
    )
    index = compact_page_index(index)
    for name, value in [
        ("dataset_summary", dataset_summary(args.data_dir)),
        ("page_index", index),
        ("training_summary", training_summary(args.training)),
        ("breakdown", results_breakdown(args.results, args.data_dir)),
    ]:
        write(args.output_dir / (name + ".json"), value)
        print(name, (args.output_dir / (name + ".json")).stat().st_size)
    print("sample pages", len(chosen), "bytes", total)


if __name__ == "__main__":
    main()
