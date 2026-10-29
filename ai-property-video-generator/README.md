# AI Property Video Generator

Turn a handful of property photos and a few facts into a finished 20–40 second
promotional MP4 — with an AI-written script (or a deterministic template script
when no API key is available), optional AI voice-over, optional background music
from your own files, readable on-screen captions and a contact end-card.

The app runs locally on Windows, needs **no API key to work**, and never invents
property facts: the script layer is restricted to the details you type in.

> Demo assets that ship with the project: `generated_videos/samples/` contains a
> real narrated video and a real narrated + music video, both rendered by this
> codebase from the synthetic data in `sample_data/`.

---

## 1. What it does

| Step | What happens | Fallback if something fails |
| --- | --- | --- |
| 1. Property details | Name, location, price, type, bedrooms, features, contact, description are validated. Only name + location are required. | Clear field-level messages; nothing crashes on a missing field. |
| 2. Photos | 4–8 JPG/JPEG/PNG/WebP images, previewed, reorderable by drag or arrows, removable. Each is verified as a real image and normalised. | Unsupported/corrupt files are rejected with a plain-English reason. |
| 3. Script | Provider LLM returns strict JSON (headline, intro, per-photo captions, CTA). | Built-in **template generator** (no key, no network) runs automatically. |
| 4. Voice-over | Optional narration per scene, timed to the scene lengths. | Configured fallback engine (Edge → Windows SAPI, or your own choice); otherwise the video renders without narration. |
| 5. Music | Optional: pick a track from the local library, upload one for this video, or skip. Trimmed/looped, faded, and **ducked under the narration**. | No music is added — never a substitute track. |
| 6. Render | Pillow composes scene frames, FFmpeg animates them, crossfades and muxes the audio. | Render errors are reported; a failed render is never presented as a finished MP4. |
| 7. Verify | The finished file is decoded with FFmpeg (duration, both streams, playable) before it is offered. | The job fails with a plain error and the details stay in the form for a retry. |

The script layer only ever uses facts you supplied. `services/fact_guard.py`
compares every claim in AI output against the submitted details, and
`services/template_script.py` is deterministic so template mode is reproducible.

---

## 2. Requirements

* **Windows 10 or 11** (the offline voice-over engine uses Windows SAPI; everything
  else is cross-platform).
* **Python 3.10+** — verified on **Python 3.14.0**.
* **FFmpeg** — *not required*: `imageio-ffmpeg` ships a static FFmpeg 7.1 build with
  H.264 and AAC. Install FFmpeg yourself only if you prefer to.
* **Internet** — optional. Needed for the Gemini/OpenAI script providers and for
  Edge neural voices. Without it the app still produces a video using template
  scripts and offline Windows voices.

Everything installed is free and open source apart from the optional paid APIs.

---

## 3. Windows setup

```bat
:: 1. Go to the project folder
cd ai-property-video-generator

:: 2. Create and activate a virtual environment
py -3.14 -m venv .venv
.venv\Scripts\activate

:: (If the "py" launcher is missing, use:  python -m venv .venv)

:: 3. Install the pinned dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

:: 4. Check the environment (FFmpeg, disk space, offline voices)
python scripts\check_env.py
```

`check_env.py` prints one line per check. A healthy result looks like this
(abridged):

```
[ok]   Python version                   3.14.0
[ok]   Package: Flask                   3.1.3
[ok]   FFmpeg binary                    ...\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe
[ok]   FFmpeg filter: zoompan
[ok]   FFmpeg filter: sidechaincompress
[warn] ffprobe                          not found - ffmpeg -i parsing will be used instead
[ok]   Free disk space                  88870 MB
[ok]   Windows offline voices           2 installed
[ok]   Sample images                    6 found
Core pipeline ready. Optional integrations are reported as [--].
```

`[warn] ffprobe` is expected: the bundled FFmpeg does not ship ffprobe, and the app
falls back to parsing `ffmpeg -i`. Add `--network` to also test whether the free
Edge voice service is reachable.

### Running the app

```bat
python app.py
```

Open **http://127.0.0.1:5000**. The local server binds to loopback only. Change
the port with `APP_PORT` if 5000 is busy:

