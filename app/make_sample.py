"""Generate the fictional SGD demo with real Chromium-rendered documents."""

import json
import shutil
import tempfile
from pathlib import Path

from PIL import Image

from ledgerlens.datagen.cli import generate_dataset
from ledgerlens.datagen.degrade import degrade
from ledgerlens.datagen.render import RenderError, render_png

OUT = Path(__file__).parent / "sample"
SEED = 4
STATEMENTS = [f"biz_0000_p{i:03d}" for i in range(1, 5)]
SELECTED = [*STATEMENTS, "biz_0000_inv1"]


def main():
    """Publish only after all five pages have rendered successfully."""
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ledgerlens-sample-") as temp:
        source = Path(temp)
        try:
            generate_dataset(source, 1, SEED)
            anomalies = []
            for name, strength in zip(
                SELECTED, ["clean", "light", "medium", "heavy", "clean"], strict=True
            ):
                # Render the pristine HTML again so degradation is applied once.
                render_png(source / f"{name}.html", source / f"{name}.png")
                with Image.open(source / f"{name}.png") as image:
                    rendered, details = degrade(image, strength, SEED)
                rendered.save(source / f"{name}.png")
                metadata = json.loads((source / f"{name}.meta.json").read_text())
                metadata.update(strength=strength, seed=SEED, degradation=details)
                (source / f"{name}.meta.json").write_text(
                    json.dumps(metadata, indent=2, ensure_ascii=False)
                )
                anomalies.extend(metadata["anomalies"])
        except RenderError as error:
            raise SystemExit(
                f"Sample generation requires working headless Chromium: {error}"
            ) from error
        for path in OUT.glob("biz_*.*"):
            path.unlink()
        for name in SELECTED:
            for suffix in ("png", "json", "meta.json"):
                shutil.copyfile(source / f"{name}.{suffix}", OUT / f"{name}.{suffix}")
        (OUT / "manifest.json").write_text(json.dumps(SELECTED, indent=2))
    print(f"Seed {SEED} · SGD · stmt_b (non-held-out)")
    print("Anomalies: " + ", ".join(sorted({a["type"] for a in anomalies})))
    print("Wrote four consecutive statement pages and one invoice to app/sample")


if __name__ == "__main__":
    main()
