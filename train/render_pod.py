"""Render no-render datagen output with Linux Playwright on the Pod."""

import argparse
import json
from pathlib import Path

from PIL import Image

from ledgerlens.datagen.degrade import degrade


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright

    manifest = json.loads((args.data / "splits.json").read_text())
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(
            viewport={"width": 1240, "height": 1754}, device_scale_factor=1
        )
        for index, page_id in enumerate(manifest["pages"]):
            image_path = args.data / f"{page_id}.png"
            metadata_path = args.data / f"{page_id}.meta.json"
            metadata = json.loads(metadata_path.read_text())
            if metadata["rendered"] and image_path.exists():
                continue
            page.goto((args.data / f"{page_id}.html").resolve().as_uri())
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(image_path))
            with Image.open(image_path) as image:
                degraded, details = degrade(
                    image, metadata["strength"], metadata["seed"]
                )
            degraded.save(image_path)
            metadata.update(rendered=True, degradation=details)
            metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
            print(
                f"Rendered {index + 1}/{len(manifest['pages'])}: {page_id}", flush=True
            )
        browser.close()


if __name__ == "__main__":
    main()
