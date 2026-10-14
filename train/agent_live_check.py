"""Run the worker's chat agent against a live vLLM server and record every event with timings.

Start vLLM first (see train/agent_live_check.sh), then:
  LEDGERLENS_NO_ENGINE=1 PYTHONPATH=src:. python -m train.agent_live_check --out DIR
"""

import argparse
import asyncio
import base64
import io
import json
import time
from pathlib import Path

from PIL import Image, ImageDraw

from worker import agent

SAMPLE = Path("app/sample")


def page(name):
    return base64.b64encode((SAMPLE / name).read_bytes()).decode()


def not_a_document():
    image = Image.new("RGB", (1200, 900), (120, 170, 220))
    draw = ImageDraw.Draw(image)
    draw.ellipse((850, 80, 1050, 280), fill=(250, 220, 90))
    draw.polygon([(0, 900), (420, 380), (840, 900)], fill=(60, 120, 70))
    draw.polygon([(500, 900), (900, 300), (1200, 700), (1200, 900)], fill=(40, 100, 60))
    draw.text((60, 60), "Weekend hike, Bukit Timah", fill=(20, 20, 20))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


async def run(name, runner, data, out):
    engine = agent.Engine()
    events = []
    started = time.monotonic()
    first_text = None
    async with engine:
        async for event in runner(data, engine):
            elapsed = round(time.monotonic() - started, 3)
            if event["type"] == "deltas" and first_text is None:
                first_text = elapsed
            events.append({"t": elapsed, **event})
    final = next((e for e in reversed(events) if e["type"] in ("final", "error")), {})
    summary = {
        "scenario": name,
        "wall_s": round(time.monotonic() - started, 3),
        "first_text_s": first_text,
        "intent": final.get("intent"),
        "error": final.get("message") if final.get("type") == "error" else None,
        "problems": final.get("problems"),
        "usage": final.get("usage"),
        "deltas_events": sum(e["type"] == "deltas" for e in events),
    }
    (out / f"{name}.json").write_text(json.dumps(events, indent=2, default=str))
    print(json.dumps(summary), flush=True)
    return final


async def main(out):
    out.mkdir(parents=True, exist_ok=True)
    pages = [
        {"page_id": "p1", "image_base64": page("biz_0000_p001.png")},
        {"page_id": "p2", "image_base64": page("biz_0000_p002.png")},
        {"page_id": "inv1", "image_base64": page("biz_0000_inv1.png")},
        {"page_id": "x1", "image_base64": not_a_document()},
    ]
    read = await run("read", agent.run_read, {"pages": pages}, out)
    documents = read.get("documents") or []
    memo = await run(
        "memo_preread",
        agent.run_agent,
        {"message": "Write a credit memo for the committee.", "documents": documents},
        out,
    )
    await run(
        "memo_new_pages",
        agent.run_agent,
        {"message": "Write a credit memo for the committee.", "new_pages": pages[:2]},
        out,
    )
    await run(
        "ask_data",
        agent.run_agent,
        {
            "message": "Who is the largest payer, and what share of money in do they make up?",
            "documents": documents,
        },
        out,
    )
    await run(
        "ask_data_month",
        agent.run_agent,
        {
            "message": "Which month had the lowest net cash flow?",
            "documents": documents,
        },
        out,
    )
    await run(
        "edit_memo",
        agent.run_agent,
        {
            "message": "Make the red flags section shorter.",
            "documents": documents,
            "last_memo": memo.get("memo") or {},
        },
        out,
    )
    await run(
        "knowledge",
        agent.run_agent,
        {"message": "What does a returned cheque usually tell a lender?"},
        out,
    )
    await run(
        "needs_documents",
        agent.run_agent,
        {"message": "Write a credit memo for this business."},
        out,
    )
    await run(
        "off_topic",
        agent.run_agent,
        {"message": "Write me a poem about cats.", "documents": documents},
        out,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    asyncio.run(main(parser.parse_args().out))
