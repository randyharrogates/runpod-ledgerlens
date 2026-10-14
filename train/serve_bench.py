"""Benchmark an OpenAI-compatible vLLM server with concurrent page extraction."""

import argparse
import asyncio
import base64
import io
import json
import math
import statistics
import time
from pathlib import Path

import httpx
from PIL import Image

from ledgerlens.schema import json_schema
from train.common import DEFAULT_MAX_PIXELS, build_messages, read_rows, resize_image
from train.predict import parse_prediction


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


async def run(args, transport=None):
    """Write report-compatible predictions; transport permits CPU-only mock tests."""
    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/") + "/",
        timeout=args.timeout,
        transport=transport,
    ) as client:
        for split in args.sets.split(","):
            rows = read_rows(args.data, split)
            if args.limit is not None:
                rows = rows[: args.limit]
            destination = args.out / split
            destination.mkdir(parents=True, exist_ok=True)
            started = time.perf_counter()

            async def page(row):
                async with semaphore:
                    latency = None
                    prompt_tokens = completion_tokens = 0
                    error_count = invalid = 0
                    try:
                        with Image.open(row["image"]) as source:
                            if source.width * source.height > 60_000_000:
                                raise ValueError("Image exceeds 60 million pixels")
                            image = resize_image(source, args.max_pixels)
                        buffer = io.BytesIO()
                        image.save(buffer, format="PNG")
                        url = "data:image/png;base64," + base64.b64encode(
                            buffer.getvalue()
                        ).decode("ascii")
                        messages = build_messages(row["doc_type"], image)
                        messages[1]["content"][1] = {
                            "type": "image_url",
                            "image_url": {"url": url},
                        }
                        request_started = time.perf_counter()
                        try:
                            response = await client.post(
                                "chat/completions",
                                json={
                                    "model": args.model,
                                    "messages": messages,
                                    "temperature": 0,
                                    "max_tokens": args.max_tokens,
                                    "response_format": {
                                        "type": "json_schema",
                                        "json_schema": {
                                            "name": row["doc_type"],
                                            "schema": json_schema(row["doc_type"]),
                                        },
                                    },
                                },
                            )
                            response.raise_for_status()
                        finally:
                            latency = time.perf_counter() - request_started
                        answer = response.json()
                        usage = answer.get("usage") or {}
                        prompt_tokens = usage.get("prompt_tokens") or 0
                        completion_tokens = usage.get("completion_tokens") or 0
                        raw = answer["choices"][0]["message"]["content"]
                        try:
                            text = parse_prediction(raw, row["doc_type"])
                        except (ValueError, TypeError, AttributeError):
                            invalid = 1
                            text = json.dumps(
                                {"_invalid": True, "raw": raw}, ensure_ascii=False
                            )
                    except (
                        httpx.HTTPError,
                        OSError,
                        ValueError,
                        TypeError,
                        KeyError,
                        IndexError,
                        Image.DecompressionBombError,
                    ) as error:
                        invalid = error_count = 1
                        text = json.dumps(
                            {
                                "_invalid": True,
                                "error": str(error) or type(error).__name__,
                            }
                        )
                    (destination / f"{row['id']}.json").write_text(text)
                    return {
                        "id": row["id"],
                        "latency_seconds": latency,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "invalid": invalid,
                        "errors": error_count,
                    }

            # Tasks enter the semaphore in file order and write immediately on completion.
            results = await asyncio.gather(*(page(row) for row in rows))
            seconds = time.perf_counter() - started
            latencies = [
                result["latency_seconds"]
                for result in results
                if result["latency_seconds"] is not None
            ]
            tokens = sum(result["completion_tokens"] for result in results)
            timing = {
                "total_seconds": seconds,
                "pages": len(rows),
                "seconds_per_page": seconds / len(rows) if rows else None,
                "concurrency": args.concurrency,
                "model": args.model,
                "base_url": args.base_url,
                "label": args.label,
                "latency_seconds": {
                    "p50": percentile(latencies, 0.5),
                    "p90": percentile(latencies, 0.9),
                    "max": max(latencies) if latencies else None,
                    "mean": statistics.mean(latencies) if latencies else None,
                },
                "completion_tokens": {
                    "mean": tokens / len(rows) if rows else None,
                    "total": tokens,
                },
                "output_tokens_per_second": tokens / seconds if seconds else 0,
                "invalid": sum(result["invalid"] for result in results),
                "errors": sum(result["errors"] for result in results),
                "max_pixels": args.max_pixels,
                "page_ids": [row["id"] for row in rows],
                "requests": results,
            }
            (destination / "_timing.json").write_text(json.dumps(timing, indent=2))
            print(
                f"{split}: {len(rows)} pages in {seconds:.2f}s, "
                f"{timing['output_tokens_per_second']:.2f} output tokens/s, "
                f"{timing['invalid']} invalid, {timing['errors']} errors"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--sets", default="test_unseen_layout")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-pixels", type=int, default=DEFAULT_MAX_PIXELS)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    for name in ("limit", "concurrency", "max_pixels", "max_tokens", "timeout"):
        value = getattr(args, name)
        if value is not None and (value <= 0 or not math.isfinite(value)):
            parser.error(f"--{name.replace('_', '-')} must be positive and finite")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
