"""Environment check: verifies everything the renderer needs.

Run before the first video:
    python scripts/check_env.py
    python scripts/check_env.py --network    (also probes Edge TTS reachability)

Exit code 0 means the core pipeline can run. Warnings do not fail the check.
"""

from __future__ import annotations

import argparse
import sys as _sys

try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RESULTS: list[tuple[str, str, str]] = []  # (status, name, detail)


def record(status: str, name: str, detail: str = "") -> None:
    RESULTS.append((status, name, detail))


def check_python() -> None:
    version = sys.version_info
    if version >= (3, 10):
        record("PASS", "Python version", f"{version.major}.{version.minor}.{version.micro}")
    else:
        record("FAIL", "Python version", f"{version.major}.{version.minor} (need 3.10+)")


def check_packages() -> None:
    packages = {
        "flask": "Flask",
        "PIL": "Pillow",
        "requests": "requests",
        "imageio_ffmpeg": "imageio-ffmpeg",
        "mutagen": "mutagen",
        "edge_tts": "edge-tts",
        "dotenv": "python-dotenv",
    }
    from importlib.metadata import PackageNotFoundError, version as package_version

    for module, label in packages.items():
        try:
            __import__(module)
        except Exception as exc:
            record("FAIL", f"Package: {label}", f"missing ({exc})")
            continue
        try:
            installed = package_version(label)
        except PackageNotFoundError:
            installed = "installed"
        record("PASS", f"Package: {label}", installed)
    try:
        __import__("pytest")
        record("PASS", "Package: pytest", package_version("pytest"))
    except Exception:
        record("WARN", "Package: pytest", "not installed (tests will not run)")


def check_ffmpeg() -> None:
    try:
        from services import ffmpeg_tools

        binary = ffmpeg_tools.ffmpeg_path()
        record("PASS", "FFmpeg binary", binary)
        record("PASS", "FFmpeg version", ffmpeg_tools.version())
        for feature in ("zoompan", "xfade", "sidechaincompress", "loudnorm", "alimiter"):
            record("PASS" if ffmpeg_tools.has_filter(feature) else "FAIL",
                   f"FFmpeg filter: {feature}")
        probe = ffmpeg_tools.ffprobe_path()
        record("PASS" if probe else "WARN", "ffprobe",
               probe or "not found — ffmpeg -i parsing will be used instead")
        free = ffmpeg_tools.disk_free_mb(Path.cwd())
        record("PASS" if free > 500 else "WARN", "Free disk space", f"{free} MB")
    except Exception as exc:
        record("FAIL", "FFmpeg", str(exc))


def check_fonts() -> None:
    try:
        from PIL import ImageFont

        from services.video import _font_path

        path = _font_path("bold")
        if not path:
            record("FAIL", "Video fonts", "no usable TTF found")
            return
        font = ImageFont.truetype(path, 40)
        font.getbbox("Aa")
        record("PASS", "Video fonts", Path(path).name)
    except Exception as exc:
        record("FAIL", "Video fonts", str(exc))


def check_voices() -> None:
    try:
        from config.providers import sapi_voices

        voices = sapi_voices()
        record("PASS" if voices else "WARN", "Windows offline voices",
               f"{len(voices)} installed" if voices else "none found (Edge still works)")
    except Exception as exc:
        record("WARN", "Windows offline voices", str(exc))


def check_directories() -> None:
    from services.storage import ensure_dirs

    try:
        ensure_dirs()
        record("PASS", "Working directories", "uploads, music_library, generated_videos")
    except Exception as exc:
        record("FAIL", "Working directories", str(exc))


def check_settings() -> None:
    try:
        from config.settings import store

        public = store.public()
        record("PASS", "Settings store", f"LLM={public['llm']['provider']}, "
                                          f"TTS={public['tts']['engine']}")
        for name, status in store.all_secret_status().items():
            if status["configured"]:
                record("PASS", f"Credential: {status['label']}",
                       f"{status['masked']} ({status['source']})")
            else:
                record("INFO", f"Credential: {status['label']}", "not configured")
    except Exception as exc:
        record("FAIL", "Settings store", str(exc))


def check_sample_data() -> None:
    from services.storage import SAMPLE_DIR

    json_path = SAMPLE_DIR / "property_sample.json"
    images = sorted((SAMPLE_DIR / "images").glob("*.jpg")) if (SAMPLE_DIR / "images").is_dir() else []
    record("PASS" if json_path.is_file() else "FAIL", "Sample property JSON", str(json_path.name))
    record("PASS" if len(images) >= 4 else "FAIL", "Sample images", f"{len(images)} found")


def check_network() -> None:
    try:
        import requests

        response = requests.get(
            "https://speech.platform.bing.com/consumer/speech/synthesize/readaloud/voices/list"
            "?trustedclienttoken=6A5AA1D4EAFF4E9FB37E23D68491D6F4",
            timeout=10,
        )
        record("PASS" if response.status_code == 200 else "WARN",
               "Edge TTS reachability", f"HTTP {response.status_code}")
    except Exception as exc:
        record("WARN", "Edge TTS reachability", f"unreachable ({type(exc).__name__})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", action="store_true",
                        help="also test whether the free Edge voice service is reachable")
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    args = parser.parse_args()

    check_python()
    check_packages()
    check_ffmpeg()
    check_fonts()
    check_voices()
    check_directories()
    check_settings()
    check_sample_data()
    if args.network:
        check_network()

    if args.json:
        print(json.dumps([{"status": s, "name": n, "detail": d} for s, n, d in RESULTS], indent=2))
    else:
        print("\nAI Property Video Generator — environment check\n" + "=" * 62)
        for status, name, detail in RESULTS:
            marker = {"PASS": "[ok]  ", "WARN": "[warn]", "FAIL": "[FAIL]", "INFO": "[--]  "}[status]
            print(f"{marker} {name:<32} {detail}")
        failures = [item for item in RESULTS if item[0] == "FAIL"]
        print("=" * 62)
        if failures:
            print(f"{len(failures)} blocking problem(s). Fix the [FAIL] lines above.")
        else:
            print("Core pipeline ready. Optional integrations are reported as [--].")

    return 1 if any(status == "FAIL" for status, _, _ in RESULTS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
