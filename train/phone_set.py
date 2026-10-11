"""Print, pair and score held-out synthetic pages photographed on a phone."""

import argparse
import base64
import html
import json
import math
import os
import random
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from ledgerlens.datagen.render import RenderError, render_png
from ledgerlens.images import DEFAULT_MAX_PIXELS
from train.common import canonical_target, read_rows
from train.predict import parse_prediction
from train.report import assignments, make_report
from worker.client import call_endpoint, job_errors, runpodctl_key

BUCKETS = ("test_unseen_layout", "test_seen_layout", "invoice")
PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def safe_name(value: str) -> str:
    if not value or Path(value).name != value or value.startswith("."):
        raise ValueError("Page and model names must be plain file names")
    return value


def write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def select_pages(synth: Path, n: int = 20, seed: int = 7) -> list[dict]:
    """Proportional quotas, then round-robin by least-used business/layout/currency."""
    if n <= 0:
        raise ValueError("--n must be positive")
    splits = json.loads((synth / "splits.json").read_text())
    pools = {bucket: [] for bucket in BUCKETS}
    for page_id in sorted(set(splits["test"])):
        safe_name(page_id)
        info = splits["pages"][page_id]
        gold = json.loads(canonical_target((synth / f"{page_id}.json").read_text()))
        test_set = splits["business_test_sets"][info["business_id"]]
        page = {
            "page_id": page_id,
            "doc_type": gold["doc_type"],
            "business_id": info["business_id"],
            "layout": info["layout_id"],
            "currency": gold["currency"],
            "test_set": test_set,
        }
        bucket = "invoice" if gold["doc_type"] == "invoice" else test_set
        pools[bucket].append(page)
    exact = [n * weight / 20 for weight in (10, 6, 4)]
    quotas = [math.floor(value) for value in exact]
    for index in sorted(range(3), key=lambda i: (-(exact[i] - quotas[i]), i))[
        : n - sum(quotas)
    ]:
        quotas[index] += 1
    rng = random.Random(seed)
    for bucket, quota in zip(BUCKETS, quotas, strict=True):
        rng.shuffle(pools[bucket])
        if len(pools[bucket]) < quota:
            raise ValueError(
                f"Need {quota} {bucket} pages; only {len(pools[bucket])} available"
            )
    counts = {field: Counter() for field in ("business_id", "layout", "currency")}
    chosen = []
    while sum(quotas):
        for i, bucket in enumerate(BUCKETS):
            if not quotas[i]:
                continue
            page = min(
                pools[bucket],
                key=lambda p: tuple(counts[field][p[field]] for field in counts),
            )
            pools[bucket].remove(page)
            chosen.append(page)
            quotas[i] -= 1
            for field in counts:
                counts[field][page[field]] += 1
    # Arrange the selected multiset without consecutive business identities. Taking the
    # largest remaining business first guarantees a solution whenever one exists.
    ordered = []
    remaining = Counter(page["business_id"] for page in chosen)
    if max(remaining.values()) > (n + 1) // 2:
        raise ValueError(
            "Too few test businesses to avoid consecutive pages; reduce --n"
        )
    layout_counts, currency_counts = Counter(), Counter()
    while chosen:
        eligible = [
            page
            for page in chosen
            if not ordered or page["business_id"] != ordered[-1]["business_id"]
        ]
        page = min(
            eligible,
            key=lambda p: (
                -remaining[p["business_id"]],
                layout_counts[p["layout"]],
                currency_counts[p["currency"]],
            ),
        )
        chosen.remove(page)
        remaining[page["business_id"]] -= 1
        layout_counts[page["layout"]] += 1
        currency_counts[page["currency"]] += 1
        ordered.append({"order": len(ordered) + 1, **page})
    return ordered


def checked_image(path: Path) -> Image.Image:
    """Check header dimensions before any pixel decode, matching the upload limit."""
    try:
        image = Image.open(path)
        if image.width * image.height > 60_000_000:
            image.close()
            raise ValueError("Image exceeds 60,000,000 pixels; resize before ingest")
        return image
    except Image.DecompressionBombError:
        raise ValueError("Image exceeds safe pixel dimensions") from None


