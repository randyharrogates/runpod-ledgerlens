"""Call LedgerLens through Runpod REST; credentials come only from the environment."""

import argparse
import base64
import json
import os
import sys
import time
import tomllib
from pathlib import Path

import httpx


def runpodctl_key(path=Path.home() / ".runpod" / "config.toml"):
    """Read the key `runpodctl doctor` saved, so it never has to be typed or exported."""
    try:
        return tomllib.loads(path.read_text()).get("apikey") or None
    except (OSError, tomllib.TOMLDecodeError):
        return None


def call_endpoint(endpoint_id, payload, key, *, stream=False, timeout=900):
    base = f"https://api.runpod.ai/v2/{endpoint_id}"
    deadline = time.monotonic() + timeout
    with httpx.Client(
        headers={"Authorization": f"Bearer {key}"}, timeout=timeout
    ) as client:
        response = client.post(
            base + ("/run" if stream else "/runsync"), json={"input": payload}
        )
        response.raise_for_status()
        result = response.json()
        if not stream and result.get("status") not in ("IN_QUEUE", "IN_PROGRESS"):
            return result
        job_id = result["id"]
        chunks = []
        while time.monotonic() < deadline:
            response = client.get(base + f"/stream/{job_id}")
            response.raise_for_status()
            result = response.json()
            chunks.extend(result.get("stream") or [])
            if result.get("status") in (
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                "TIMED_OUT",
            ):
                return {**result, "stream": chunks}
            time.sleep(1)
    raise TimeoutError("Runpod job did not finish before the client timeout")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="task", required=True)
    extract = commands.add_parser("extract")
    extract.add_argument("--image", type=Path, required=True)
    extract.add_argument(
        "--doc-type", choices=("statement", "invoice", "receipt"), required=True
    )
    memo = commands.add_parser("memo")
    memo.add_argument("--analysis", type=Path, required=True)
    for command in (extract, memo):
        command.add_argument("--endpoint-id", required=True)
        command.add_argument("--max-tokens", type=int)
    args = parser.parse_args(argv)
    key = os.getenv("RUNPOD_API_KEY") or runpodctl_key()
    if not key:
        print(
            "Set RUNPOD_API_KEY or run `runpodctl doctor` to save a key",
            file=sys.stderr,
        )
        return 2
    try:
        payload = {"task": args.task}
        if args.task == "extract":
            payload.update(
                doc_type=args.doc_type,
                image_base64=base64.b64encode(args.image.read_bytes()).decode(),
            )
        else:
            payload["analysis"] = json.loads(args.analysis.read_text())
        if args.max_tokens is not None:
            payload["max_tokens"] = args.max_tokens
        started = time.monotonic()
        result = call_endpoint(
            args.endpoint_id, payload, key, stream=args.task == "memo"
        )
        output = json.dumps(
            {"result": result, "round_trip_s": time.monotonic() - started}, indent=2
        )
        print(output.replace(key, "[REDACTED]"))
        return 0
    except (OSError, ValueError, KeyError, httpx.HTTPError, TimeoutError):
        # Avoid printing server bodies or exception strings that might echo credentials.
        print(
            "Request failed; check input files, endpoint, and credentials",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
