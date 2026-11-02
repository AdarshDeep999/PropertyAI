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
    musicMode: "builtin",
    trackId: null,
    builtinId: "morning-light",
    builtin: [],
    startedAt: null,
    elapsedTimer: null,
    dragId: null,
  };

  const SESSION_KEY = "pvs.session.v1";
  const JOB_KEY = "pvs.job.v1";

  if (!document.getElementById("property-form")) return; // not the generator page

  /* ------------------------------------------------------------------ tabs */
  function setTab(name, scroll) {
    state.tab = name;
    document.body.dataset.step = name;
    document.querySelectorAll(".step").forEach((button) => {
      button.setAttribute("aria-selected", button.dataset.tab === name ? "true" : "false");
    });
    document.querySelectorAll(".panel").forEach((panel) => {
      panel.classList.toggle("is-active", panel.id === "panel-" + name);
    });
    const index = TABS.indexOf(name);
    const fill = $("stepper-fill");
    if (fill) fill.style.width = (100 * Math.max(0, index) / (TABS.length - 1)) + "%";
    // Progress and Preview carry their own actions; the floating bar would sit
    // on top of the player and the download button.
    const actionBar = document.querySelector(".action-bar");
    if (actionBar) actionBar.hidden = name === "progress" || name === "result";
    $("btn-prev").disabled = index <= 0;
    $("btn-next").disabled = index >= LAST_FORM_TAB;
    if (scroll === false) return;
    // Bring the step controls to the top rather than the hero: the panel the
    // user just asked for should be what they see.
    const stepper = document.querySelector(".stepper");
    const top = stepper
      ? stepper.getBoundingClientRect().top + window.scrollY - 74
      : 0;
    window.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
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

  /* ------------------------------------------------------------- summary rail */
  function setSummary(nodeId, value, emptyLabel) {
    const node = $(nodeId);
    if (!node) return;
    node.textContent = value || emptyLabel;
    node.classList.toggle("is-empty", !value);
  }

  function voiceLabel() {
    const engineSelect = $("gen-tts-engine");
    const voiceSelect = $("gen-tts-voice");
    if (!engineSelect) return "";
    const engine = (catalog.tts || {})[engineSelect.value] || {};
    const voice = voiceSelect && voiceSelect.selectedOptions[0]
      ? voiceSelect.selectedOptions[0].textContent.split(" — ")[0]
      : "";
    const engineName = (engine.label || "").split(" (")[0];
    if (!engineName) return voice;
    return voice ? engineName.split(" neural")[0] + " · " + voice : engineName;
  }

  function updateSummary() {
    const values = formValues();
    const count = state.files.length;
    setSummary("sum-property", values.name, "Not set");
    setSummary("sum-photos", count ? count + " of " + limits.max_images : "", "None yet");
    setSummary("sum-script", state.script
      ? (state.script.source === "ai" ? "AI written" : "Template written")
      : "", "Not written");
    setSummary("sum-voice", $("enable-narration") && !$("enable-narration").checked
      ? "Off" : voiceLabel(), "Pending");

    let music = "";
    if (state.musicMode === "none") music = "No music";
    else if (state.musicMode === "builtin") {
      const track = (state.builtin || []).find((item) => item.id === state.builtinId);
      music = track ? track.name : "Studio score";
    } else if (state.musicMode === "library") {
      music = state.trackId ? "From library" : "";
    } else if (state.musicMode === "upload") {
      const file = $("music-file") && $("music-file").files[0];
      music = file ? file.name : "";
    }
    setSummary("sum-music", music, "Not chosen");

    const photoChip = $("photo-count-chip");
    if (photoChip) photoChip.textContent = count + " / " + limits.max_images;
    const scriptChip = $("script-source-chip");
    if (scriptChip) {
      scriptChip.textContent = state.script
        ? (state.script.source === "ai" ? "AI script" : "Template script")
        : (boot.has_llm_key ? "AI ready" : "Template ready");
    }

    const finished = Boolean(state.job && state.job.status === "done" && state.job.result);
    const complete = {
      details: Boolean(values.name) && Boolean(values.location),
      images: count >= limits.min_images && count <= limits.max_images,
      script: Boolean(state.script),
      music: state.musicMode === "none" ||
        (state.musicMode === "builtin" && Boolean(state.builtinId)) ||
        (state.musicMode === "library" && Boolean(state.trackId)) ||
        (state.musicMode === "upload" && Boolean($("music-file") && $("music-file").files.length)),
      progress: finished,
      result: finished,
    };
    document.querySelectorAll(".step").forEach((button) => {
      button.classList.toggle("is-complete", Boolean(complete[button.dataset.tab]));
    });
  }

  function updateReadiness() {
    const values = formValues();
    const ready = Boolean(values.name) && Boolean(values.location) &&
      state.files.length >= limits.min_images && state.files.length <= limits.max_images;
    const button = $("generate-btn");
    button.disabled = !ready || state.busy;
    const couldRender = ready && !state.busy;
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

    const sideNote = $("readiness-side");
    if (sideNote) {
      sideNote.textContent = state.busy
        ? "A film is rendering — watch the render step."
        : showingFinishedVideo
          ? "Your film is ready — open the preview to watch or download it."
          : couldRender
          ? "Everything is in place. Press Generate video when the cut feels right."
          : "Add a name, a location and " + limits.min_images +
            " photos to unlock the render.";
    }
    updateSummary();
  }

  ["field-name", "field-location", "field-price", "field-bedrooms", "field-type",
   "field-features", "field-contact", "field-description"].forEach((id) => {
    const node = $(id);
    if (!node) return;
    node.addEventListener("input", () => {
      updateReadiness();
      clearScript(); // facts may have changed: never render a stale script
      saveSession();
    });
    node.addEventListener("blur", () => {
      const values = formValues();
      if (id === "field-name") setFieldError("name", values.name.length >= 2 ? "" : "Enter the property name.");
      if (id === "field-location") setFieldError("location", values.location.length >= 2 ? "" : "Enter the location.");
    });
  });

  /* --------------------------------------------------------------- session */
  function saveSession() {
    const payload = {
      fields: formValues(),
      music: {
        mode: state.musicMode,
        builtinId: state.builtinId,
        trackId: state.trackId,
        volume: $("music-volume") ? $("music-volume").value : "30",
      },
      narration: $("enable-narration") ? $("enable-narration").checked : true,
      script: state.script ? { data: state.script.data, source: state.script.source } : null,
      force_template: $("force-template-field") ? $("force-template-field").value : "0",
      saved_at: Date.now(),
    };
    try { localStorage.setItem(SESSION_KEY, JSON.stringify(payload)); } catch (error) { /* private mode */ }
  }

  function restoreSession() {
    let payload = null;
    try { payload = JSON.parse(localStorage.getItem(SESSION_KEY) || "null"); } catch (error) { payload = null; }
    if (!payload || typeof payload !== "object") return;

    const fields = payload.fields || {};
    const targets = {
      name: "field-name", location: "field-location", price: "field-price",
      property_type: "field-type", bedrooms: "field-bedrooms",
      features: "field-features", contact: "field-contact", description: "field-description",
    };
    Object.entries(targets).forEach(([key, id]) => {
      const node = $(id);
      if (node && fields[key] != null) node.value = fields[key];
    });
    if (fields.tone && $("tone")) $("tone").value = fields.tone;
    if (payload.narration === false && $("enable-narration")) $("enable-narration").checked = false;

    const music = payload.music || {};
    if (music.volume != null && $("music-volume")) {
      $("music-volume").value = music.volume;
      $("music-volume-label").textContent = music.volume + "%";
    }
    if (music.builtinId) state.builtinId = music.builtinId;
    if (music.trackId) state.trackId = music.trackId;
    state.musicMode = music.mode || "builtin";

    if (payload.script && payload.script.data) {
      state.script = { data: payload.script.data, source: payload.script.source || "template" };
      $("script-json").value = JSON.stringify(payload.script.data);
      $("force-template-field").value = payload.force_template || "0";
      renderScript(state.script, null);
    }
  }

  async function restoreJob() {
    let jobId = null;
    try { jobId = localStorage.getItem(JOB_KEY); } catch (error) { jobId = null; }
    if (!jobId) return;
    try {
      const job = await api("/api/jobs/" + encodeURIComponent(jobId));
      state.job = job;
      if (job.status === "done") {
        showResult(job);
      } else if (job.status === "queued" || job.status === "running") {
        state.busy = true;
        state.startedAt = Date.parse(job.created_at) || Date.now();
        setTab("progress");
        renderJob(job);
        startElapsed();
        startPolling();
        toast("Your render is still running — picking it back up.", "ok");
      } else {
        forgetJob();
      }
    } catch (error) {
      forgetJob();
    }
  }

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
    updateSummary();
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
    updateSummary();
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
      updateSummary();
    });
    $("gen-tts-voice").addEventListener("change", () => {
      persist({ engine: engineSelect.value, voice: $("gen-tts-voice").value });
      updateSummary();
    });

    $("enable-narration").addEventListener("change", () => {
      $("narration-options").style.opacity = $("enable-narration").checked ? "1" : ".5";
      updateSummary();
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
    $("music-builtin-wrap").hidden = mode !== "builtin";
    $("music-library-wrap").hidden = mode !== "library";
    $("music-upload-wrap").hidden = mode !== "upload";
    if (mode === "library") loadLibrary();
    if (mode === "builtin") ensureBuiltinLoaded();
    saveSession();
    updateSummary();
  }

  musicRadios.forEach((radio) => {
    radio.addEventListener("change", () => setMusicMode(radio.value));
  });

  function bars(seed) {
    let html = "";
    for (let index = 0; index < 20; index += 1) {
      const height = 24 + ((index * 37 + seed * 13) % 66);
      html += '<span style="height:' + height + '%"></span>';
    }
    return html;
  }

  function renderBuiltin(tracks) {
    const list = $("builtin-list");
    if (!list) return;
    state.builtin = tracks || [];
    if (!state.builtin.length) {
      list.innerHTML = '<div class="empty-state">No soundtracks in this install — upload a track instead.</div>';
      return;
    }
    list.innerHTML = state.builtin.map((track) => (
      '<div class="sound" data-sound="' + escapeHtml(track.id) + '" role="button" tabindex="0" aria-pressed="false">' +
        '<div class="sound__head"><span class="sound__name">' + escapeHtml(track.name) + "</span>" +
          '<span class="sound__check">✓</span></div>' +
        '<span class="sound__mood">' + escapeHtml(track.mood) + " · " +
          escapeHtml(track.duration_label) + "</span>" +
        '<span class="sound__desc">' + escapeHtml(track.description) + "</span>" +
        '<span class="sound__bar" aria-hidden="true">' + bars(track.name.length) + "</span>" +
        '<button type="button" class="sound__play" data-preview aria-label="Preview ' +
          escapeHtml(track.name) + '">▶</button>' +
        '<audio preload="none" src="' + escapeHtml(track.audio_url) + '"></audio>' +
      "</div>"
    )).join("");

    const cards = list.querySelectorAll(".sound");
    cards.forEach((card) => {
      const id = card.dataset.sound;
      const audio = card.querySelector("audio");
      const playButton = card.querySelector("[data-preview]");
      const choose = () => selectBuiltin(id);
      card.addEventListener("click", choose);
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(); }
      });
      playButton.addEventListener("click", (event) => {
        event.stopPropagation();
        if (audio.paused) {
          cards.forEach((other) => {
            if (other === card) return;
            const otherAudio = other.querySelector("audio");
            if (otherAudio) { otherAudio.pause(); otherAudio.currentTime = 0; }
          });
          audio.currentTime = 0;
          audio.play().catch(() => {});
        } else {
          audio.pause();
        }
      });
      audio.addEventListener("play", () => {
        card.classList.add("is-playing");
        playButton.textContent = "❚❚";
      });
      const stop = () => {
        card.classList.remove("is-playing");
        playButton.textContent = "▶";
      };
      audio.addEventListener("pause", stop);
      audio.addEventListener("ended", stop);
    });
    selectBuiltin(state.builtinId || state.builtin[0].id);
  }

  function selectBuiltin(id) {
    state.builtinId = id;
    $("music-id").value = id;
    document.querySelectorAll(".sound").forEach((card) => {
      const on = card.dataset.sound === id;
      card.classList.toggle("is-selected", on);
      card.setAttribute("aria-pressed", on ? "true" : "false");
    });
    saveSession();
    updateSummary();
  }

  let builtinPromise = null;
  function ensureBuiltinLoaded() {
    if (builtinPromise) return builtinPromise;
    builtinPromise = api("/api/music").then((data) => {
      renderBuiltin(data.builtin || []);
      return data;
    }).catch(() => {
      builtinPromise = null;
      $("builtin-list").innerHTML =
        '<div class="alert alert--error"><div>Soundtracks could not be loaded — you can still upload a track.</div></div>';
    });
    return builtinPromise;
  }

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
          updateSummary();
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
    saveSession();
  });

  const musicFileInput = $("music-file");
  if (musicFileInput) musicFileInput.addEventListener("change", updateSummary);

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
    if (state.musicMode === "builtin" && !state.builtinId) {
      toast("Choose a soundtrack, or switch music off.", "error");
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
    if (state.musicMode === "builtin" && state.builtinId) form.append("music_id", state.builtinId);
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
    state.startedAt = null;
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
      rememberJob(data.job.id);
      state.startedAt = Date.now();
      startElapsed();
      startPolling();
    } catch (error) {
      state.busy = false;
      stopElapsed();
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

    const phase = $("job-phase");
    if (phase) {
      const running = (job.stages || []).find((stage) => stage.status === "running");
      const settled = { done: "Finished", failed: "Stopped", cancelled: "Cancelled" };
      phase.textContent = running
        ? (running.detail || running.label)
        : (settled[job.status] || "Working…");
    }

    $("stages").innerHTML = (job.stages || []).map((stage, index) => (
      '<li class="stage" data-status="' + escapeHtml(stage.status) + '">' +
        '<span class="stage__mark">' + glyph(stage.status, index) + "</span>" +
        '<span><span class="stage__label">' + escapeHtml(stage.label) + "</span>" +
          (stage.detail ? '<span class="stage__detail"> — ' + escapeHtml(stage.detail) + "</span>" : "") +
        "</span>" +
        '<span class="small muted">' +
          escapeHtml(stage.status.charAt(0).toUpperCase() + stage.status.slice(1)) + "</span>" +
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
    let misses = 0;
    const tick = async () => {
      if (!state.job) return;
      try {
        const job = await api("/api/jobs/" + state.job.id);
        misses = 0;
        state.job = job;
        renderJob(job);
        if (["done", "failed", "cancelled"].includes(job.status)) {
          clearInterval(state.timer);
          state.timer = null;
          state.busy = false;
          stopElapsed();
          updateReadiness();
          if (job.status === "done") showResult(job);
        }
      } catch (error) {
        // A free host can restart mid-render, which surfaces as a 502. Keep
        // trying for a while before declaring the job lost: the page should
        // survive a hiccup instead of dumping the user back to step one.
        if (error.status === 404) {
          clearInterval(state.timer);
          state.timer = null;
          state.busy = false;
          stopElapsed();
          forgetJob();
          updateReadiness();
          const node = $("job-error");
          node.hidden = false;
          node.innerHTML = "<div><strong>This render was interrupted.</strong> " +
            "The server restarted before it finished, so the video was not saved. " +
            "Your details are kept — re-add the photos if they are no longer listed, " +
            "then press <em>Try again</em>.</div>";
          $("retry-generate").hidden = false;
          $("job-status-badge").textContent = "Interrupted";
          return;
        }
        misses += 1;
        if (misses <= 40) {
          $("job-phase").textContent = "Reconnecting to the server… (" + error.message + ")";
          return;
        }
        clearInterval(state.timer);
        state.timer = null;
        state.busy = false;
        stopElapsed();
        updateReadiness();
        const node = $("job-error");
        node.hidden = false;
        node.innerHTML = "<div><strong>Lost contact with the server.</strong> " +
          escapeHtml(error.message) + "<br>Your video may still be rendering — reload " +
          "this page in a minute to check.</div>";
        $("retry-generate").hidden = false;
      }
    };
    state.timer = setInterval(tick, 900);
    tick();
  }

  function rememberJob(id) {
    try { localStorage.setItem(JOB_KEY, id); } catch (error) { /* private mode */ }
  }

  function forgetJob() {
    try { localStorage.removeItem(JOB_KEY); } catch (error) { /* private mode */ }
  }

  function startElapsed() {
    stopElapsed();
    if (!state.startedAt) state.startedAt = Date.now();
    const paint = () => {
      const node = $("job-elapsed");
      if (!node) return;
      const seconds = Math.max(0, Math.round((Date.now() - state.startedAt) / 1000));
      node.textContent = Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
    };
    paint();
    state.elapsedTimer = setInterval(paint, 1000);
  }

  function stopElapsed() {
    if (state.elapsedTimer) clearInterval(state.elapsedTimer);
    state.elapsedTimer = null;
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
    stopElapsed();
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
        ? "AI · " + (script.model || script.provider || "")
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
    forgetJob();
    try { localStorage.removeItem(SESSION_KEY); } catch (error) { /* private mode */ }
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
        rememberJob(data.job.id);
        state.startedAt = Date.now();
        startElapsed();
        startPolling();
      } catch (error) {
        state.busy = false;
        stopElapsed();
        toast(error.message, "error");
      } finally {
        demoButton.disabled = false;
      }
    });
  }

  /* ------------------------------------------------------------------ init */
  initNarration();
  restoreSession();
  setMusicMode(state.musicMode);
  if ($("tone")) $("tone").addEventListener("change", saveSession);
  if ($("enable-narration")) $("enable-narration").addEventListener("change", saveSession);
  renderThumbs();
  updateReadiness();
  // #step-photos (and friends) opens the studio on that step — useful after a
  // refresh or when the URL is shared between agents.
  const requested = (window.location.hash || "").replace("#step-", "");
  setTab(TABS.includes(requested) ? requested : "details", false);
  restoreJob();
})();
