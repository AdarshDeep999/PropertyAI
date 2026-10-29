"""Build the deliverable sample videos with the real production pipeline.

Default run (no API key needed):
    python scripts/build_sample.py

Produces in generated_videos/samples/:
    sample_narrated.mp4        narration, no music
    sample_narrated_music.mp4  narration + the supplied music track (optional)

Options:
    --music PATH        use this audio file as background music
    --ai                try the configured LLM instead of the template script
    --no-narration      render silently (useful for a quick pipeline smoke test)
    --engine ENGINE     override the TTS engine for this build only
    --out DIR           output directory
"""

from __future__ import annotations

import argparse
import sys as _sys

# Windows consoles may be cp1252; keep progress glyphs from crashing the build.
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass
import datetime as dt
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import store  # noqa: E402
from services import ffmpeg_tools, music as music_lib  # noqa: E402
from services.script_generator import generate_script, word_budget  # noqa: E402
from services.storage import SAMPLE_DIR, VIDEO_DIR  # noqa: E402
from services.template_script import generate_template_script  # noqa: E402
from services.tts import synthesize  # noqa: E402
from services.validation import load_property_json  # noqa: E402
from services.video import RenderRequest, render_video  # noqa: E402

DEFAULTS = json.loads((Path(__file__).resolve().parent.parent / "config" /
                       "defaults.json").read_text(encoding="utf-8"))


class StoreOverride:
    """Delegate to the real settings store while forcing a TTS engine for this run."""

    def __init__(self, base, tts_patch: dict):
        self.base = base
        self.tts_patch = {key: value for key, value in tts_patch.items() if value}

    def public(self):
        config = self.base.public()
        config = json.loads(json.dumps(config))
        config["tts"].update(self.tts_patch)
        return config

    def get_secret(self, name):
        return self.base.get_secret(name)


def _public_video(item: dict) -> dict:
    clean = {key: value for key, value in item.items() if key != "path"}
    poster = clean.pop("poster_path", "")
    if poster:
        clean["poster"] = Path(poster).name
    return clean


