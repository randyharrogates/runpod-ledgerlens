"""Build compact demo insights and a browsable synthetic subset."""

import argparse
import json
import shutil
from pathlib import Path

from ledgerlens.insights import (
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
    index["total_pages"] = len(pages)
    if len(json.dumps(index, separators=(",", ":")).encode()) > 600_000:
        index["note"] = (
            "Index capped at 600 KB; page details in committed mode are available only for the sample subset."
        )
        low, high = 0, len(pages)
        while low < high:
            middle = (low + high + 1) // 2
            index["pages"] = pages[:middle]
            if len(json.dumps(index, separators=(",", ":")).encode()) <= 599_999:
                low = middle
            else:
                high = middle - 1
        index["pages"] = pages[:low]
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
