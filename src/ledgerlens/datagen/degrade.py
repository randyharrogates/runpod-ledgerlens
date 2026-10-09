"""Deterministic phone-photo-like degradation, using only Pillow and numpy."""

from io import BytesIO
from typing import Literal

import numpy as np
from PIL import Image, ImageFilter

Strength = Literal["clean", "light", "medium", "heavy"]


def degrade(
    image: Image.Image, strength: Strength, seed: int
) -> tuple[Image.Image, dict]:
    """Apply and record seeded tilt, blur, lighting, fold and compression."""
    levels = {"clean": 0, "light": 1, "medium": 2, "heavy": 3}
    if strength not in levels:
        raise ValueError(f"Unknown strength: {strength}")
    level = levels[strength]
    result = image.convert("RGB")
    operations: list[dict] = []
    if not level:
        return result, {"strength": strength, "seed": seed, "operations": operations}
    rng = np.random.default_rng(seed)
    w, h = result.size
    tilt_x, tilt_y = rng.uniform(-0.000012, 0.000012, 2) * level
    coefficients = (
        1,
        float(rng.uniform(-0.015, 0.015) * level),
        0,
        float(rng.uniform(-0.01, 0.01) * level),
        1,
        0,
        float(tilt_x),
        float(tilt_y),
    )
    result = result.transform(
        result.size,
        Image.Transform.PERSPECTIVE,
        coefficients,
        resample=Image.Resampling.BICUBIC,
        fillcolor="white",
    )
    operations.append({"op": "perspective", "coefficients": coefficients})
    angle = float(rng.uniform(-1.2, 1.2) * level)
    result = result.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor="white")
    operations.append({"op": "rotation", "degrees": angle})
    radius = float(rng.uniform(0.25, 0.65) * level)
    result = result.filter(ImageFilter.GaussianBlur(radius))
    operations.append({"op": "gaussian_blur", "radius": radius})
    direction = bool(rng.integers(0, 2))
    gradient = np.linspace(1 - 0.12 * level, 1, w)
    if direction:
        gradient = gradient[::-1]
    arrays = np.asarray(result, dtype=np.float64) * gradient[None, :, None]
    fold = int(rng.integers(max(1, w // 4), max(2, 3 * w // 4)))
    fold_width = max(1, w // 150)
    distance = np.abs(np.arange(w) - fold)
    arrays *= (1 - 0.1 * level * np.exp(-distance / fold_width))[None, :, None]
    result = Image.fromarray(np.clip(arrays, 0, 255).astype(np.uint8))
    operations.extend(
        [
            {"op": "lighting", "minimum": 1 - 0.12 * level, "reversed": direction},
            {"op": "fold", "x": fold, "width": fold_width},
        ]
    )
    scale = [1, 0.88, 0.68, 0.48][level]
    result = result.resize(
        (max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS
    )
    operations.append({"op": "downscale", "scale": scale, "size": result.size})
    quality = [100, 85, 60, 35][level]
    buffer = BytesIO()
    result.save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    with Image.open(buffer) as compressed:
        result = compressed.copy()
    operations.append({"op": "jpeg_recompression", "quality": quality})
    return result, {"strength": strength, "seed": seed, "operations": operations}
