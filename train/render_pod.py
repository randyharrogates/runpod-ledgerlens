"""Render no-render datagen output with Linux Playwright on the Pod.

Pages are split across several browser processes, so the (billed) Pod spends minutes, not half an
hour, on rendering.
"""

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image

from ledgerlens.datagen.degrade import degrade


def render_chunk(data: Path, page_ids: list[str]) -> int:
    """Render and degrade one chunk of pages in its own browser; return how many were rendered."""
    from playwright.sync_api import sync_playwright

    rendered = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(
            viewport={"width": 1240, "height": 1754}, device_scale_factor=1
        )
        for page_id in page_ids:
            image_path = data / f"{page_id}.png"
            metadata_path = data / f"{page_id}.meta.json"
            metadata = json.loads(metadata_path.read_text())
            if metadata["rendered"] and image_path.exists():
                continue
            page.goto((data / f"{page_id}.html").resolve().as_uri())
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(image_path))
            with Image.open(image_path) as image:
                degraded, details = degrade(
                    image, metadata["strength"], metadata["seed"]
                )
            degraded.save(image_path)
            metadata.update(rendered=True, degradation=details)
            metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
            rendered += 1
        browser.close()
    return rendered


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()

    # splits.json maps page id -> metadata; sorted ids give every worker a stable share.
    pages = sorted(json.loads((args.data / "splits.json").read_text())["pages"])
    chunks = [pages[i :: args.workers] for i in range(args.workers)]
    with ProcessPoolExecutor(args.workers) as pool:
        counts = list(pool.map(render_chunk, [args.data] * len(chunks), chunks))
    print(f"Rendered {sum(counts)} of {len(pages)} pages with {args.workers} workers")


if __name__ == "__main__":
    main()
