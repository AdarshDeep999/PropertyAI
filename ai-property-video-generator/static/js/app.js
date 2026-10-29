/* Generator wizard: form state, photo ordering, script preview, narration,
   music library, live job progress, and the finished video panel. */
(function () {
  "use strict";

  const { api, toast, escapeHtml, $, boot } = window.Studio;
  const limits = boot.limits || { min_images: 4, max_images: 8, max_image_mb: 15 };
  const catalog = boot.catalog || {};
  const TABS = ["details", "images", "script", "music", "progress", "result"];
  const LAST_FORM_TAB = 3;
  const ALLOWED_EXT = [".jpg", ".jpeg", ".png", ".webp"];
  const ALLOWED_AUDIO = [".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"];

  const state = {
    tab: "details",
    files: [],
    script: null,
    job: null,
    timer: null,
    busy: false,
    musicMode: "none",
    trackId: null,
    dragId: null,
  };

  if (!document.getElementById("property-form")) return; // not the generator page

  /* ------------------------------------------------------------------ tabs */
  function setTab(name) {
    state.tab = name;
    document.querySelectorAll(".step").forEach((button) => {
      button.setAttribute("aria-selected", button.dataset.tab === name ? "true" : "false");
    });
    document.querySelectorAll(".panel").forEach((panel) => {
      panel.classList.toggle("is-active", panel.id === "panel-" + name);
    });
    const index = TABS.indexOf(name);
    $("btn-prev").disabled = index <= 0;
    $("btn-next").disabled = index >= LAST_FORM_TAB;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  document.querySelectorAll(".step").forEach((button) => {
    button.addEventListener("click", () => setTab(button.dataset.tab));
  });
  $("btn-next").addEventListener("click", () => {
    const index = TABS.indexOf(state.tab);
    if (index < LAST_FORM_TAB) setTab(TABS[index + 1]);
  });
  $("btn-prev").addEventListener("click", () => {
    const index = TABS.indexOf(state.tab);
    if (index > 0) setTab(TABS[index - 1]);
  });

  /* -------------------------------------------------------------- form data */
  function formValues() {
    return {
      name: ($("field-name").value || "").trim(),
      location: ($("field-location").value || "").trim(),
      price: ($("field-price").value || "").trim(),
      property_type: ($("field-type").value || "").trim(),
      bedrooms: ($("field-bedrooms").value || "").trim(),
      features: ($("field-features").value || "").trim(),
      contact: ($("field-contact").value || "").trim(),
      description: ($("field-description").value || "").trim(),
      tone: ($("tone").value || "warm"),
    };
  }

  function setFieldError(field, message) {
    const node = document.querySelector('[data-error-for="' + field + '"]');
    if (node) node.textContent = message || "";
    const input = $("field-" + field) ||
      (field === "property_type" ? $("field-type") : null) ||
      (field === "music" ? $("music-file") : null);
    if (input) input.classList.toggle("is-invalid", Boolean(message));
  }

  function validate(showErrors) {
    const values = formValues();
    const errors = {};
    if (!values.name) errors.name = "Enter the property name.";
    else if (values.name.length < 2) errors.name = "Use at least 2 characters.";
    if (!values.location) errors.location = "Enter the location.";
    else if (values.location.length < 2) errors.location = "Use at least 2 characters.";
    if (values.price && !/\d/.test(values.price)) {
      errors.price = "Add a number, or leave the price blank.";
    }
    if (values.bedrooms) {
      const beds = Number(values.bedrooms);
      if (!Number.isFinite(beds) || beds < 0 || beds > 50) {
        errors.bedrooms = "Bedrooms must be between 0 and 50.";
      }
    }
    if (state.files.length < limits.min_images) {
      errors.images = "Add at least " + limits.min_images + " photos.";
    }
    if (state.files.length > limits.max_images) {
      errors.images = "Use at most " + limits.max_images + " photos.";
    }
    if (showErrors) {
      ["name", "location", "price", "bedrooms", "property_type", "features", "contact", "description"]
        .forEach((field) => setFieldError(field, errors[field] || ""));
      const alertNode = $("image-alert");
      if (errors.images) {
        alertNode.hidden = false;
        alertNode.innerHTML = "<div><strong>Photos:</strong> " + escapeHtml(errors.images) + "</div>";
      } else {
        alertNode.hidden = true;
      }
    }
    return { ok: Object.keys(errors).length === 0, errors, values };
  }

  function updateReadiness() {
    const values = formValues();
    const ready = Boolean(values.name) && Boolean(values.location) &&
      state.files.length >= limits.min_images && state.files.length <= limits.max_images;
    const button = $("generate-btn");
    button.disabled = !ready || state.busy;
    const note = $("readiness");
    const showingFinishedVideo = state.tab === "result" && state.job &&
      state.job.status === "done" && state.job.result;
    if (state.busy) note.textContent = "A video is being generated…";
    else if (showingFinishedVideo) {
      note.textContent = "Your video is ready — download it or generate another.";
    } else if (!values.name || !values.location) {
      note.textContent = "Add a property name and location to continue.";
    }
    else if (state.files.length < limits.min_images) {
      note.textContent = "Add " + (limits.min_images - state.files.length) + " more photo(s) to enable generation.";
    } else if (state.files.length > limits.max_images) {
      note.textContent = "Remove " + (state.files.length - limits.max_images) + " photo(s).";
    } else {
      note.textContent = "Ready to generate · " + state.files.length + " photos · " +
        (boot.has_llm_key ? "AI script available" : "template script mode");
    }
  }

  ["field-name", "field-location", "field-price", "field-bedrooms", "field-type",
   "field-features", "field-contact", "field-description"].forEach((id) => {
    const node = $(id);
    if (!node) return;
    node.addEventListener("input", () => {
      updateReadiness();
      clearScript(); // facts may have changed: never render a stale script
    });
    node.addEventListener("blur", () => {
      const values = formValues();
      if (id === "field-name") setFieldError("name", values.name.length >= 2 ? "" : "Enter the property name.");
      if (id === "field-location") setFieldError("location", values.location.length >= 2 ? "" : "Enter the location.");
    });
  });

  /* ---------------------------------------------------------------- images */
  function addFiles(fileList) {
    const problems = [];
    Array.from(fileList || []).forEach((file) => {
      if (state.files.length >= limits.max_images) {
        problems.push("Only " + limits.max_images + " photos are allowed.");
        return;
      }
      const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
      if (!ALLOWED_EXT.includes(ext)) {
        problems.push(file.name + ": use JPG, PNG or WebP.");
        return;
      }
      if (file.size > limits.max_image_mb * 1024 * 1024) {
        problems.push(file.name + ": larger than " + limits.max_image_mb + " MB.");
        return;
      }
      if (state.files.some((entry) => entry.file.name === file.name && entry.file.size === file.size)) {
        return;
      }
      state.files.push({ file, url: URL.createObjectURL(file), id: "f" + Math.random().toString(36).slice(2) });
    });

    const alertNode = $("image-alert");
    if (problems.length) {
      alertNode.hidden = false;
      alertNode.innerHTML = "<div><strong>Some files were skipped.</strong><ul>" +
        problems.map((item) => "<li>" + escapeHtml(item) + "</li>").join("") + "</ul></div>";
    } else {
      alertNode.hidden = true;
    }
    renderThumbs();
    updateReadiness();
    clearScript();
  }

  function moveFile(index, delta) {
    const target = index + delta;
    if (target < 0 || target >= state.files.length) return;
    const [entry] = state.files.splice(index, 1);
    state.files.splice(target, 0, entry);
    renderThumbs();
  }

  function renderThumbs() {
    const wrap = $("thumbs");
    wrap.innerHTML = "";
    state.files.forEach((entry, index) => {
      const card = document.createElement("div");
      card.className = "thumb";
      card.draggable = true;
      card.dataset.id = entry.id;
      card.innerHTML =
        '<span class="thumb__order">' + (index + 1) + "</span>" +
        '<img class="thumb__img" alt="' + escapeHtml(entry.file.name) + '" src="' + entry.url + '">' +
        '<div class="thumb__bar">' +
          '<span class="thumb__name" title="' + escapeHtml(entry.file.name) + '">' +
            escapeHtml(entry.file.name) + "</span>" +
          '<button type="button" class="icon-btn" data-move="-1" aria-label="Move earlier">◀</button>' +
          '<button type="button" class="icon-btn" data-move="1" aria-label="Move later">▶</button>' +
          '<button type="button" class="icon-btn icon-btn--danger" data-remove="1" aria-label="Remove">✕</button>' +
        "</div>";

      card.querySelectorAll("[data-move]").forEach((button) => {
        button.addEventListener("click", () => moveFile(index, Number(button.dataset.move)));
      });
      card.querySelector("[data-remove]").addEventListener("click", () => {
        URL.revokeObjectURL(entry.url);
        state.files = state.files.filter((item) => item.id !== entry.id);
        renderThumbs();
        updateReadiness();
        clearScript();
      });

      card.addEventListener("dragstart", () => {
        state.dragId = entry.id;
        card.classList.add("is-dragging");
      });
      card.addEventListener("dragend", () => {
        card.classList.remove("is-dragging");
        state.dragId = null;
      });
      card.addEventListener("dragover", (event) => event.preventDefault());
      card.addEventListener("drop", (event) => {
        event.preventDefault();
        if (!state.dragId || state.dragId === entry.id) return;
        const from = state.files.findIndex((item) => item.id === state.dragId);
        const to = state.files.findIndex((item) => item.id === entry.id);
        if (from < 0 || to < 0) return;
        const [moved] = state.files.splice(from, 1);
        state.files.splice(to, 0, moved);
        renderThumbs();
      });
      wrap.appendChild(card);
    });

    $("thumbs-empty").hidden = state.files.length > 0;
    const count = state.files.length;
    $("image-count").textContent = count + (count === 1 ? " photo" : " photos");
    const fill = $("image-count-fill");
    fill.style.width = Math.min(100, (count / limits.max_images) * 100) + "%";
    fill.style.background = count < limits.min_images ? "var(--danger)" : "var(--accent)";
  }

  const dropzone = $("dropzone");
  const imageInput = $("image-input");
  imageInput.addEventListener("change", () => {
    addFiles(imageInput.files);
    imageInput.value = "";
  });
  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.add("is-over");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-over");
    });
  });
  dropzone.addEventListener("drop", (event) => {
    if (event.dataTransfer && event.dataTransfer.files) addFiles(event.dataTransfer.files);
  });
  $("clear-images").addEventListener("click", () => {
    state.files.forEach((entry) => URL.revokeObjectURL(entry.url));
    state.files = [];
    renderThumbs();
    updateReadiness();
    clearScript();
  });

  /* ---------------------------------------------------------------- script */
  function clearScript() {
    if (!state.script) return;
    state.script = null;
    $("script-json").value = "";
    $("script-card").hidden = true;
    $("script-status").innerHTML = "";
  }

  function scriptStatus(kind, message, extra) {
    $("script-status").innerHTML =
      '<div class="alert alert--' + kind + '"><div><strong>' + escapeHtml(message) + "</strong>" +
      (extra ? "<br>" + escapeHtml(extra) : "") + "</div></div>";
  }

  function renderScript(result, estimate) {
    const card = $("script-card");
    card.hidden = false;
    const badge = $("script-badge");
    const isAi = result.source === "ai";
    badge.textContent = isAi ? "AI generated" : "Template generated";
    badge.className = "badge " + (isAi ? "badge--ai" : "badge--template");
    $("script-headline").textContent = result.data.headline || "";

    const total = estimate && estimate.total ? Number(estimate.total).toFixed(1) + "s" : "";
    const scenes = (result.data.scenes || []).length;
    $("script-estimate").textContent = total ? "≈ " + total + " · " + (scenes + 2) + " scenes" : "";

    let html = "";
    html += '<div class="script-scene"><span class="script-scene__num">1</span><div>' +
      '<div class="script-scene__caption">Opening</div>' +
      '<div class="script-scene__narration">' + escapeHtml(result.data.intro_narration) + "</div></div></div>";
    (result.data.scenes || []).forEach((scene, index) => {
      html += '<div class="script-scene"><span class="script-scene__num">' + (index + 2) + "</span><div>" +
        '<div class="script-scene__caption">' + escapeHtml(scene.caption) + "</div>" +
        '<div class="script-scene__narration">' + escapeHtml(scene.narration) + "</div></div></div>";
    });
    html += '<div class="script-scene"><span class="script-scene__num">' + (scenes + 2) + "</span><div>" +
      '<div class="script-scene__caption">' + escapeHtml(result.data.cta_headline) + "</div>" +
      '<div class="script-scene__narration">' + escapeHtml(result.data.cta_line) + "</div></div></div>";
    $("script-scenes").innerHTML = html;

    if (isAi) {
      const label = (catalog.llm[result.provider] || {}).label || result.provider;
      scriptStatus("ok", "Script written by " + label + " (" + (result.model || "model") + ").",
        "Only the facts you supplied are used; unsupported numbers are rejected automatically.");
    } else {
      scriptStatus("warn", "Built-in template script used.",
        result.fallback_reason || "No AI provider was available.");
    }
  }

  async function generateScript(forceTemplate) {
    const values = formValues();
    if (!values.name || !values.location) {
      toast("Add the property name and location first.", "error");
      setTab("details");
      return;
    }
    if (state.files.length < limits.min_images) {
      toast("Add at least " + limits.min_images + " photos first.", "error");
      setTab("images");
      return;
    }
    const buttons = [$("generate-script"), $("use-template")];
    buttons.forEach((button) => { button.disabled = true; });
    scriptStatus("info", "Writing the script…", forceTemplate
      ? "Template mode never calls an API." : "This may call your configured AI provider once.");
    try {
      const data = await api("/api/script", {
        method: "POST",
        body: {
          property: values,
          image_count: state.files.length,
          force_template: Boolean(forceTemplate),
          tone: values.tone,
        },
      });
      state.script = data.script;
      $("script-json").value = JSON.stringify(data.script.data);
      $("force-template-field").value = forceTemplate ? "1" : "0";
      renderScript(data.script, data.estimate);
      if (data.estimate && data.estimate.warnings && data.estimate.warnings.length) {
        data.estimate.warnings.forEach((warning) => toast(warning, "error"));
      }
    } catch (error) {
      scriptStatus("error", error.message, error.hint || "");
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  $("generate-script").addEventListener("click", () => generateScript(false));
  $("use-template").addEventListener("click", () => generateScript(true));

  const willUse = $("script-will-use");
  if (boot.has_llm_key) {
    willUse.textContent = "An AI key is configured — Generate script will call it once and cache the result.";
  } else {
    willUse.textContent = "No AI key configured, so the template script will be used. You can add a key in Settings.";
  }

  /* -------------------------------------------------------------- narration */
  function fillGeneratorVoices() {
    const engineSelect = $("gen-tts-engine");
    const engine = (catalog.tts || {})[engineSelect.value] || {};
    const voiceSelect = $("gen-tts-voice");
    voiceSelect.innerHTML = (engine.voices || []).map((voice) =>
      '<option value="' + escapeHtml(voice.id) + '">' + escapeHtml(voice.label) + "</option>").join("");
    const preferred = (boot.settings.tts || {}).voice || engine.default_voice;
    if (preferred && (engine.voices || []).some((voice) => voice.id === preferred)) {
      voiceSelect.value = preferred;
    }
    $("gen-tts-note").textContent = engine.needs_internet
      ? "Needs an internet connection. Saved to Settings automatically."
      : "Works offline. Saved to Settings automatically.";
  }

  function initNarration() {
    const engineSelect = $("gen-tts-engine");
    engineSelect.innerHTML = (catalog.tts_order || []).map((id) => {
      const engine = catalog.tts[id];
      return '<option value="' + escapeHtml(id) + '">' + escapeHtml(engine.label) + "</option>";
    }).join("");
    engineSelect.value = (boot.settings.tts || {}).engine || "edge";
    fillGeneratorVoices();

    const persist = async (patch) => {
      try {
        await api("/api/settings", { method: "POST", body: { tts: patch } });
      } catch (error) {
        toast("Voice setting not saved: " + error.message, "error");
      }
    };
    engineSelect.addEventListener("change", () => {
      fillGeneratorVoices();
      persist({ engine: engineSelect.value, voice: $("gen-tts-voice").value });
    });
    $("gen-tts-voice").addEventListener("change", () => {
      persist({ engine: engineSelect.value, voice: $("gen-tts-voice").value });
    });

    $("enable-narration").addEventListener("change", () => {
      $("narration-options").style.opacity = $("enable-narration").checked ? "1" : ".5";
    });
  }

  function narrationText() {
    if (!state.script) return "";
    const data = state.script.data;
    const parts = [data.intro_narration];
    (data.scenes || []).slice(0, 2).forEach((scene) => parts.push(scene.narration));
    return parts.join(" ");
  }

  $("preview-narration").addEventListener("click", async () => {
    const button = $("preview-narration");
    const status = $("narration-preview-status");
    const alertNode = $("narration-alert");
    alertNode.hidden = true;
    if (!state.script) {
      toast("Generate the script first, then preview the narration.", "error");
      setTab("script");
      return;
    }
    button.disabled = true;
    status.textContent = "Generating a short preview…";
    try {
      const data = await api("/api/narration/preview", {
        method: "POST", body: { text: narrationText() },
      });
      const audio = $("narration-audio");
      audio.src = data.url + "?t=" + Date.now();
      audio.hidden = false;
      audio.play().catch(() => {});
      status.textContent = data.message + " (" + Number(data.narration.duration).toFixed(1) + "s)";
      if (data.narration.fallback_used) {
        alertNode.hidden = false;
        alertNode.innerHTML = "<div><strong>Fallback engine used.</strong> " +
          escapeHtml(data.narration.primary_error || "") + "</div>";
      }
    } catch (error) {
      status.textContent = "Preview failed.";
      alertNode.hidden = false;
      let html = "<div><strong>" + escapeHtml(error.message) + "</strong>";
      if (error.hint) html += "<br>" + escapeHtml(error.hint);
      if (error.details && error.details.can_continue_without_narration) {
        html += "<br>You can still create the video with narration turned off.";
      }
      alertNode.innerHTML = html + "</div>";
    } finally {
      button.disabled = false;
    }
  });

  /* ----------------------------------------------------------------- music */
  const musicRadios = document.querySelectorAll('input[name="music_mode"]');

  function setMusicMode(mode) {
    state.musicMode = mode;
    document.querySelectorAll(".choice").forEach((choice) => {
      const selected = choice.dataset.mode === mode;
      choice.classList.toggle("is-selected", selected);
      const radio = choice.querySelector("input");
      if (radio) radio.checked = selected;
    });
    $("music-library-wrap").hidden = mode !== "library";
    $("music-upload-wrap").hidden = mode !== "upload";
    if (mode === "library") loadLibrary();
  }

  musicRadios.forEach((radio) => {
    radio.addEventListener("change", () => setMusicMode(radio.value));
  });

  async function loadLibrary() {
    const list = $("music-library");
    try {
      const data = await api("/api/music");
      $("music-usage").textContent = data.usage.count + " track(s) · " + data.usage.size_label;
      if (!data.tracks.length) {
        list.innerHTML = "";
        $("music-empty").hidden = false;
        return;
      }
      $("music-empty").hidden = true;
      list.innerHTML = data.tracks.map((track) => (
        '<div class="track" data-track="' + track.id + '">' +
          '<label class="check" style="flex:0 0 auto"><input type="radio" name="library_track" value="' +
            track.id + '"></label>' +
          '<div class="track__meta">' +
            '<div class="track__name">' + escapeHtml(track.name) + "</div>" +
            '<div class="track__sub">' + escapeHtml(track.duration_label) + " · " +
              escapeHtml(track.size_label) +
              (track.metadata_label ? " · " + escapeHtml(track.metadata_label) : "") + "</div>" +
          "</div>" +
          '<button type="button" class="icon-btn" data-play aria-label="Preview track">▶</button>' +
          '<button type="button" class="icon-btn icon-btn--danger" data-delete aria-label="Delete track">✕</button>' +
          '<audio preload="none" src="' + escapeHtml(track.audio_url) + '"></audio>' +
        "</div>"
      )).join("");

      list.querySelectorAll(".track").forEach((row) => {
        const trackId = row.dataset.track;
        const audio = row.querySelector("audio");
        row.querySelector("[data-play]").addEventListener("click", () => {
          if (audio.paused) { audio.play(); } else { audio.pause(); }
        });
        row.querySelector('input[type="radio"]').addEventListener("change", () => {
          state.trackId = trackId;
          $("music-id").value = trackId;
        });
        row.querySelector("[data-delete]").addEventListener("click", async () => {
          if (!window.confirm("Remove this track from the library?")) return;
          try {
            await api("/api/music/" + trackId, { method: "DELETE" });
            if (state.trackId === trackId) { state.trackId = null; $("music-id").value = ""; }
            loadLibrary();
            toast("Track removed.", "ok");
          } catch (error) {
            toast(error.message, "error");
          }
        });
      });
    } catch (error) {
      list.innerHTML = '<div class="alert alert--error">' + escapeHtml(error.message) + "</div>";
    }
  }

  const volume = $("music-volume");
  volume.addEventListener("input", () => {
    $("music-volume-label").textContent = volume.value + "%";
  });

  /* ------------------------------------------------------------ generation */
  async function startGeneration() {
    if (state.busy) return;
    const validation = validate(true);
    if (!validation.ok) {
      if (validation.errors.images && !validation.errors.name && !validation.errors.location) {
        setTab("images");
      } else {
        setTab("details");
      }
      toast("Please fix the highlighted fields.", "error");
      return;
    }
    if (state.musicMode === "library" && !state.trackId) {
      toast("Choose a library track, or switch music off.", "error");
      setTab("music");
      return;
    }
    if (state.musicMode === "upload" && !$("music-file").files.length) {
      toast("Choose a music file, or switch music off.", "error");
      setTab("music");
      return;
    }

    const form = new FormData();
    Object.entries(validation.values).forEach(([key, value]) => form.append(key, value || ""));
    state.files.forEach((entry) => form.append("images", entry.file, entry.file.name));
    form.append("music_mode", state.musicMode);
    if (state.musicMode === "library" && state.trackId) form.append("music_id", state.trackId);
    if (state.musicMode === "upload") {
      const file = $("music-file").files[0];
      if (file) form.append("music_file", file, file.name);
      if ($("save-to-library").checked) form.append("save_to_library", "1");
    }
    form.append("music_volume", String(Number(volume.value) / 100));
    form.append("enable_narration", $("enable-narration").checked ? "1" : "0");
    if (state.script) form.append("script_json", JSON.stringify(state.script.data));
    if ($("force-template-field").value === "1") form.append("force_template", "1");

    state.busy = true;
    updateReadiness();
    $("generate-btn").disabled = true;
    setTab("progress");
    renderJob({ status: "queued", stages: [], progress: 0, warnings: [],
                error: null, result: null, cancellable: true });

    try {
      const response = await fetch("/api/generate", {
        method: "POST",
        headers: { "X-CSRF-Token": boot.csrf },
        body: form,
      });
      let data = null;
      try { data = await response.json(); } catch (error) { data = null; }
      if (!response.ok) {
        const payload = (data && data.error) || { message: "Could not start the render." };
        throw Object.assign(new Error(payload.message), payload);
      }
      state.job = data.job;
      startPolling();
    } catch (error) {
      state.busy = false;
      updateReadiness();
      const node = $("job-error");
      node.hidden = false;
      node.innerHTML = "<div><strong>" + escapeHtml(error.message) + "</strong>" +
        (error.hint ? "<br>" + escapeHtml(error.hint) : "") + "</div>";
      $("retry-generate").hidden = false;
      toast(error.message, "error");
    }
  }

  $("generate-btn").addEventListener("click", startGeneration);

  function glyph(status, index) {
    if (status === "done") return "✓";
    if (status === "running") return "●";
    if (status === "failed") return "!";
    if (status === "skipped") return "–";
    return String(index + 1);
  }

  function renderJob(job) {
    const badge = $("job-status-badge");
    const labels = {
      queued: "Queued", running: "Rendering", done: "Finished",
      failed: "Failed", cancelled: "Cancelled",
    };
    badge.textContent = labels[job.status] || job.status;
    badge.className = "badge" + (job.status === "done" ? " badge--ai" : "");

    const percent = job.progress || 0;
    $("progress-fill").style.width = percent + "%";
    $("progress-percent").textContent = percent + "%";

    $("stages").innerHTML = (job.stages || []).map((stage, index) => (
      '<li class="stage" data-status="' + escapeHtml(stage.status) + '">' +
        '<span class="stage__mark">' + glyph(stage.status, index) + "</span>" +
        '<span><span class="stage__label">' + escapeHtml(stage.label) + "</span>" +
          (stage.detail ? '<span class="stage__detail"> — ' + escapeHtml(stage.detail) + "</span>" : "") +
        "</span>" +
        '<span class="small muted">' + escapeHtml(stage.status) + "</span>" +
      "</li>"
    )).join("");

    $("cancel-job").hidden = !job.cancellable;
    $("job-done").hidden = job.status !== "done";
    $("go-to-result").hidden = job.status !== "done";
    $("job-warnings").hidden = !(job.warnings && job.warnings.length);
    if (job.warnings && job.warnings.length) {
      $("job-warnings").innerHTML = "<div><strong>Notes</strong><ul>" +
        job.warnings.map((warning) => "<li>" + escapeHtml(warning) + "</li>").join("") + "</ul></div>";
    }

    const errorNode = $("job-error");
    if (job.error) {
      errorNode.hidden = false;
      let html = "<div><strong>" + escapeHtml(job.error.message) + "</strong>";
      if (job.error.hint) html += "<br>" + escapeHtml(job.error.hint);
      errorNode.innerHTML = html + "</div>";
      $("retry-generate").hidden = false;
      if (job.error.details && job.error.details.can_continue_without_narration) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "btn btn--ghost btn--sm";
        button.style.marginTop = "10px";
        button.textContent = "Continue without narration";
        button.addEventListener("click", () => {
          $("enable-narration").checked = false;
          startGeneration();
        });
        errorNode.querySelector("div").appendChild(button);
      }
    } else {
      errorNode.hidden = true;
    }
    $("retry-generate").hidden = job.status !== "failed" && job.status !== "cancelled";
  }

  function startPolling() {
    if (state.timer) clearInterval(state.timer);
    const tick = async () => {
      if (!state.job) return;
      try {
        const job = await api("/api/jobs/" + state.job.id);
        state.job = job;
        renderJob(job);
        if (["done", "failed", "cancelled"].includes(job.status)) {
          clearInterval(state.timer);
          state.timer = null;
          state.busy = false;
          updateReadiness();
          if (job.status === "done") showResult(job);
        }
      } catch (error) {
        clearInterval(state.timer);
        state.timer = null;
        state.busy = false;
        updateReadiness();
        toast("Lost contact with the render job: " + error.message, "error");
      }
    };
    state.timer = setInterval(tick, 900);
    tick();
  }

  $("cancel-job").addEventListener("click", async () => {
    if (!state.job) return;
    try {
      const result = await api("/api/jobs/" + state.job.id + "/cancel", { method: "POST" });
      toast(result.message, "ok");
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("go-to-result").addEventListener("click", () => setTab("result"));

  $("retry-generate").addEventListener("click", () => {
    $("job-error").hidden = true;
    startGeneration();
  });

  function showResult(job) {
    const result = job.result;
    if (!result) return;
    $("result-empty").hidden = true;
    $("result-ready").hidden = false;

    const video = $("result-video");
    video.src = result.video_url + "?t=" + Date.now();
    if (result.poster_url) video.poster = result.poster_url;

    $("download-link").href = result.download_url;
    $("download-link").setAttribute("download", result.filename || "property-video.mp4");
    $("result-file").textContent = result.filename + " · " + result.size_label;

    const script = result.script || {};
    const narration = result.narration;
    const rows = [
      ["Duration", Number(result.duration).toFixed(1) + " s"],
      ["Resolution", result.resolution],
      ["File size", result.size_label],
      ["Script", script.source === "ai"
        ? "AI · " + (script.provider || "") + " " + (script.model || "")
        : "Template"],
      ["Narration", narration
        ? narration.engine_label + (narration.fallback_used ? " (fallback)" : "")
        : "Off"],
      ["Music", result.music_used ? result.music_name : "None"],
      ["Render time", (result.elapsed_seconds || 0) + " s"],
    ];
    $("result-meta").innerHTML = rows.map(([label, value]) =>
      "<div class=\"meta-row\"><dt>" + escapeHtml(label) + "</dt><dd>" +
      escapeHtml(value) + "</dd></div>").join("");

    if (script.source === "ai") {
      $("result-script-note").innerHTML = "<div><strong>AI script.</strong> Generated by " +
        escapeHtml(script.provider || "your provider") +
        " and fact-checked against your inputs.</div>";
    } else {
      $("result-script-note").innerHTML = "<div><strong>Template script.</strong> " +
        escapeHtml(script.fallback_reason || "Built-in generator used.") + "</div>";
    }

    const warnings = job.warnings || [];
    $("result-warnings").hidden = warnings.length === 0;
    if (warnings.length) {
      $("result-warnings").innerHTML = "<div><strong>Notes</strong><ul>" +
        warnings.map((warning) => "<li>" + escapeHtml(warning) + "</li>").join("") + "</ul></div>";
    }

    setTab("result");
    updateReadiness();
    toast("Video ready — " + Number(result.duration).toFixed(1) + " seconds.", "ok");
  }

  $("start-over").addEventListener("click", () => {
    state.files.forEach((entry) => URL.revokeObjectURL(entry.url));
    state.files = [];
    state.script = null;
    state.job = null;
    state.busy = false;
    state.trackId = null;
    document.getElementById("property-form").reset();
    $("script-json").value = "";
    $("force-template-field").value = "0";
    $("script-card").hidden = true;
    $("script-status").innerHTML = "";
    $("result-ready").hidden = true;
    $("result-empty").hidden = false;
    $("job-error").hidden = true;
    $("job-warnings").hidden = true;
    $("job-done").hidden = true;
    $("narration-audio").hidden = true;
    $("narration-audio").removeAttribute("src");
    $("music-id").value = "";
    renderThumbs();
    updateReadiness();
    setMusicMode("none");
    setTab("details");
  });

  /* ----------------------------------------------------------- sample data */
  async function fillSample() {
    try {
      const sample = await api("/api/sample");
      $("field-name").value = sample.name || "";
      $("field-location").value = sample.location || "";
      $("field-price").value = sample.price || "";
      $("field-type").value = sample.property_type || "";
      $("field-bedrooms").value = sample.bedrooms == null ? "" : sample.bedrooms;
      $("field-features").value = (sample.key_features || []).join("\n");
      $("field-contact").value = sample.contact || "";
      $("field-description").value = sample.description || "";
      clearScript();
      updateReadiness();
      toast("Synthetic sample data loaded. Add the six images from sample_data/images.", "ok");
    } catch (error) {
      toast(error.message, "error");
    }
  }
  $("load-sample").addEventListener("click", fillSample);

  const demoButton = $("demo-generate");
  if (demoButton) {
    demoButton.addEventListener("click", async () => {
      if (state.busy) return;
      demoButton.disabled = true;
      state.busy = true;
      setTab("progress");
      renderJob({ status: "queued", stages: [], progress: 0, warnings: [],
                  error: null, result: null, cancellable: true });
      try {
        const data = await api("/api/demo", { method: "POST" });
        state.job = data.job;
        startPolling();
      } catch (error) {
        state.busy = false;
        toast(error.message, "error");
      } finally {
        demoButton.disabled = false;
      }
    });
  }

  /* ------------------------------------------------------------------ init */
  initNarration();
  renderThumbs();
  updateReadiness();
  setTab("details");
})();
