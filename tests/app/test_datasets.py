"""Upload validation and isolated dataset browsing."""

import io
import json
import stat
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.backend import datasets, main, training


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(datasets, "ROOT", tmp_path / "uploads")
    monkeypatch.setattr(training, "ROOT", tmp_path / "runs")
    return TestClient(main.app)


def files():
    root = Path("app/sample/dataset")
    meta = next(root.glob("*.meta.json"))
    identity = meta.name.removesuffix(".meta.json")
    return {
        "page" + suffix: (root / (identity + suffix)).read_bytes()
        for suffix in (".json", ".meta.json", ".html")
    }


def archive(entries, compression=zipfile.ZIP_STORED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as zipped:
        for name, data in entries.items():
            zipped.writestr(name, data)
    return stream.getvalue()


def send(client, entries):
    return client.post(
        "/api/datasets",
        files={"file": ("dataset.zip", archive(entries), "application/zip")},
    )


@pytest.mark.parametrize(
    "name",
    [
        "../outside.json",
        "/outside.json",
        "a/b/c.json",
        "x\\y.json",
        "bad-id.json",
        "extra.txt",
    ],
)
def test_unsafe_paths(client, name):
    response = send(client, {**files(), name: b"{}"})
    assert response.status_code == 422
    assert response.json()["detail"]["problems"]
    assert not datasets.ROOT.exists()


def test_symlink_and_bomb(client):
    symlink = zipfile.ZipInfo("evil.json")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    response = client.post(
        "/api/datasets",
        files={"file": ("data.zip", archive({symlink: b"/etc/passwd"}))},
    )
    assert response.status_code == 422
    data = archive({"page.html": b"a" * 100_000}, zipfile.ZIP_DEFLATED)
    assert (
        client.post("/api/datasets", files={"file": ("data.zip", data)}).status_code
        == 422
    )
    assert not datasets.ROOT.exists()


@pytest.mark.parametrize("change", ["gold", "meta", "visual", "split", "mixed"])
def test_bad_documents(client, change):
    entries = files()
    if change == "gold":
        entries["page.json"] = b"{}"
    elif change == "meta":
        del entries["page.meta.json"]
    elif change == "visual":
        del entries["page.html"]
    elif change == "split":
        entries["splits.json"] = b'{"train":["page"],"val":["page"],"test":[]}'
    else:
        entries["top/other.html"] = b"bad"
    assert send(client, entries).status_code == 422
    assert not datasets.ROOT.exists()


def test_valid_upload_and_browse(client):
    response = send(client, {"top/" + k: v for k, v in files().items()})
    assert response.status_code == 200
    identity = response.json()["id"]
    folder = datasets.upload_path(identity)
    splits = json.loads((folder / "splits.json").read_text())
    assert sorted(p for s in ("train", "val", "test") for p in splits[s]) == ["page"]
    assert client.get("/api/datasets").json()[0]["validation"]["valid"]
    assert (
        client.get(f"/api/dataset/summary?dataset={identity}").json()["totals"]["pages"]
        == 1
    )
    assert client.get(f"/api/dataset/pages?dataset={identity}").json()["total"] == 1
    assert client.get(f"/api/dataset/pages/page?dataset={identity}").status_code == 200
    preview = client.get(f"/api/dataset/pages/page/preview?dataset={identity}")
    assert "sandbox" in preview.headers["content-security-policy"]
    assert client.get("/api/dataset/pages?dataset=..").status_code == 404
    assert client.delete(f"/api/datasets/{identity}").status_code == 200
    assert client.get("/api/datasets").json() == []
    assert client.delete("/api/datasets/default").status_code == 404


def test_business_splits(client):
    entries = {}
    for i in range(20):
        for name, data in files().items():
            if name.endswith(".meta.json"):
                meta = json.loads(data)
                meta["business_id"] = f"business_{i // 2}"
                data = json.dumps(meta).encode()
            entries[name.replace("page", f"p{i}")] = data
    response = send(client, entries)
    assert response.status_code == 200
    root = datasets.upload_path(response.json()["id"])
    memberships = {}
    splits = json.loads((root / "splits.json").read_text())
    for split in ("train", "val", "test"):
        values = splits[split]
        for p in values:
            business = json.loads((root / f"{p}.meta.json").read_text())["business_id"]
            assert business not in memberships or memberships[business] == split
            memberships[business] = split
    assert {s: len(splits[s]) for s in ("train", "val", "test")} == {
        "train": 16,
        "val": 2,
        "test": 2,
    }


def test_image_dimensions_before_verify(client, monkeypatch):
    entries = files()
    entries["page.png"] = b"image"

    class Huge:
        width, height = 100_000, 100_000

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def verify(self):
            pytest.fail("Huge image must not be decoded")

    monkeypatch.setattr(datasets.Image, "open", lambda _: Huge())
    assert send(client, entries).status_code == 422


def test_training_manifest_and_image_normalization(client):
    from PIL import Image

    entries = files()
    del entries["page.html"]
    image = io.BytesIO()
    Image.new("RGB", (10, 10)).save(image, format="JPEG")
    entries["page.jpg"] = image.getvalue()
    response = send(client, entries)
    assert response.status_code == 200
    root = datasets.upload_path(response.json()["id"])
    assert (root / "page.png").read_bytes().startswith(b"\x89PNG")
    meta = json.loads((root / "page.meta.json").read_text())
    assert meta["rendered"] and meta["seed"] == 7
    manifest = json.loads((root / "splits.json").read_text())
    assert manifest["pages"]["page"]["business_id"] == meta["business_id"]
    assert "business_test_sets" in manifest
    assert (
        client.get(
            f"/api/dataset/pages/page/preview?dataset={response.json()['id']}"
        ).status_code
        == 200
    )
