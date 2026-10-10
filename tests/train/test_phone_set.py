"""Offline phone workflow: clean prints, camera pairing and Serverless scoring."""

import json
from collections import Counter

import pytest
from PIL import Image

from ledgerlens.datagen.cli import generate_dataset
from ledgerlens.schema import DOCUMENT_ADAPTER
from train import phone_set
from train.common import read_rows


@pytest.fixture
def phone(tmp_path, monkeypatch):
    synth = tmp_path / "synth"
    generate_dataset(synth, 12, 7, no_render=True)

    def render(html, png):
        assert html.suffix == ".html"
        Image.new("RGB", (40, 60), "white").save(png)

    monkeypatch.setattr(phone_set, "render_png", render)
    directory = tmp_path / "phone"
    pages = phone_set.prepare(synth, directory)
    return directory, synth, pages


def photos_for(tmp_path, pages):
    directory = tmp_path / "photos"
    directory.mkdir()
    for index in range(len(pages)):
        exif = Image.Exif()
        exif[36867] = f"2026:10:10 12:{index:02d}:00"
        exif[274] = 6
        # Reverse names so lexical order disagrees with camera order.
        Image.new("RGB", (20, 10), (index * 10, 0, 0)).save(
            directory / f"{len(pages) - index:02d}.jpg", exif=exif
        )
    return directory


def test_selection_and_print(phone):
    directory, synth, pages = phone
    assert pages == phone_set.select_pages(synth)
    assert pages != phone_set.select_pages(synth, seed=8)
    buckets = Counter(
        "invoice" if page["doc_type"] == "invoice" else page["test_set"]
        for page in pages
    )
    assert buckets == {"test_unseen_layout": 10, "test_seen_layout": 6, "invoice": 4}
    assert all(a["business_id"] != b["business_id"] for a, b in zip(pages, pages[1:]))
    assert len({page["layout"] for page in pages}) >= 3
    assert {page["currency"] for page in pages} == {"SGD", "MYR", "IDR"}
    businesses = Counter(page["business_id"] for page in pages)
    assert max(businesses.values()) - min(businesses.values()) <= 2
    invoice_layouts = Counter(
        page["layout"] for page in pages if page["doc_type"] == "invoice"
    )
    assert invoice_layouts == {"invoice_a": 2, "invoice_b": 2}
    splits = json.loads((synth / "splits.json").read_text())
    assert {page["page_id"] for page in pages} <= set(splits["test"])
    assert len({page["page_id"] for page in pages}) == 20
    assert (directory / "print.pdf").read_bytes().startswith(b"%PDF")
    assert len(list((directory / "clean").glob("*.png"))) == 20
    assert len(phone_set.select_pages(synth, n=5)) == 5
    with pytest.raises(ValueError, match="positive"):
        phone_set.select_pages(synth, n=0)
    with pytest.raises(ValueError, match="available"):
        phone_set.select_pages(synth, n=10000)


def test_ingest(phone, tmp_path):
    directory, synth, pages = phone
    photos = photos_for(tmp_path, pages)
    out = tmp_path / "data"
    rows = phone_set.ingest(directory, photos, out)
    assert rows == read_rows(out, "test_phone")
    assert json.loads((out / "synth_source.json").read_text())["path"] == str(synth)
    for index, row in enumerate(rows):
        DOCUMENT_ADAPTER.validate_json(row["target"])
        assert row["id"] == pages[index]["page_id"]
        with Image.open(row["image"]) as image:
            assert image.size == (10, 20)
            assert abs(image.getpixel((5, 5))[0] - index * 10) < 4
    contact = (out / "pairs.html").read_text()
    assert "20.jpg" in contact and "Clean page" in contact


