"""Offline HTML screenshots using a locally installed Chromium binary."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


class RenderError(RuntimeError):
    """Chromium is missing or cannot run in this environment."""


def find_chromium() -> Path | None:
    """Search the Playwright cache first, then the macOS Chrome application."""
    cache = Path.home() / "Library/Caches/ms-playwright"
    for name in ("chrome-headless-shell", "Chromium"):
        if cache.exists():
            for candidate in sorted(cache.rglob(name)):
                if candidate.is_file():
                    return candidate
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return chrome if chrome.is_file() else None


def render_png(html: Path, out: Path, width: int = 1240, height: int = 1754) -> None:
    """Screenshot a local HTML page, failing clearly on sandbox restrictions."""
    binary = find_chromium()
    if binary is None:
        raise RenderError(
            "No local Chromium found; install it outside this offline phase or use --no-render."
        )
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    # A fresh local profile avoids extensions and writes to the user's Chrome profile.
    with TemporaryDirectory(prefix="ledgerlens-render-", dir=out.parent) as temporary:
        temporary_path = Path(temporary)
        screenshot = temporary_path / "page.png"
        command = [
            str(binary),
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={width},{height}",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-sync",
            "--no-pings",
            "--no-first-run",
            "--no-default-browser-check",
            "--host-resolver-rules=MAP * ~NOTFOUND",
            f"--user-data-dir={temporary_path / 'profile'}",
            f"--screenshot={screenshot}",
            html.resolve().as_uri(),
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=45, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RenderError(
                f"Local Chromium failed (possibly blocked by sandbox): {error}"
            ) from error
        if result.returncode or not screenshot.is_file():
            raise RenderError(
                f"Local Chromium failed (possibly blocked by sandbox): {result.stderr[-1500:]}"
            )
        screenshot.replace(out)
