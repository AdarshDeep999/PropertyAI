# Demo guide — AI Property Video Generator

A 6–8 minute walkthrough for an evaluator, plus the honest test record from the
build machine.

---

## 1. Before the demo (5 minutes)

```bat
cd ai-property-video-generator
.venv\Scripts\activate
python scripts\check_env.py
python app.py
```

Open **http://127.0.0.1:5000**. Keep `generated_videos\samples\` open in Explorer
so you can show the pre-rendered outputs without waiting.

Optional, if you want to show live AI script generation: have a Gemini key ready
and enter it in **Settings → Google Gemini** before the demo, then press **Test
connection** once (it should say *Connected*). The demo works fully without it.

**Timing expectations:** one render takes roughly 2–3 minutes on a modern laptop
(≈130 s in testing). Script generation is instant in template mode and 2–10 s with
an LLM. Narration adds ~10–20 s. Say this out loud rather than letting the progress
bar feel like a stall — every stage shown is real work.

---

## 2. Suggested script

### 0:00 — Frame the problem (30 s)

> “An agent has six phone photos and a listing. Making a promo video normally means
> an editor, a scriptwriter and a voice artist. This tool takes the listing facts
> and the photos and produces a 35-second MP4 — script, captions, narration and
> music — on a laptop, with no API key required.”

### 0:30 — Show the finished artefact first (45 s)

Open `generated_videos\samples\sample_narrated_music.mp4` and play it.

> “This is a real output from this codebase: 1920×1080, 34.9 seconds, H.264 with
> narration and CC0 piano. The captions come from the supplied features, the
> end-card from the supplied contact details. Nothing in it was invented.”

Point out: opening title with name/location/price, six photo scenes with captions,
closing enquiry card. Note the music dips under the voice.

### 1:15 — Walk the workflow (3 min)

1. **Property details tab** — click **Load sample property**.
   > “Six fields, only name and location are required. Price and bedrooms are
   > optional and simply disappear if omitted — no `None` leaking into the video.”
2. **Photos tab** — drag in `sample_data\images\*.jpg` (six files).
   > “4–8 images, previewed, reorderable, removable. The order here is the scene
   > order. Unsupported files are rejected before any rendering starts.”
   Drag one thumbnail to a new position to show reordering; then drag it back.
3. **Script & voice-over tab** — press **Generate script**.
   > “The template generator is deterministic and offline. With a key, the same
   > schema is produced by Gemini or any OpenAI-compatible model, validated as
   > JSON, and checked against the submitted facts by the fact guard. If the model
   > invents a price or an amenity, that script is rejected and the template is
   > used instead — and the UI says which one you are looking at.”
   Read the headline, one caption, the CTA. Toggle narration on, press **Preview
   narration** to hear the voice.
4. **Background music tab** — show the three modes.
   > “Library, upload-once, or no music. There is no AI music generation: users
   > bring their own track and are responsible for the rights. Volume slider,
   > automatic ducking under speech, fade in/out, loop or trim.”
   Pick the library track (upload `sample_data\music\relaxing-piano-cc0.mp3` once if
   the library is empty), set volume ~35.
5. **Generate tab** — press **Generate video**. While it runs, narrate the stages:
   validate → images → script → narration → music → render → verify → ready.
   > “The verify stage actually decodes the finished file. A job that fails never
   > shows a player or a download link.”
6. **Preview & download tab** — play the result, show duration/resolution/codecs,
   press **Download MP4**. Open the file from Explorer to prove it is a real MP4.

Provider configuration is deliberately **not** a wizard step: it lives on the
**Settings** page, which you show next.

### 4:15 — Show the settings and a real connection test (1 min)

**Settings**:
> “Keys are entered here, encrypted at rest (DPAPI on Windows, owner-only file
> elsewhere), masked in the UI, never sent to the browser and redacted from logs.
> Test connection makes a real request.”

If you want to demonstrate failure safely: save a deliberately invalid key, press
**Test connection**, show the plain message (“The Gemini API key was rejected”),
then **Remove key**. This costs nothing and was verified against the live Gemini
endpoint during development. Do **not** claim a quota failure unless you have
actually exhausted one.

### 5:15 — Fallbacks (45 s)

