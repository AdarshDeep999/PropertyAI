# Technology inventory

Everything below is actually used by the code in this repository (or deliberately
not used, where stated). Versions are the pinned versions from
`requirements.txt`, verified on Windows 11 with Python 3.14.0.

Legend: **AI/ML** = uses a trained model or AI service; **Conventional** = ordinary
software. “Fallback role” says what happens when the item is missing, unconfigured
or fails.

---

## 1. Runtime and core libraries

| Technology | Version | Purpose | AI/ML? | API key? | Can cost money? | Internet? | Fallback role |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Python | 3.14.0 (3.10+ supported) | Application runtime | Conventional | No | No | No | — |
| Flask | 3.1.3 | HTTP server, routes, JSON APIs, file responses, sessions | Conventional | No | No | No | The whole app is local; no hosted fallback needed |
| Jinja2 | bundled with Flask | Server-side HTML templates; secrets are never rendered into them | Conventional | No | No | No | — |
| Pillow | 12.1.0 | Image verification, EXIF-aware normalisation, scene composition, FreeType typography, poster extraction, synthetic sample images | Conventional (image processing, no model) | No | No | No | Corrupt/unsupported images are rejected with a clear message before rendering |
| imageio-ffmpeg | 0.7.0 | Supplies a static FFmpeg 7.1 binary so no system install is required | Conventional | No | No | No (wheel is bundled at install time) | If a system FFmpeg is installed, the app can use it via `FFMPEG_BINARY` |
| FFmpeg | 7.1 (gyan.dev essentials build) | H.264/AAC encode and decode, Ken Burns (`zoompan`), crossfades (`xfade`), sidechain ducking (`sidechaincompress`), loudness normalisation (`loudnorm`), fades, trimming/looping, stream probing | Conventional media processing | No | No | No | A render failure is reported as a failed job; the app never presents a partial file |
| requests | 2.32.5 | Server-side calls to LLM and TTS HTTP APIs, with timeouts | Conventional | No | No | Only when a provider is used | Every provider path has a timeout and error classification |
| mutagen | 1.48.1 | Reads duration and title/artist/album tags for music-library entries | Conventional | No | No | No | Unreadable audio is rejected; tags are optional metadata |
| python-dotenv | 1.2.2 | Optional `.env` loading for keys and paths | Conventional | No | No | No | The app works entirely through the Settings UI without `.env` |
| Waitress | 3.0.2 | Production WSGI server used by `wsgi.py` and the Docker image (Windows and Linux) | Conventional | No | No | No | Falls back to Flask's development server only when running `python app.py` locally |
| Docker | optional | Container build from the included `Dockerfile`; DejaVu fonts for Linux rendering | Conventional | No | No | No (build needs a registry) | Not required locally; it is the recommended deployment path |
| pytest | 9.2.0 | Automated test suite | Conventional | No | No | No (live tests opt-in) | — |
| Windows DPAPI (`crypt32` via `ctypes`) | OS component | Encrypts `config/secrets.json` to the current Windows user | Conventional (OS cryptography) | No | No | No | On non-Windows/DPAPI failure, the file is written with owner-only permissions and a warning is logged |
| Windows SAPI (`System.Speech`) | OS component | Offline text-to-speech fallback | **AI/ML** (OS speech synthesis) | No | No | **No** | Used automatically when the primary TTS engine fails and SAPI is configured as fallback; quality is robotic |

---

## 2. AI services