```bat
set APP_PORT=5050
python app.py
```

---

## 4. Using the app

The main page is a six-step wizard; provider keys live on the **Settings** page
(linked in the header), not in the wizard.

1. **Property details** — fill in what you know. Price and bedroom count are
   optional and are simply not shown if you leave them empty. No field is ever
   rendered as `None` or `null`.
2. **Photos** — drag the 6 sample photos from `sample_data\images\` (or your own
   4–8 photos) into the drop zone. Drag the thumbnails to reorder; use ✕ to
   remove. The order of the thumbnails is the order of the scenes.
3. **Script & voice-over** — choose a tone (`warm`, `premium`, `concise`) and
   press **Generate script**. The result panel shows the headline, every scene
   caption and the CTA, plus whether it came from AI or the template. Use the
   narration toggle and **Preview narration** to hear it before rendering.
4. **Background music** — pick *Library*, *Upload*, or *No music*, set the volume
   slider, and preview a library track inline.
5. **Generate** — press **Generate video**. Real progress appears stage by stage
   (validate → images → script → narration → music → render → verify → ready).
   **Cancel** is available while a render is running.
6. **Preview & download** — the finished MP4 plays inline, with its duration,
   resolution, size, codecs, music/narration summary and a download button.

There is also a **Load sample property** button (fills the form from
`sample_data/property_sample.json`) and a **Build demo video** shortcut
(`POST /api/demo`) that renders the shipped sample data end to end.

---

## 5. Configuring API keys

Keys are entered in **Settings** (`/settings`) — never in code or in the browser's
JavaScript. You can also provide them through a local `.env` file; copy
`.env.example` to `.env` and edit it. `.env` is git-ignored.

| Service | Purpose | Key needed | Free tier |
| --- | --- | --- | --- |
| Google Gemini | AI script generation | Yes | Yes |
| OpenAI-compatible (OpenAI, Z.ai, Groq, OpenRouter, local Ollama) | AI script generation | Yes, except local Ollama | Depends on provider |
| Microsoft Edge neural voices | Voice-over (default) | **No** | Free |
| Windows SAPI | Voice-over (offline fallback) | **No** | Free |
| OpenAI TTS | Voice-over (optional) | Yes | Paid |
| ElevenLabs | Voice-over (optional) | Yes | Limited free tier |

Steps in the Settings page:

1. Choose the provider (for example **Google Gemini**).
2. Paste the key into the password-masked field and press **Save key**. The page
   then shows a mask such as `••••••••-000` and the source (`settings` or `.env`).
3. Press **Test connection**. The button performs a **real** request — a tiny
   structured prompt for LLMs, a short spoken phrase for TTS. Nothing is reported
   as working unless the provider actually answered.
4. Use **Remove key** to delete a key stored from the UI. A key that came from
   `.env` cannot be removed here (the message says so).

Template mode needs no key at all: turn on **Always use the built-in template
script** in Settings (or tick *Use template script* in step 3) and the
deterministic generator is used.

### Where keys are stored

* `config/secrets.json` — written by the app. On Windows the contents are
  encrypted with **DPAPI** (`CryptProtectData`), tied to your Windows user
  account. Where DPAPI is unavailable the file is written with owner-only
  permissions instead, and the app logs a warning.
* `config/settings.json` — non-secret preferences (provider choice, model, voice,
  volume, tone).
* `config/session.key` — the random Flask session/CSRF signing key.
* Both secret files are git-ignored, never logged, never sent to the browser, and
  never written into a rendered video or its metadata.

**Storage limits:** DPAPI protects the file against other Windows users, not
against malware running as you. On Linux the secrets file relies on owner-only
permissions. For a hosted deployment, keep `config/` on a private volume, set
`APP_USERNAME`/`APP_PASSWORD`, and terminate HTTPS at your proxy. This is
appropriate local/small-team storage, not a managed secret vault.

---

## 6. Voice-over

* Primary engine and optional fallback are chosen in Settings:
  `edge`, `windows_sapi`, `openai`, `elevenlabs`, or `none` for the fallback.
* Edge voices are free and keyless but need internet and use Microsoft's public
  read-aloud endpoint; Windows SAPI voices work with no internet at all and sound
  robotic.
* Narration is generated **per scene**, so it stays in sync with the visuals. The
  timeline lengthens a scene (within limits) when a spoken line needs more time,
  rather than cutting the sentence off.
* If narration is switched off, no TTS request is made at all.
* If the primary engine fails, the app says so in plain words, retries only where
  a retry makes sense (timeouts, rate limits), and then uses the explicit fallback
  engine. The result panel and the video metadata record which engine actually
  spoke — a fallback is never reported as a success for the original engine.

---

## 7. Background music

Background music is **never generated by an AI model**. It is always a file you
provided, in one of three modes:

* **Library** — reusable local tracks in `music_library/` with an `index.json`.
  Each entry shows the filename, duration, size and any title/artist/album read
  from the file's tags, and can be previewed or removed.
* **Upload for this video** — used for the current render only, unless you tick
  *Also save to the library*.
* **No music** — the video renders with voice-over only, or completely silent.

Processing: the track is trimmed or looped to the video length, given a short
fade-in/fade-out, loudness-normalised, and **ducked** under the narration with an
FFmpeg sidechain compressor, so speech stays clear. The volume slider scales the
music bed. Unsupported or corrupt audio is rejected with a specific message.

**Rights:** you are responsible for having the right to use any track you upload.
The library page shows this notice, and the sample track shipped in
`sample_data/music/` is CC0 — see [CREDITS.md](sample_data/music/CREDITS.md).

---

## 8. Sample data and the sample videos

```
sample_data/
  property_sample.json      synthetic facts ("Marina Crest Residence", fake 555-01xx phone)
  images/                   6 illustrated property photos (JPG, generated by scripts/make_sample_images.py)
  music/                    relaxing-piano-cc0.mp3 (CC0, 60.7s)