* **Script fallback (live, safe):** turn on **Always use the built-in template
  script** (or tick *Use template script* in step 3). Generate again and point at
  the “Template” badge. This is deterministic and needs no network.
* **Voice fallback (explain honestly):** switch the primary engine to **OpenAI
  TTS** with no key saved and the fallback to **Edge**. Generation shows a friendly
  failure and uses the configured fallback — this path is covered by automated
  tests with mocked providers, and Edge→SAPI was exercised directly, but do not
  present it as a live cloud outage demo unless you cause one.
* **No music / no narration:** set *No music*, switch narration off, render. The
  video still succeeds, silently. Nothing is substituted.

### 6:00 — Close (30 s)

> “The whole thing is Flask + Pillow + FFmpeg. The AI parts are optional adapters;
> the product works without them. The sample outputs, synthetic data and test
> suite are in the repo. Known limits: 16:9 only, one render at a time, and a single
> shared login when deployed — no per-user accounts yet.”

---

## 3. Complete example workflow (copy/paste reference)

1. `python app.py` → http://127.0.0.1:5000
2. **Load sample property**.
3. Add `sample_data\images\01-exterior.jpg` … `06-terrace.jpg`.
4. Step 3: tone *warm* → **Generate script** → read captions.
5. Narration **on**, voice `en-US-AriaNeural` (Edge), **Preview narration**.
6. Step 4: **Library** → `relaxing-piano-cc0.mp3` → volume 35.
7. **Generate video** → wait for *Ready to preview and download*.
8. Play, then **Download MP4**.
9. Optional: repeat with **No music**, then with narration off, to show all three
   audio outcomes.

---

## 4. How to demonstrate each feature

### Script generation

* **Template (no key):** guaranteed to work offline. Open **Show script details**
  to show `source: template`, the word budget and estimated duration.
* **AI:** configure Gemini (or an OpenAI-compatible endpoint) in Settings, test the
  connection, then generate. The result badge reads **AI** and names the model.
  The fact guard may downgrade it to the template; the warning explains why, which
  is itself a good thing to show.
* **Malformed AI output:** not directly reproducible without a misbehaving model.
  It is covered by automated tests (invalid JSON, wrong types, over-long text →
  template fallback), not by a live demo.

### Voice-over

* Toggle narration, choose engine/voice, **Preview narration** (a real TTS call).
* The default Edge voice needs internet; switching to **Windows built-in speech**
  demonstrates fully offline narration (robotic, by design).
* Narration-off renders make **zero** TTS calls — visible in the progress stage
  (“Voice-over is off”).

### Music

* **Library:** upload the CC0 track, then select it in a later render; preview and
  delete from the library list.
* **Upload for this video:** choose a file in step 4 without saving it. It is used
  once and lives only in that job's folder.
* **None:** select *No music*; the video is narration-only or silent.
* Point out the rights notice and the CC0 credit file.

### API configuration and connection testing

* Save/update/remove a key; observe the mask (`••••••••-000`) and the source tag
  (`settings` vs `.env`).
* **Test connection** is a real request. A deliberately invalid key produces
  “The Gemini API key was rejected.” A valid key produces “Connected…” with the
  model name. With no key, the app says so instead of pretending.
* The key value is never displayed again, never in the page source, never in
  `logs/app.log`.

### Fallback behaviour (safe demo)

| Fallback | How to show it live | What to say |
| --- | --- | --- |
| Template script | Force template mode, generate | Deterministic, offline, no charge |
| Narration disabled | Toggle off, render | No TTS call, valid silent or music-only output |
| Edge → SAPI | Disconnect the network, render with Edge primary + SAPI fallback (allow ~30 s of retries) | The result names the engine that actually spoke; it is not reported as Edge |
| Invalid LLM key | Save a fake key, Test connection, remove it | Real 401 from the provider, plain message, no key echoed |

Do not stage a fake “quota exceeded” or “network outage” screenshot; if you have not
caused it, describe the automated coverage instead.

---

## 5. Key design decisions

1. **Facts are the only source of truth.** Prompts include only supplied fields, and
   `fact_guard.py` rejects numbers, prices or feature claims that do not appear in
   the input. The template generator cannot invent facts by construction.
2. **Keyless by default.** Edge neural TTS is free and needs no key; Windows SAPI is
   the offline fallback; the template script always works. A paid key improves the
   script, it does not gate the product.
