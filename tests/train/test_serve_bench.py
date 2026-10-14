"""CPU-only contracts for concurrent OpenAI-compatible page benchmarks."""

import argparse
import asyncio
import base64
import io
import json

import httpx
import pytest
from PIL import Image

from ledgerlens.schema import json_schema
from train.common import build_messages
from train.predict import parse_prediction
from train.serve_bench import run


@pytest.fixture
def dataset(tmp_path):
    rows = []
    for index, doc_type in enumerate(("statement", "invoice", "statement")):
        image = tmp_path / f"page-{index}.png"
        Image.new("RGB", (20, 10), (index, 0, 0)).save(image)
        rows.append({"id": f"page-{index}", "image": str(image), "doc_type": doc_type})
    (tmp_path / "test_unseen_layout.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows)
    )
    return argparse.Namespace(
        base_url="http://test/v1",
        model="fine-tuned",
        data=tmp_path,
        sets="test_unseen_layout",
        limit=None,
        concurrency=2,
        out=tmp_path / "out",
        max_pixels=100,
        max_tokens=4096,
        timeout=600,
        label="LoRA comparison",
    )


def valid_answer(doc_type):
    if doc_type == "invoice":
        return json.dumps(
            dict(
                issuer="Fable Trading",
                recipient="Willow Works",
                invoice_number="INV-1",
                date="2026-01-01",
                currency="SGD",
                line_items=[],
                subtotal="0",
                tax="0",
                total="0",
            )
        )
    return json.dumps(
        dict(
            bank_name="Fable Bank",
            account_holder="Willow Works",
            account_number_masked="**1234",
            period_start="2026-01-01",
            period_end="2026-01-31",
            currency="SGD",
            opening_balance="0",
            closing_balance="0",
            transactions=[],
        )
    )


@pytest.mark.parametrize("failure", ["mixed", "timeout", "none"])
def test_requests_outputs_and_concurrency(dataset, failure, capsys):
    submitted = []
    completed = []
    active = maximum = 0

    async def respond(request):
        nonlocal active, maximum
        body = json.loads(request.content)
        index = len(submitted)
        submitted.append(index)
        doc_type = ("statement", "invoice", "statement")[index]
        assert request.url.path == "/v1/chat/completions"
        assert body["model"] == dataset.model
        assert body["temperature"] == 0
        assert body["max_tokens"] == dataset.max_tokens
        assert body["response_format"] == {
            "type": "json_schema",
            "json_schema": {"name": doc_type, "schema": json_schema(doc_type)},
        }
        assert body["messages"][0] == build_messages(doc_type, None)[0]
        content = body["messages"][1]["content"]
        assert content[0] == {"type": "text", "text": f"Transcribe this {doc_type}."}
        assert content[1]["type"] == "image_url"
        url = content[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
            assert image.format == "PNG"
            assert image.width * image.height <= dataset.max_pixels
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.03 if index == 0 else 0.001)
            if index == 2:
                # The faster second page was persisted while the first was still running.
                assert (dataset.out / dataset.sets / "page-1.json").exists()
            completed.append(index)
            if failure == "mixed" and index == 2:
                return httpx.Response(500, text="server failure")
            if failure == "timeout" and index == 2:
                raise httpx.ReadTimeout("page timed out", request=request)
            raw = (
                "not JSON"
                if failure == "mixed" and index == 1
                else valid_answer(doc_type)
            )
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": raw}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10},
                },
            )
        finally:
            active -= 1

    asyncio.run(run(dataset, httpx.MockTransport(respond)))
    assert submitted == [0, 1, 2]
    assert completed[0] == 1
    assert maximum == 2
    directory = dataset.out / dataset.sets
    for index, doc_type in enumerate(("statement", "invoice", "statement")):
        text = (directory / f"page-{index}.json").read_text()
        if failure == "mixed" and index == 1:
            assert json.loads(text) == {"_invalid": True, "raw": "not JSON"}
        elif failure != "none" and index == 2:
            result = json.loads(text)
            assert result["_invalid"] is True
            assert (
                "500" in result["error"]
                if failure == "mixed"
                else "timed out" in result["error"]
            )
        else:
            assert text == parse_prediction(valid_answer(doc_type), doc_type)
    timing = json.loads((directory / "_timing.json").read_text())
    assert timing["pages"] == 3
    assert timing["page_ids"] == [f"page-{index}" for index in range(3)]
    assert timing["total_seconds"] > 0
    assert timing["seconds_per_page"] == pytest.approx(timing["total_seconds"] / 3)
    for field in ("model", "base_url", "label", "concurrency", "max_pixels"):
        assert timing[field] == getattr(dataset, field)
    latencies = timing["latency_seconds"]
    assert 0 <= latencies["p50"] <= latencies["p90"] <= latencies["max"]
    assert 0 < latencies["mean"] <= latencies["max"]
    tokens = 30 if failure == "none" else 20
    assert timing["completion_tokens"] == {"mean": tokens / 3, "total": tokens}
    assert timing["output_tokens_per_second"] == pytest.approx(
        tokens / timing["total_seconds"]
    )
    assert timing["invalid"] == {"none": 0, "mixed": 2, "timeout": 1}[failure]
    assert timing["errors"] == (failure != "none")
    assert timing["requests"][0]["prompt_tokens"] == 100
    assert timing["requests"][0]["completion_tokens"] == 10
    assert timing["requests"][0]["latency_seconds"] > 0
    assert capsys.readouterr().out.count("test_unseen_layout:") == 1


def test_empty_split(dataset):
    (dataset.data / "test_unseen_layout.jsonl").write_text("")
    asyncio.run(
        run(
            dataset,
            httpx.MockTransport(lambda request: pytest.fail("Unexpected request")),
        )
    )
    timing = json.loads((dataset.out / dataset.sets / "_timing.json").read_text())
    assert timing["pages"] == 0
    assert timing["seconds_per_page"] is None
    assert timing["completion_tokens"] == {"mean": None, "total": 0}
    assert timing["latency_seconds"]["p50"] is None


def test_limit_preserves_file_order(dataset):
    dataset.limit = 1
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": valid_answer("statement")}}]},
        )

    asyncio.run(run(dataset, httpx.MockTransport(respond)))
    directory = dataset.out / dataset.sets
    timing = json.loads((directory / "_timing.json").read_text())
    assert len(requests) == 1
    assert timing["pages"] == 1
    assert timing["page_ids"] == ["page-0"]
    assert not (directory / "page-1.json").exists()
    assert timing["completion_tokens"]["total"] == 0