generated_videos/samples/
  sample_narrated.mp4           34.9 s, 1920x1080, H.264 + AAC, Edge narration, no music
  sample_narrated_music.mp4     34.9 s, 1920x1080, H.264 + AAC, Edge narration + CC0 music
  sample_narrated.jpg           poster frame
  sample_narrated_music.jpg     poster frame
  sample-metadata.json          the exact property data, script, timeline and audio settings used
```

Reproduce them (about 4–5 minutes on a modern laptop, one render at a time):

```bat
python scripts\build_sample.py
```

Options: `--out DIR`, `--music PATH`, `--ai` (use the configured LLM instead of the
template), `--no-narration`, `--engine edge|windows_sapi`, `--images DIR`,
`--property FILE`, `--keep-work`.

Regenerate the illustrated photos if you delete them:

```bat
python scripts\make_sample_images.py
```

### Walkthrough with the sample input

1. `python app.py`, open http://127.0.0.1:5000
2. Click **Load sample property** (fills every field from the JSON).
3. Drag all six files from `sample_data\images\` into the photo drop zone.
4. Step 3 → **Generate script** → read the captions → leave narration on.
5. Step 4 → choose **Library** → pick `relaxing-piano-cc0.mp3` (upload it once if
   the library is empty) → volume ~35.
6. Step 5 → **Generate video** → wait for *Ready to preview and download*.
7. Step 6 → play the video, then **Download MP4**.

---

## 9. API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` , `/settings` | Wizard page, settings page |
| GET | `/api/health` | FFmpeg, disk space and offline-voice checks |
| GET/POST | `/api/settings` | Read/save non-secret preferences |
| POST | `/api/settings/secret` | Save a masked credential |
| DELETE | `/api/settings/secret/<name>` | Remove a UI-stored credential |
| POST | `/api/settings/test` | Live connection test (`kind=llm` or `kind=tts`) |
| GET/POST | `/api/music` | List / upload library tracks |
| DELETE | `/api/music/<id>` · GET `/media/music/<id>` | Remove / stream a track |
| POST | `/api/script` | Generate a script (AI or template) and estimate the timeline |
| POST | `/api/narration/preview` · GET `/media/preview/<id>` | Narration preview clip |
| POST | `/api/generate` | Start a render (multipart: fields + `images`) |
| GET | `/api/sample` , POST `/api/demo` | Sample data / one-click demo render |
| GET | `/api/jobs/<id>` , POST `/api/jobs/<id>/cancel` | Job status / cancel |
| GET | `/video/<id>` · `/download/<id>` · `/poster/<id>` | Stream, download, poster |