| Service | Integration | Purpose | AI/ML? | API key? | Can cost money? | Internet? | Fallback role |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Google Gemini (`generativeLanguage` REST) | `services/script_generator.py` | Writes the structured marketing script and captions. Default model `gemini-3.5-flash`; `gemini-3.6-flash` and the lite variants selectable | **AI/ML** (hosted LLM) | **Yes** (`GEMINI_API_KEY` or Settings) | Free tier available; paid tiers exist | Yes | Optional. On missing/invalid key, quota, rate limit, timeout or malformed JSON → deterministic template script, with the reason shown |
| OpenAI-compatible Chat Completions | `services/script_generator.py` | Same script contract against OpenAI, Z.ai (GLM), Groq, OpenRouter or a local Ollama server | **AI/ML** (hosted or local LLM) | Yes, except local Ollama | Depends on provider | Yes (except local Ollama) | Optional; same template fallback |
| Microsoft Edge neural voices (`edge-tts` 7.3.1 client → Microsoft read-aloud endpoint) | `services/tts.py` | Default voice-over: per-scene MP3 narration | **AI/ML** (Microsoft neural TTS) | **No** | **No** (free service) | **Yes** | If unreachable/rate-limited, the configured fallback engine (normally Windows SAPI) is used and the UI says so. This is an unofficial endpoint, hence the explicit fallback |
| OpenAI text-to-speech | `services/tts.py` | Optional higher-quality cloud narration | **AI/ML** | Yes (`OPENAI_TTS_API_KEY`) | **Yes** (paid per character) | Yes | Optional; failures fall back or disable narration, never silently swap engines |
| ElevenLabs | `services/tts.py` | Optional alternative cloud narration | **AI/ML** | Yes (`ELEVENLABS_API_KEY` + voice ID) | **Yes** (free tier limited) | Yes | Optional, same fallback policy |

Notes:

* **No model was trained or fine-tuned.** The app calls existing models/services.
* **No AI music generation is implemented**, by design. Music is always a user file.
* The Gemini default model is configurable; a model name the key cannot access
  returns an “unsupported model” error and the script falls back.
* OpenAI TTS and ElevenLabs are implemented adapters but were **not** exercised
  against the live services during this build (no keys); only mocked tests cover
  their request/error handling.

---

## 3. Frontend

| Technology | Purpose | AI/ML? | Key? | Cost? | Internet? | Fallback role |
| --- | --- | --- | --- | --- | --- | --- |
| HTML5 + CSS (custom, no framework) | Seven-step wizard, settings page, responsive layout | Conventional | No | No | No (fonts are system fonts) | — |
| Vanilla JavaScript (`static/js/app.js`, `settings.js`) | Tab navigation, photo preview/reorder, API calls, progress polling, player | Conventional | No | No | No | If JavaScript is unavailable the pages still render, but generation requires it |
| HTML5 `<video>` + `<audio>` | Result playback, narration preview, music preview | Conventional | No | No | No | Download link is always available for a verified render |

No CDN, analytics, telemetry or third-party frontend script is loaded.

---

## 4. Data and media assets

| Item | Purpose | AI/ML? | Key? | Cost? | Internet? | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| JSON (`config/defaults.json`, `property_sample.json`, `music_library/index.json`, job metadata) | Configuration, sample input, library index, render metadata | Conventional | No | No | No | No database is used; jobs are in memory, files on disk |
| Synthetic property data (`sample_data/property_sample.json`) | Demo input | Conventional | No | No | No | Fictional name/address; phone in the reserved 555-01xx range; `.example` email domain |
| Sample images (`sample_data/images/`) | Demo photos | Conventional (procedurally drawn with Pillow) | No | No | No | Generated by `scripts/make_sample_images.py`; not stock photos, no real property |
| `relaxing-piano-cc0.mp3` | Demo background music | Conventional (recorded music, not AI) | No | No | No (shipped file) | CC0 1.0; see `sample_data/music/CREDITS.md` |

---

## 5. Deliberately not used

| Not used | Why |
| --- | --- |
| MoviePy | Direct FFmpeg gives deterministic filter graphs, better control of ducking/xfade and fewer heavyweight dependencies. The brief allows “FFmpeg and/or MoviePy”. |
| Any AI music generator | Explicitly out of scope; music must be user-supplied. |
| A database / ORM | Single-user local tool; JSON plus the filesystem is sufficient and easier to inspect. |
| Cloud storage / CDN | Everything is local by design; no upload of user photos to a third party. |
| Authentication provider | The app is localhost-only and says so; adding login is a deployment prerequisite, not a feature of this prototype. |
| Whisper/STT, image generation, model training | Not required by the brief. |

---

## 6. Cost and offline summary

* **Fully offline, zero cost:** template script + Windows SAPI narration + local
  music/no music + Pillow/FFmpeg render.
* **Free but online:** Edge neural narration (no key), Gemini free tier (key).
* **Paid and online:** OpenAI/OpenAI-compatible LLMs beyond free tiers, OpenAI TTS,
  ElevenLabs.
* The app never retries indefinitely and never makes a TTS call when narration is
  switched off, so an accidental bill cannot grow from a forgotten toggle.
