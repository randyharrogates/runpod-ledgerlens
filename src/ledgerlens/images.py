"""Image preprocessing shared by training, batch prediction and the live worker.

All three must shrink pages the same way, or the fine-tuned model sees different inputs in
production than it saw in training.
"""

import math

from PIL import Image

# About 2 MP: an A4 page at 150 dpi (1240 x 1754) passes almost untouched, so small statement text
# stays legible. Phone photos (12 MP) are scaled down to this. ~2,000 visual tokens on Qwen3-VL.
DEFAULT_MAX_PIXELS = 2_000_000


def resize_image(
    image: Image.Image, max_pixels: int = DEFAULT_MAX_PIXELS
) -> Image.Image:
    """Convert to RGB and scale down, keeping the aspect ratio, to at most max_pixels."""
    if max_pixels <= 0:
        raise ValueError("max_pixels must be positive")
    image = image.convert("RGB")
    width, height = image.size
    if width * height <= max_pixels:
        return image
    scale = math.sqrt(max_pixels / (width * height))
    size = (max(1, int(width * scale)), max(1, int(height * scale)))
    # Extremely thin images can round one dimension up to one pixel.
    if size[0] * size[1] > max_pixels:
        size = (
            min(size[0], max_pixels),
            min(size[1], max_pixels // min(size[0], max_pixels)),
        )
    return image.resize(size, Image.Resampling.LANCZOS)