Writes require the CSRF token that the page embeds, and every endpoint rejects
requests that do not originate from this computer.

---

## 10. Deploying

The app is a standard WSGI application ([wsgi.py](wsgi.py) exposes `app`) and a
`Dockerfile` is included. Rendering is CPU-bound; give the container at least
2 GB of RAM and expect one render at a time.

### Docker

```bat
docker build -t property-video-studio .

docker run -d --name property-video -p 8000:8000 ^
  -e APP_USERNAME=studio -e APP_PASSWORD=change-this ^
  -e TRUST_PROXY=1 -e APP_HTTPS=1 ^
  -v apvg-config:/app/config ^
  -v apvg-uploads:/app/uploads ^
  -v apvg-music:/app/music_library ^
  -v apvg-videos:/app/generated_videos ^
  property-video-studio
```

Open the app through your HTTPS proxy (for example Caddy or nginx). The
container listens on port 8000 with Waitress (4 threads).

### Required production settings

| Variable | Why |
| --- | --- |
| `APP_USERNAME` + `APP_PASSWORD` | Enables HTTP Basic authentication. Without both, remote clients are refused and the app is local-only. |
| `APP_HTTPS=1` | Marks the session cookie `Secure`; set it when users reach the app over HTTPS. |
| `TRUST_PROXY=1` | Trusts one `X-Forwarded-For` / `X-Forwarded-Proto` hop from your proxy. |
| Volumes for `config/`, `uploads/`, `music_library/`, `generated_videos/` | Keys, uploaded media and rendered MP4s must survive container restarts. |

> Basic auth is a single shared login, not per-user accounts. Put the app behind
> HTTPS, use a long password, and do not enable remote access without both
> credentials set. For multi-user or public SaaS use, replace the gate with a real
> identity provider and object storage.

### Running without Docker

```bat
pip install -r requirements.txt
set APP_USERNAME=studio
set APP_PASSWORD=change-this
waitress-serve --listen=0.0.0.0:8000 --threads=4 wsgi:app
```

---

## 11. Testing

```bat
:: offline suite (no keys, no network)
python -m pytest -q

:: real network calls: invalid-key handling against Gemini/OpenAI + Edge TTS
set RUN_LIVE=1
python -m pytest -q tests\test_live_providers.py
```

* The default suite is offline and mocked where a provider is involved:
  **156 passed, 3 skipped** (the live ones) in about 2.5 minutes.
* `RUN_LIVE=1` adds real calls: an invalid Gemini key must be rejected, Edge TTS
  must actually produce audio, and the configured LLM provider is exercised. These
  tests spend no money when no valid paid key is configured.

