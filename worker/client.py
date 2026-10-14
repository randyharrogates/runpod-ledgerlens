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


def job_errors(result):
    """Collect errors from Runpod jobs and aggregated worker output."""
    if isinstance(result, dict):
        errors = [str(result["error"])] if result.get("error") else []
        for field in ("output", "stream"):
            errors.extend(job_errors(result.get(field)))
        return errors
    if isinstance(result, list):
        return [error for item in result for error in job_errors(item)]
    return []


def call_endpoint(endpoint_id, payload, key, *, stream=False, timeout=900):
    base = f"https://api.runpod.ai/v2/{endpoint_id}"
    deadline = time.monotonic() + timeout
    # Always /run then poll: /runsync holds one connection open for the whole job, and Runpod
    # dropped it mid-job on a cold start (the job still completed). Short requests, many polls.
    with httpx.Client(headers={"Authorization": f"Bearer {key}"}, timeout=30) as client:
        response = client.post(base + "/run", json={"input": payload})
        response.raise_for_status()
        result = response.json()
        job_id = result["id"]
        chunks = []

        def timed_out():
            errors = job_errors({**result, "stream": chunks})
            detail = "; " + "; ".join(errors) if errors else ""
            return TimeoutError(
                f"Runpod job {job_id} still {result.get('status', 'UNKNOWN')} "
                f"after {timeout}s; check with: runpodctl serverless status "
                f"{endpoint_id} {job_id}{detail}"
            )

        while time.monotonic() < deadline:
            try:
                route = "stream" if stream else "status"
                response = client.get(base + f"/{route}/{job_id}")
            except httpx.TransportError:
                # A dropped or slow poll is not a failed job; try again until the deadline.
                time.sleep(1)
                continue
            response.raise_for_status()
            result = response.json()
            chunks.extend(result.get("stream") or [])
            if result.get("status") in (
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                "TIMED_OUT",
            ):
                return (
                    {**result, "id": job_id, "stream": chunks}
                    if stream
                    else {**result, "id": job_id}
                )
            time.sleep(1)
        raise timed_out()


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
    generate = commands.add_parser("generate")
    generate.add_argument("--prompt", required=True)
    generate.add_argument("--image", type=Path)
    generate.add_argument("--system")
    generate.add_argument("--temperature", type=float)
    generate.add_argument("--stream", action="store_true")
    for command in (extract, memo, generate):
        command.add_argument("--endpoint-id", required=True)
        command.add_argument("--max-tokens", type=int)
        command.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args(argv)
    key = os.getenv("RUNPOD_API_KEY") or runpodctl_key()

    def safe_print(message, **kwargs):
        print(message.replace(key, "[REDACTED]") if key else message, **kwargs)

    if not key:
        safe_print(
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
        elif args.task == "memo":
            payload["analysis"] = json.loads(args.analysis.read_text())
        else:
            payload.update(prompt=args.prompt, stream=args.stream)
            for field in ("system", "temperature"):
                if (value := getattr(args, field)) is not None:
                    payload[field] = value
            if args.image:
                payload["image_base64"] = base64.b64encode(
                    args.image.read_bytes()
                ).decode()
        if args.max_tokens is not None:
            payload["max_tokens"] = args.max_tokens
        started = time.monotonic()
        result = call_endpoint(
            args.endpoint_id,
            payload,
            key,
            stream=args.task == "memo" or (args.task == "generate" and args.stream),
            timeout=args.timeout,
        )
        output = json.dumps(
            {"result": result, "round_trip_s": time.monotonic() - started}, indent=2
        )
        safe_print(output)
        failed = result.get("status") in ("FAILED", "CANCELLED", "TIMED_OUT")
        return 1 if failed or job_errors(result) else 0
    except httpx.HTTPStatusError as exc:
        safe_print(
            f"HTTP {exc.response.status_code} for {exc.request.url.path}",
            file=sys.stderr,
        )
        return 1
    except TimeoutError as exc:
        safe_print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, httpx.HTTPError):
        # Avoid printing server bodies or exception strings that might echo credentials.
        safe_print(
            "Request failed; check input files, endpoint, and credentials",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