def test_reject_photos(phone, tmp_path):
    directory, _, pages = phone
    photos = photos_for(tmp_path, pages)
    heic = photos / "extra.HEIC"
    heic.write_bytes(b"not decoded")
    with pytest.raises(ValueError, match="HEIC is not supported; convert to JPEG"):
        phone_set.ingest(directory, photos, tmp_path / "data")
    heic.unlink()
    next(photos.iterdir()).unlink()
    with pytest.raises(ValueError, match="Photo count 19.*20"):
        phone_set.ingest(directory, photos, tmp_path / "data")


def test_pixel_check_before_decode(tmp_path, monkeypatch):
    class Header:
        width, height = 10000, 10000
        closed = False

        def close(self):
            self.closed = True

    header = Header()
    monkeypatch.setattr(phone_set.Image, "open", lambda path: header)
    with pytest.raises(ValueError, match="60,000,000"):
        phone_set.checked_image(tmp_path / "huge.jpg")
    assert header.closed


def test_filename_fallback(tmp_path):
    for name in ("b.png", "a.png"):
        Image.new("RGB", (4, 4)).save(tmp_path / name)
    assert [
        p.name for p in sorted(tmp_path.glob("*.png"), key=phone_set.photo_time)
    ] == ["a.png", "b.png"]


def test_score(phone, tmp_path, monkeypatch):
    directory, _, pages = phone
    data = tmp_path / "data"
    rows = phone_set.ingest(directory, photos_for(tmp_path, pages), data)
    monkeypatch.setenv("RUNPOD_API_KEY", "secret-not-printed")
    calls = []

    def endpoint(endpoint_id, payload, key):
        assert endpoint_id == "endpoint-id" and key == "secret-not-printed"
        assert payload["task"] == "extract" and payload["image_base64"]
        index = len(calls)
        calls.append(payload)
        target = json.loads(rows[index]["target"])
        output = {
            "document": target,
            "valid": index != 1,
            "raw": rows[index]["target"] if index != 1 else "broken",
            "gpu": "fake GPU",
            "model": "fake model",
            "timing": {"cold_start": index == 0, "engine_load_s": 3},
        }
        return {
            "status": "COMPLETED",
            "output": [output],
            "executionTime": 2000,
            "delayTime": 500,
        }

    monkeypatch.setattr(phone_set, "call_endpoint", endpoint)
    out = tmp_path / "score"
    results = phone_set.score(data, {"baseline": "endpoint-id"}, {"baseline": 1.8}, out)
    prediction = out / "predictions" / "baseline" / "test_phone"
    timing = json.loads((prediction / "_timing.json").read_text())
    assert timing["seconds_per_page"] == 2
    assert timing["mean_delay_seconds"] == 0.5
    assert timing["cold_start_count"] == 1
    assert timing["total_seconds"] == 40
    assert timing["wall_seconds"] >= 0
    assert timing["page_ids"] == [row["id"] for row in rows]
    assert json.loads((prediction / f"{rows[1]['id']}.json").read_text())["_invalid"]
    assert results["test_phone"]["baseline"]["cost_per_1000_pages"] == 1
    assert results["test_phone"]["baseline"]["valid_json_rate"] == 0.95
    assert "Runpod executionTime" in (out / "eval" / "results.md").read_text()
    assert (out / "eval" / "results.json").exists()


def test_score_no_secret_in_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUNPOD_API_KEY", "never-show-this")
    data = tmp_path / "data"
    data.mkdir()
    (data / "test_phone.jsonl").write_text(
        json.dumps(
            {
                "id": "p",
                "image": str(tmp_path / "p.jpg"),
                "doc_type": "invoice",
                "target": "{}",
            }
        )
        + "\n"
    )
    (tmp_path / "p.jpg").write_bytes(b"fake")

    def failed(*args):
        raise RuntimeError("never-show-this")

    monkeypatch.setattr(phone_set, "call_endpoint", failed)
    with pytest.raises(SystemExit):
        phone_set.main(
            [
                "score",
                "--data",
                str(data),
                "--endpoint",
                "m=id",
                "--price",
                "m=1",
                "--out",
                str(tmp_path / "out"),
            ]
        )
    assert "never-show-this" not in capsys.readouterr().err