def prepare(synth: Path, out: Path, n: int = 20, seed: int = 7):
    pages = select_pages(synth, n, seed)
    out.mkdir(parents=True, exist_ok=True)
    clean = out / "clean"
    clean.mkdir(exist_ok=True)
    sheets = []
    try:
        for page in pages:
            path = clean / f"{page['page_id']}.png"
            render_png(synth / f"{page['page_id']}.html", path)
            # Fit the complete clean page above a separate bottom margin; no text is covered.
            with checked_image(path) as source:
                content = ImageOps.contain(source.convert("RGB"), (1180, 1650))
            sheet = Image.new("RGB", (1240, 1754), "white")
            sheet.paste(content, ((1240 - content.width) // 2, 20))
            draw = ImageDraw.Draw(sheet)
            draw.text(
                (40, 1700),
                f"LedgerLens phone test {page['order']:02d}/{n}",
                fill="black",
                font=ImageFont.load_default(size=20),
            )
            sheets.append(sheet)
        sheets[0].save(
            out / "print.pdf", save_all=True, append_images=sheets[1:], resolution=150
        )
    finally:
        for sheet in sheets:
            sheet.close()
    write_json(
        out / "manifest.json",
        {"synth": str(synth.resolve()), "seed": seed, "pages": pages},
    )
    return pages


def photo_time(path: Path):
    with checked_image(path) as image:
        exif = image.getexif()
        value = exif.get(36867)
        if value is None and 34665 in exif:
            value = exif.get_ifd(34665).get(36867)
        try:
            stamp = datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            stamp = None
    # Dated photos precede undated ones; names break equal timestamps and order undated files.
    return (stamp is None, stamp or datetime.max, path.name)


def ingest(phone: Path, photos: Path, out: Path):
    """Pair numbered photos by manifest order, or a full set by camera time."""
    manifest = json.loads((phone / "manifest.json").read_text())
    pages = manifest["pages"]
    synth = Path(manifest["synth"])
    files = [
        path
        for path in photos.iterdir()
        if path.is_file() and not path.name.startswith(".")
    ]
    if any(path.suffix.lower() in {".heic", ".heif"} for path in files):
        raise ValueError(
            "HEIC is not supported; convert to JPEG first, e.g. on a Mac: "
            "mkdir jpg && sips -s format jpeg *.HEIC --out jpg"
        )
    files = [path for path in files if path.suffix.lower() in PHOTO_SUFFIXES]
    missing = []
    if not files:
        raise ValueError("No photos found")
    if all(path.stem.isdigit() for path in files):
        orders = {page["order"] for page in pages}
        numbered = {}
        for path in files:
            number = int(path.stem)
            if number not in orders:
                raise ValueError(
                    f"Photo number {number} has no matching manifest order"
                )
            if number in numbered:
                raise ValueError(f"Duplicate photo number {number}")
            numbered[number] = path
        missing = [page for page in pages if page["order"] not in numbered]
        pages = [page for page in pages if page["order"] in numbered]
        files = [numbered[page["order"]] for page in pages]
    elif len(files) != len(pages):
        raise ValueError(
            f"Photo count {len(files)} must equal manifest count {len(pages)}"
        )
    else:
        files.sort(key=photo_time)
    ids = [safe_name(page["page_id"]) for page in pages]
    if not pages or len(ids) != len(set(ids)):
        raise ValueError("Manifest must contain distinct pages")
    targets = [
        canonical_target((synth / f"{page_id}.json").read_text()) for page_id in ids
    ]
    images = out / "phone_images"
    images.mkdir(parents=True, exist_ok=True)
    rows, pairs = [], []
    for page, photo, target in zip(pages, files, targets, strict=True):
        page_id = page["page_id"]
        destination = images / f"{page_id}.jpg"
        with checked_image(photo) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Use JPEG, PNG or WEBP photos")
            oriented = ImageOps.exif_transpose(source)
            oriented.convert("RGB").save(destination, quality=95)
        rows.append(
            {
                "id": page_id,
                "doc_type": json.loads(target)["doc_type"],
                "image": str(destination.resolve()),
                "target": target,
            }
        )
        clean = phone / "clean" / f"{page_id}.png"
        pairs.append(
            f"<section><h2>{page['order']:02d}: {html.escape(page_id)} — {html.escape(photo.name)}</h2>"
            f'<img src="{html.escape(destination.resolve().as_uri(), quote=True)}" alt="Phone photo">'
            f'<img src="{html.escape(clean.resolve().as_uri(), quote=True)}" alt="Clean page"></section>'
        )
    (out / "test_phone.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    write_json(out / "synth_source.json", {"path": str(synth.resolve())})
    (out / "pairs.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Phone test pairing</title>'
        "<style>img{width:45%;vertical-align:top}section{break-inside:avoid}</style>"
        "<h1>Confirm each photo matches its clean page before scoring</h1>"
        + "\n".join(pairs),
        encoding="utf-8",
    )
    print(
        f"Open {out / 'pairs.html'} and confirm all photo/clean-page pairings before scoring."
    )
    if missing:
        print(
            "Missing pages: "
            + ", ".join(f"{page['order']:02d} {page['page_id']}" for page in missing)
        )
    return rows


def milliseconds(result, field):
    value = float(result[field]) / 1000
    if not math.isfinite(value) or value < 0:
        raise ValueError("Invalid Runpod timing")
    return value


def score(data: Path, endpoints: dict[str, str], prices: dict[str, float], out: Path):
    if not endpoints or endpoints.keys() != prices.keys():
        raise ValueError("Each endpoint needs exactly one matching price")
    for name in endpoints:
        safe_name(name)
        if not endpoints[name] or not math.isfinite(prices[name]) or prices[name] < 0:
            raise ValueError(
                "Endpoint IDs must be nonempty and prices finite and non-negative"
            )
    rows = read_rows(data, "test_phone")
    if not rows:
        raise ValueError("Phone test set is empty")
    key = os.getenv("RUNPOD_API_KEY") or runpodctl_key()
    if not key:
        raise ValueError(
            "No Runpod API key: run `runpodctl doctor` or set RUNPOD_API_KEY"
        )
    models = {}
    for name, endpoint in endpoints.items():
        directory = out / "predictions" / name
        destination = directory / "test_phone"
        destination.mkdir(parents=True, exist_ok=True)
        for stale in destination.glob("*.json"):
            stale.unlink()
        executions, delays, loads, failures = [], [], [], []
        cold_starts = 0
        started = time.perf_counter()
        gpu, model = None, endpoint
        for row in rows:
            payload = {
                "task": "extract",
                "doc_type": row["doc_type"],
                "image_base64": base64.b64encode(
                    Path(row["image"]).read_bytes()
                ).decode(),
            }
            try:
                result = call_endpoint(endpoint, payload, key)
            except Exception:
                # Remote bodies and exception strings can echo secrets. Never expose them.
                result = {"status": "CLIENT_ERROR"}
            output = result.get("output")
            if isinstance(output, list):
                output = output[-1] if output else None
            if (
                result.get("status") != "COMPLETED"
                or job_errors(result)
                or not isinstance(output, dict)
            ):
                # A failed call counts as a failed page, not a crashed evaluation.
                failures.append(f"{row['id']}: {result.get('status')}")
                write_json(
                    destination / f"{row['id']}.json",
                    {"_invalid": True, "raw": None, "status": result.get("status")},
                )
                continue
            executions.append(milliseconds(result, "executionTime"))
            delays.append(milliseconds(result, "delayTime"))
            worker_timing = output.get("timing") or {}
            cold_starts += bool(worker_timing.get("cold_start"))
            if worker_timing.get("cold_start"):
                loads.append(float(worker_timing.get("engine_load_s") or 0))
            gpu = output.get("gpu") or gpu
            model = output.get("model") or model
            raw = output.get("raw")
            # Preserve the uncorrected extraction; the report separately applies balance checks.
            try:
                if output.get("valid") is False:
                    raise ValueError("Worker rejected extraction")
                answer = raw if raw is not None else json.dumps(output.get("document"))
                prediction = json.loads(parse_prediction(answer, row["doc_type"]))
            except (ValueError, TypeError, AttributeError):
                prediction = {"_invalid": True, "raw": raw}
            write_json(destination / f"{row['id']}.json", prediction)
        timing = {
            "pages": len(rows),
            "page_ids": [row["id"] for row in rows],
            "seconds_per_page": sum(executions) / len(executions)
            if executions
            else None,
            "total_seconds": sum(executions),
            "mean_delay_seconds": sum(delays) / len(delays) if delays else None,
            "failed_calls": failures,
            "cold_start_count": cold_starts,
            "wall_seconds": time.perf_counter() - started,
            "gpu_name": gpu,
            "model": model,
            "load_seconds": sum(loads),
            "max_pixels": DEFAULT_MAX_PIXELS,
        }
        write_json(destination / "_timing.json", timing)
        models[name] = directory
    results = make_report(data, models, prices, out / "eval", sets=["test_phone"])
    report = out / "eval" / "results.md"
    text = report.read_text()
    text = text.replace(
        "Timing includes image loading, batch inference and output writes; model load is recorded separately. Costs use the supplied hourly prices.",
        "Seconds/page and costs use Runpod executionTime, including cold jobs, excluding queue delay and client wall time. Mean delay, cold-start count and wall time are recorded separately. Costs use the supplied hourly prices.",
    )
    report.write_text(text)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--synth", type=Path, required=True)
    prep.add_argument("--out", type=Path, required=True)
    prep.add_argument("--n", type=int, default=20)
    prep.add_argument("--seed", type=int, default=7)
    pair = commands.add_parser("ingest")
    pair.add_argument("--phone", type=Path, required=True)
    pair.add_argument("--photos", type=Path, required=True)
    pair.add_argument("--out", type=Path, required=True)
    evaluate = commands.add_parser("score")
    evaluate.add_argument("--data", type=Path, required=True)
    evaluate.add_argument("--endpoint", action="append", required=True)
    evaluate.add_argument("--price", action="append", required=True)
    evaluate.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            prepare(args.synth, args.out, args.n, args.seed)
        elif args.command == "ingest":
            ingest(args.phone, args.photos, args.out)
        else:
            score(
                args.data,
                assignments(args.endpoint),
                {name: float(value) for name, value in assignments(args.price).items()},
                args.out,
            )
    except (OSError, ValueError, TypeError, KeyError, RenderError) as error:
        parser.exit(1, f"{args.command} failed: {error}\n")


if __name__ == "__main__":
    main()