Results from this build machine (Windows 11, Python 3.14.0) are summarised in
[DEMO.md](DEMO.md#6-verified-test-results).

---

## 12. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `Address already in use` | Another program (or an earlier run of this app) holds port 5000. Set `APP_PORT=5050` or close the other process. Then open `http://127.0.0.1:5050`. |
| “A video is already rendering.” | Only one render runs at a time (FFmpeg saturates the CPU). Wait for the running job, then start again. |
| `FFmpeg was not found` | Run `python scripts\check_env.py`. Reinstall with `python -m pip install imageio-ffmpeg`, or install FFmpeg (`winget install Gyan.FFmpeg`) and set `FFMPEG_BINARY=C:\ffmpeg\bin\ffmpeg.exe`. |
| Narration is robotic | The Edge service was unreachable, so the Windows SAPI fallback spoke. The result panel always names the engine used. Connect to the internet and re-render for neural voices. |
| “The Gemini API key was rejected.” | Test connection made a real request and the provider refused the key. Check the key, the model name, and the account's quota/billing. |
| “rate limit / quota” messages | The provider's free tier is exhausted. Wait, or switch to template mode — the app never retries indefinitely. |
| Video has no sound | Narration was switched off and no music was selected. That is valid; the app does not add a substitute track. |
| Render fails with a disk message | Keep at least ~150 MB free; the renderer writes scene frames and clips into `uploads/jobs/<id>/`. |
| A key will not delete | It comes from `.env`, which the app deliberately does not rewrite. Remove the line from `.env` and restart. |

Debug information is written to `logs/app.log` with keys and authorization headers
redacted before they reach the file.

---

## 13. Project layout

```
ai-property-video-generator/
  app.py                     Flask app, routes, background render worker
  wsgi.py                    production WSGI entry point (Waitress/Gunicorn)
  Dockerfile .dockerignore   container build for a hosted deployment
  requirements.txt           pinned dependencies
  config/
    defaults.json            video/audio/script limits and timing
    providers.py             provider + engine catalogue (no credentials)
    settings.py              settings store, DPAPI secret storage, masking
  services/
    validation.py            property + upload validation
    storage.py               safe paths, ids, extension allow-lists
    images.py                image verification and normalisation
    fact_guard.py            blocks AI claims that are not in the submitted facts
    template_script.py       deterministic script generator (keyless fallback)
    script_generator.py      Gemini + OpenAI-compatible adapters, JSON validation, retries
    tts.py                   Edge / Windows SAPI / OpenAI / ElevenLabs + fallback logic
    timeline.py              scene durations, narration fitting, captions
    video.py                 Pillow scene composition + FFmpeg render + verification
    audio.py                 narration concat, ducking, loudness, fades
    music.py                 music library, upload validation, metadata
    ffmpeg_tools.py          FFmpeg discovery, probing, safe invocation
    jobs.py                  job state, progress stages, cancellation, render lock
    errors.py                error taxonomy → user messages + HTTP codes
    logging_setup.py         rotating log with secret redaction
  templates/ static/         HTML/CSS/JS interface (no secrets reach the browser)
  scripts/                   check_env.py, build_sample.py, make_sample_images.py
  sample_data/               synthetic JSON, photos, CC0 music
  generated_videos/samples/  rendered sample MP4s + metadata + posters
  tests/                     pytest suite (offline + opt-in live)
  uploads/ music_library/ logs/   runtime data (git-ignored)
```

## 14. Security notes

* Loopback-only by default. Setting both `APP_USERNAME` and `APP_PASSWORD`
  enables HTTP Basic authentication and allows remote clients; without them a
  network bind refuses remote requests.
* State-changing calls need the per-session CSRF token, and session cookies are
  HTTP-only and `Secure` when `APP_HTTPS=1`.
* API keys never reach the frontend: the settings API returns only *configured*,
  *source* and a masked hint.
* Keys and authorization headers are redacted from logs; no key is written into
  rendered videos, posters or metadata.
* Uploads are validated by content, stored under random IDs with sanitised
  extensions, and served only through ID-based routes — no path from the request
  is used directly, so path traversal is not possible.
* `.gitignore` excludes `.env`, `config/secrets.json`, `config/session.key`,
  uploads, the music library and generated videos (the curated samples excepted).

## 15. Known limitations

* **2000-era Windows-first voice fallback.** Windows SAPI voices are robotic; use
  Edge (free) or a cloud engine for demo-quality narration.
* **English-optimised.** Templates, SAPI voice selection and Edge defaults target
  English. Other languages work only where a provider/voice supports them.
* **One render at a time**, jobs live in memory (a restart forgets job history,
  though finished MP4s stay in `uploads/jobs/<id>/output/`).
* **16:9 only** (1920×1080 or the configured equivalent). No portrait variant yet.
* **Single shared login, no per-user accounts.** The optional Basic auth gate is
  designed for a private deployment; it is not a multi-tenant identity system.
* **Edge TTS is an unofficial Microsoft endpoint**: it can change or rate-limit
  without notice, which is exactly why the explicit SAPI fallback exists.
* **LLM quality varies by model/provider**; the fact guard can reject an otherwise
  usable script, in which case the template script is used and the UI says so.
* **Voice-over preview** is generated scene-free (one clip of the text you paste),
  not a full timeline preview.
