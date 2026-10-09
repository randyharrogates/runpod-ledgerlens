"""Offline vLLM extraction with the same prompt and image cap as SFT."""

import argparse
import json
import logging
import time
from pathlib import Path

from ledgerlens.schema import json_schema
from train.common import (
    DEFAULT_MAX_PIXELS,
    build_messages,
    canonical_target,
    read_rows,
    resize_image,
)


def structured_params(schema):
    try:
        from vllm.sampling_params import StructuredOutputsParams
    except ImportError:
        from vllm.sampling_params import GuidedDecodingParams

        return {"guided_decoding": GuidedDecodingParams(json=schema)}
    return {"structured_outputs": StructuredOutputsParams(json=schema)}


def parse_prediction(raw, doc_type):
    """Canonical JSON for one model answer; raises ValueError/TypeError if it is not valid."""
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise TypeError("Answer is not a JSON object")
    # doc_type has a schema default, so structured decoding may omit it (the base models did in
    # the 9 Oct rehearsal); the worker's pydantic validation fills it the same way.
    parsed.setdefault("doc_type", doc_type)
    if parsed["doc_type"] != doc_type:
        raise ValueError("Wrong document type")
    return canonical_target(parsed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument(
        "--sets", default="test_unseen_layout,test_seen_layout,test_cord"
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-pixels", type=int, default=DEFAULT_MAX_PIXELS)
    parser.add_argument("--max-model-len", type=int, default=8192)
    parser.add_argument("--gpu-mem-util", type=float, default=0.9)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    logging.basicConfig(level=logging.INFO)
    import torch
    from PIL import Image
    from vllm import LLM, SamplingParams

    start = time.perf_counter()
    llm = LLM(
        model=args.model,
        max_model_len=args.max_model_len,
        gpu_memory_utilization=args.gpu_mem_util,
        limit_mm_per_prompt={"image": 1},
        mm_processor_kwargs={"max_pixels": args.max_pixels},
    )
    processor = llm.get_tokenizer()
    load_seconds = time.perf_counter() - start
    for split in args.sets.split(","):
        rows = read_rows(args.data, split)
        if args.limit is not None:
            rows = rows[: args.limit]
        destination = args.out / split
        destination.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        # Bounded batches keep host RAM predictable; vLLM schedules each batch internally.
        for offset in range(0, len(rows), 16):
            group = rows[offset : offset + 16]
            requests, parameters = [], []
            for row in group:
                with Image.open(row["image"]) as image:
                    image = resize_image(image, args.max_pixels)
                messages = build_messages(row["doc_type"], image)
                # The template consumes image markers; PIL pixels go separately to vLLM.
                messages[1]["content"][1] = {"type": "image"}
                prompt = processor.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
                requests.append(
                    {"prompt": prompt, "multi_modal_data": {"image": image}}
                )
                parameters.append(
                    SamplingParams(
                        temperature=0,
                        max_tokens=4096,
                        **structured_params(json_schema(row["doc_type"])),
                    )
                )
            outputs = llm.generate(requests, parameters)
            for row, output in zip(group, outputs, strict=True):
                raw = output.outputs[0].text
                try:
                    text = parse_prediction(raw, row["doc_type"])
                except (ValueError, TypeError, AttributeError) as error:
                    logging.warning("Invalid output %s: %s", row["id"], error)
                    text = json.dumps(
                        {"_invalid": True, "raw": raw}, ensure_ascii=False
                    )
                (destination / f"{row['id']}.json").write_text(text)
            logging.info(
                "%s: %d/%d pages", split, min(offset + 16, len(rows)), len(rows)
            )
        seconds = time.perf_counter() - started
        timing = {
            "total_seconds": seconds,
            "pages": len(rows),
            "seconds_per_page": seconds / len(rows) if rows else None,
            "gpu_name": torch.cuda.get_device_name(),
            "model": args.model,
            "load_seconds": load_seconds,
            "max_pixels": args.max_pixels,
            "page_ids": [row["id"] for row in rows],
        }
        (destination / "_timing.json").write_text(json.dumps(timing, indent=2))


if __name__ == "__main__":
    main()