def narration_texts(script: dict) -> list[str]:
    texts = [script.get("intro_narration", "")]
    texts += [scene.get("narration", "") for scene in script.get("scenes", [])]
    texts.append(script.get("cta_narration", ""))
    return [" ".join(str(text).split()) for text in texts]


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the sample promotional videos.")
    parser.add_argument("--out", default=str(VIDEO_DIR / "samples"))
    parser.add_argument("--music", default="")
    parser.add_argument("--ai", action="store_true")
    parser.add_argument("--no-narration", action="store_true")
    parser.add_argument("--engine", default="")
    parser.add_argument("--images", default=str(SAMPLE_DIR / "images"))
    parser.add_argument("--property", default=str(SAMPLE_DIR / "property_sample.json"))
    parser.add_argument("--keep-work", action="store_true",
                        help="keep intermediate PNG/MP4 files for inspection")
    args = parser.parse_args()

    started = time.time()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / ".work"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    print("=" * 78)
    print("Building sample property video")
    print("=" * 78)

    facts = load_property_json(args.property)
    images = sorted([path for path in Path(args.images).iterdir()
                     if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")])
    if len(images) < DEFAULTS["limits"]["min_images"]:
        print(f"ERROR: found {len(images)} images, need at least "
              f"{DEFAULTS['limits']['min_images']}. Run scripts/make_sample_images.py first.")
        return 1
    print(f"Property: {facts.name} — {facts.location}")
    print(f"Images:   {len(images)} ({images[0].name} … {images[-1].name})")

    # ---- script -----------------------------------------------------------
    script_source = "template"
    script_meta: dict = {}
    if args.ai:
        result = generate_script(facts, len(images))
        script, script_source = result.data, result.source
        script_meta = {"provider": result.provider, "model": result.model,
                       "fallback_reason": result.fallback_reason}
        print(f"Script:   requested AI → {result.source}"
              + (f" ({result.fallback_reason})" if result.fallback_reason else ""))
    else:
        budget = word_budget(len(images))
        script = generate_template_script(facts, len(images),
                                          max_words_per_scene=budget["scene"],
                                          max_words_intro=budget["intro"],
                                          max_words_cta=budget["cta"])
        script_meta = {"fallback_reason": "Template mode was used for this deterministic build."}
        print("Script:   built-in template (deterministic, no API call)")

    # ---- narration --------------------------------------------------------
    narration_clips = None
    narration_durations = None
    narration_meta = None
    if args.no_narration:
        print("Narration: disabled (flagged by the caller)")
    else:
        texts = narration_texts(script)
        tts_store = StoreOverride(store, {"engine": args.engine})
        print(f"Narration: generating {len(texts)} clips with "
              f"'{tts_store.public()['tts']['engine']}'…")
        try:
            tts = synthesize(texts, work / "narration", settings_store=tts_store)
            narration_clips = list(tts.clips)
            narration_durations = list(tts.durations)
            narration_meta = tts.to_public()
            print(f"Narration: {tts.engine} / {tts.voice} · "
                  f"{tts.total_seconds:.1f}s of speech"
                  + (" · FALLBACK USED" if tts.fallback_used else ""))
            for warning in tts.warnings:
                print(f"           ! {warning}")
        except Exception as exc:
            print(f"Narration failed: {exc}")
            print("Re-run with --no-narration to build a silent sample, or fix the voice engine.")
            return 1

    # ---- music ------------------------------------------------------------
    music_path = None
    music_name = ""
    if args.music:
        source = Path(args.music)
        if not source.is_file():
            print(f"ERROR: music file not found: {source}")
            return 1
        info = ffmpeg_tools.probe(source)
        music_path = source
        music_name = source.name
        print(f"Music:     {source.name} ({info.duration:.1f}s, {info.audio_codec})")
    else:
        print("Music:     none (no replacement track is added)")

    # ---- render -----------------------------------------------------------
    variants = [("sample_narrated.mp4", None, "")]
    if music_path:
        variants.append(("sample_narrated_music.mp4", music_path, music_name))

    results = []
    for filename, track, track_name in variants:
        print(f"\nRendering {filename} …")

        def progress(stage: str, detail: str) -> None:
            print(f"  [{stage}] {detail}")

        output = out_dir / filename
        info = render_video(RenderRequest(
            facts=facts,
            script=script,
            images=images,
            work_dir=work,
            output_path=output,
            narration_clips=narration_clips,
            narration_durations=narration_durations,
            music_path=track,
            music_name=track_name,
            music_volume=float(store.public()["video"]["music_volume"]),
            progress=progress,
            defaults=DEFAULTS,
        ))
        print(f"  ✓ {filename}: {info['duration']:.1f}s · {info['resolution']} · "
              f"{info['size_bytes'] / 1024:.0f} KB · audio={info['has_audio']}")
        results.append({"file": filename, **info})

    # ---- metadata ---------------------------------------------------------
    metadata = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "property": facts.clean_dict(),
        "images": [path.name for path in images],
        "script": {"source": script_source, **script_meta, "data": script},
        "narration": narration_meta,
        "music": {"file": music_name or None},
        # Never record absolute filesystem paths in a deliverable: keep the poster
        # as a filename next to the MP4.
        "videos": [_public_video(item) for item in results],
        "notes": [
            "Synthetic property data and synthetic illustrations; no real customer information.",
            "Rendered by the same pipeline the web app uses (Pillow frames + FFmpeg).",
        ],
    }
    (out_dir / "sample-metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)

    print("\n" + "=" * 78)
    print(f"Done in {time.time() - started:.1f}s → {out_dir}")
    for item in results:
        print(f"  {item['file']}  {item['duration']:.1f}s  {item['size_bytes'] / 1024:.0f} KB")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