3. **Per-scene narration drives timing.** Each scene's TTS clip is measured, and the
   scene is lengthened (within limits) so speech is never cut off. The total stays
   in the 20–40 s target for typical listings.
4. **Text is a static overlay; only the photo moves.** Ken Burns zoom is applied to
   the image layer, not the composed frame, so captions never drift into the safe
   margins. (This was a real bug found by inspecting rendered frames.)
5. **FFmpeg for media, Pillow for layout.** FFmpeg provides reliable H.264/AAC,
   crossfades, sidechain ducking and loudness normalisation; Pillow gives precise,
   font-consistent typography that is painful to do in filter graphs.
6. **One render at a time.** FFmpeg already saturates the CPU; serialising avoids
   timeouts and makes progress truthful. A lock plus a clear “already rendering”
   message is better than three half-finished jobs.
7. **Secrets are server-side only.** DPAPI-encrypted at rest, masked in APIs,
   redacted in logs, never in templates or JS. The app binds to localhost and
   demands a CSRF token for writes.
8. **Verify before declaring success.** Every output is probed and decoded; the
   player and download link only appear for a verified file.

---

## 6. Verified test results

Machine: Windows 11, Python 3.14.0, FFmpeg 7.1 (bundled via `imageio-ffmpeg`),
Edge neural voices online, Windows SAPI voices installed.

### Passed

| Check | Result |
| --- | --- |
| Offline pytest suite | **156 passed, 3 skipped** (live-only tests), ~2.7 min including FFmpeg render tests |
| Live provider tests (`RUN_LIVE=1`) | **3 passed** — real invalid Gemini key rejected, real invalid OpenAI-compatible key rejected, real Edge TTS produced audio |
| Environment check | FFmpeg 7.1 present, 88 GB free, 2 SAPI voices |
| Template script | Deterministic, 6 photo scenes + title + CTA, features used as captions, no `None` |
| Full render from sample data | `sample_narrated.mp4` 34.9 s, 1920×1080, H.264 High + AAC LC 48 kHz, decodes cleanly |
| Second render | `sample_narrated_music.mp4` 34.9 s, same specs, music present, narration not clipped |
| HTTP workflow | Pages load, CSRF enforced, image-count/type validation, corrupt audio rejected, library upload/list/stream, job accepted and polled to 100 %, all 8 stages reported, MP4/poster/download served, downloaded file decodes |
| Invalid key over HTTP | Fake Gemini key stored → masked in settings → live endpoint returned `invalid_credentials` → key removed → no-key state reported honestly |
| Secret hygiene | Fake key absent from API responses and `logs/app.log`; no `Authorization: Bearer` logged |
| Sample metadata | Script, timeline, audio settings and verified stream info recorded per video |

### Not tested (honest gaps)

* A **valid** Gemini/OpenAI key was not available on this machine, so live AI script
  generation, quota/billing errors and model-specific JSON quirks were **not**
  exercised end-to-end. The adapters, schema validation and fallback are covered by
  mocked tests only.
* Cloud TTS (OpenAI, ElevenLabs) was **not** called; only Edge and Windows SAPI were
  used for real. Their adapters and error mapping are covered by mocked tests.
* The Edge → SAPI fallback was exercised by unit tests with a mocked Edge failure,
  not by disconnecting the network during a full render.
* No manual browser click-through of every control; HTTP-level checks plus page
  rendering were verified, not pixel-level visual QA.
* Portrait output, authentication and multi-user operation are not implemented.

---

## 7. Talking points for an evaluator

* “The interesting part is not calling an LLM — it is making the product useful
  when the LLM is unavailable, slow, expensive or wrong.”
* “The fact guard turns ‘don't hallucinate’ from a prompt instruction into an
  enforceable check.”
* “The fallback chain is explicit and visible: the UI tells you whether the script
  is AI or template, and which voice actually spoke.”
* “Media work is delegated to FFmpeg, but layout is deterministic Pillow code, so
  typography and safe margins are testable and repeatable.”
* “The sample videos are not mock-ups; `sample-metadata.json` records exactly what
  was submitted and how it was rendered.”
* “Security is scoped to the actual deployment: localhost-only, DPAPI at rest,
  masked keys, CSRF on writes — and the README says clearly what would be needed
  before exposing it to a network.”
