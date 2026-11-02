/* Shared front-end helpers + the settings experience.
   Loaded on both pages; app.js uses window.Studio for API calls and toasts.
   Every request still goes through the same Flask endpoints as before — only
   the presentation and the progressive disclosure are new. */
(function () {
  "use strict";

  const bootEl = document.getElementById("bootstrap");
  const boot = bootEl ? JSON.parse(bootEl.textContent) : { csrf: "", settings: {}, catalog: {}, secrets: {} };
  const state = {
    settings: boot.settings || {},
    catalog: boot.catalog || {},
    secrets: boot.secrets || {},
  };

  const $ = (id) => document.getElementById(id);

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  async function api(path, options) {
    const opts = options || {};
    const request = {
      method: opts.method || "GET",
      headers: { "X-CSRF-Token": boot.csrf, Accept: "application/json" },
    };
    if (opts.body !== undefined && opts.body !== null) {
      request.headers["Content-Type"] = "application/json";
      request.body = JSON.stringify(opts.body);
    }
    const response = await fetch(path, request);
    let data = null;
    try { data = await response.json(); } catch (err) { data = null; }
    if (!response.ok) {
      const payload = (data && data.error) || {
        message: "The request failed (" + response.status + ").",
      };
      const failure = new Error(payload.message || "Request failed");
      failure.code = payload.code;
      failure.hint = payload.hint;
      failure.details = payload.details;
      failure.status = response.status;
      throw failure;
    }
    return data;
  }

  function toast(message, kind) {
    const region = $("toast-region");
    if (!region) return;
    const item = document.createElement("div");
    item.className = "toast" + (kind ? " toast--" + kind : "");
    item.textContent = message;
    region.appendChild(item);
    setTimeout(() => { item.remove(); }, kind === "error" ? 7000 : 4200);
  }

  window.Studio = { boot, state, api, toast, escapeHtml, $ };

  /* ------------------------------------------------------------ section nav */
  function initSectionNav() {
    const tabs = Array.from(document.querySelectorAll("[data-settings-tab]"));
    if (!tabs.length) return;

    const show = (key, push) => {
      const known = tabs.some((tab) => tab.dataset.settingsTab === key);
      const target = known ? key : tabs[0].dataset.settingsTab;
      tabs.forEach((tab) => {
        tab.setAttribute("aria-selected", tab.dataset.settingsTab === target ? "true" : "false");
      });
      document.querySelectorAll(".settings__section").forEach((section) => {
        section.classList.toggle("is-active", section.id === "settings-" + target);
      });
      if (push && window.history && window.history.replaceState) {
        window.history.replaceState(null, "", "#" + target);
      }
    };

    tabs.forEach((tab) => {
      tab.addEventListener("click", () => show(tab.dataset.settingsTab, true));
    });

    const fromHash = (window.location.hash || "").replace("#", "");
    show(fromHash || tabs[0].dataset.settingsTab, false);
  }

  /* ---------------------------------------------------------------- secrets */
  function secretLabel(name) {
    const labels = {
      gemini: "Gemini key",
      openai_compat: "OpenAI-compatible key",
      openai_tts: "OpenAI speech key",
      elevenlabs: "ElevenLabs key",
    };
    return labels[name] || name;
  }

  function secretStatusHtml(info) {
    const dot = info.configured ? "status-dot status-dot--on" : "status-dot status-dot--off";
    let text;
    if (info.configured && info.source === "environment") {
      text = "Using <code>" + escapeHtml(info.env_var || "environment variable") +
             "</code> · " + escapeHtml(info.masked);
    } else if (info.configured) {
      text = "Saved on the server · " + escapeHtml(info.masked);
    } else {
      text = "No key saved";
    }
    return '<span class="' + dot + '"></span><span>' + text + "</span>";
  }

  function renderConfigState(elementId, info, configuredLabel, missingLabel) {
    const node = $(elementId);
    if (!node) return;
    node.textContent = info && info.configured ? (configuredLabel || "Configured") : (missingLabel || "Missing key");
    node.classList.toggle("config-state--on", Boolean(info && info.configured));
    node.classList.toggle("config-state--warn", !(info && info.configured));
  }

  function renderSecret(name) {
    const info = state.secrets[name] || { configured: false };
    document.querySelectorAll('[data-secret="' + name + '"]').forEach((node) => {
      if (node.tagName === "INPUT") return;
      node.innerHTML = secretStatusHtml(info);
    });
    if (name === "openai_tts") renderConfigState("cred-openai-state", info, "Configured", "Not configured");
    if (name === "elevenlabs") renderConfigState("cred-elevenlabs-state", info, "Configured", "Not configured");
    if (name === "gemini" || name === "openai_compat") {
      const meta = (state.catalog.llm || {})[currentProvider()] || {};
      if (meta.secret === name) renderConfigState("llm-config-state", info, "Connected", "Missing key");
    }
    const removeButton = $("llm-remove-key");
    const provider = (state.catalog.llm || {})[currentProvider()] || {};
    if (removeButton && provider.secret === name) {
      removeButton.disabled = !(state.secrets[name] && state.secrets[name].removable);
    }
  }

  function refreshAllSecrets() {
    Object.keys(state.secrets).forEach(renderSecret);
  }

  async function saveSecret(name, value, statusNode) {
    try {
      const result = await api("/api/settings/secret", { method: "POST", body: { name, value } });
      state.secrets[name] = result.secret;
      renderSecret(name);
      const input = document.querySelector('[data-secret-input="' + name + '"]');
      if (input) input.value = "";
      if (statusNode) statusNode.textContent = result.message;
      toast(result.message || "Key saved", "ok");
      return true;
    } catch (error) {
      toast(error.message + (error.hint ? " " + error.hint : ""), "error");
      if (statusNode) statusNode.textContent = error.message;
      return false;
    }
  }

  async function removeSecret(name, statusNode) {
    try {
      const result = await api("/api/settings/secret/" + encodeURIComponent(name),
                               { method: "DELETE" });
      state.secrets[name] = result.secret;
      renderSecret(name);
      if (statusNode) statusNode.textContent = result.message;
      toast(result.message || "Key removed", "ok");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  /* ----------------------------------------------------------------- providers */
  function currentProvider() {
    const select = $("llm-provider");
    return select ? select.value : (state.settings.llm || {}).provider || "gemini";
  }

  function fillVoiceSelect(select, engineId, preferred) {
    if (!select) return;
    const engine = (state.catalog.tts || {})[engineId];
    if (!engine) { select.innerHTML = ""; return; }
    const voices = engine.voices || [];
    select.innerHTML = voices.map((voice) => (
      '<option value="' + escapeHtml(voice.id) + '">' + escapeHtml(voice.label) + "</option>"
    )).join("");
    const wanted = preferred || engine.default_voice;
    if (wanted && voices.some((voice) => voice.id === wanted)) select.value = wanted;
    else if (voices.length) select.value = voices[0].id;
  }

  function setReveal(node, open) {
    if (!node) return;
    node.classList.toggle("is-open", Boolean(open));
  }

  function probeLlmKeyField(providerId) {
    const meta = (state.catalog.llm || {})[providerId] || {};
    const keyStatus = $("llm-key-status");
    if (keyStatus && meta.secret) {
      keyStatus.dataset.secret = meta.secret;
      renderSecret(meta.secret);
    }
    const label = document.querySelector('label[for="llm-key"]');
    if (label) label.textContent = (meta.label || "Provider") + " API key";
    const keyInput = $("llm-key");
    if (keyInput) {
      keyInput.placeholder = meta.requires_key
        ? "Paste your " + (meta.label || "provider") + " key"
        : "Optional — local servers need no key";
    }
  }

  function renderPresets(providerId) {
    const wrap = $("llm-presets");
    if (!wrap) return;
    const meta = (state.catalog.llm || {})[providerId] || {};
    const presets = meta.presets || [];
    if (!presets.length) { wrap.innerHTML = ""; return; }
    wrap.innerHTML = presets.map((preset, index) => (
      '<button type="button" class="btn btn--ghost btn--sm" data-preset="' + index + '">' +
      escapeHtml(preset.label) + "</button>"
    )).join("");
    wrap.querySelectorAll("[data-preset]").forEach((button) => {
      button.addEventListener("click", () => {
        const preset = presets[Number(button.dataset.preset)];
        if (!preset) return;
        const base = $("llm-base-url");
        const model = $("llm-model");
        if (base) base.value = preset.base_url || "";
        if (model && preset.model) model.value = preset.model;
        toast(preset.label + " preset applied.", "ok");
      });
    });
  }

  function syncProviderUi() {
    const select = $("llm-provider");
    if (!select) return;
    const meta = (state.catalog.llm || {})[select.value] || {};
    const notes = $("llm-provider-note");
    if (notes) notes.textContent = meta.notes || "";

    const list = $("llm-model-options");
    if (list) {
      list.innerHTML = (meta.models || []).map((model) =>
        '<option value="' + escapeHtml(model) + '"></option>').join("");
    }

    const needsBase = select.value === "openai_compatible";
    setReveal($("llm-base-url-wrap"), needsBase);
    if (needsBase) renderPresets(select.value);

    probeLlmKeyField(select.value);
    const help = $("llm-key-help");
    if (help) {
      help.textContent = meta.requires_key
        ? "Stored on the server, shown only as a mask, never sent to the browser."
        : "Local servers such as Ollama accept an empty key.";
    }
  }

  function syncTtsUi() {
    const engineSelect = $("tts-engine");
    if (!engineSelect) return;
    const engineId = engineSelect.value;
    const engine = (state.catalog.tts || {})[engineId] || {};
    fillVoiceSelect($("tts-voice"), engineId, (state.settings.tts || {}).voice);

    const note = $("tts-engine-note");
    if (note) {
      let text = engine.notes || "";
      if (engine.requires_key) text += " Requires an API key.";
      note.textContent = text;
    }

    const fallbackSelect = $("tts-fallback-engine");
    const fallbackId = fallbackSelect ? fallbackSelect.value : "none";
    const needsOpenai = engineId === "openai" || fallbackId === "openai";
    const needsEleven = engineId === "elevenlabs" || fallbackId === "elevenlabs";
    setReveal($("cred-openai"), needsOpenai);
    setReveal($("cred-elevenlabs"), needsEleven);

    const keyless = $("tts-keyless-note");
    if (keyless) keyless.hidden = needsOpenai || needsEleven || Boolean(engine.requires_key);

    ["openai_tts", "elevenlabs"].forEach(renderSecret);
  }

  /* -------------------------------------------------------------------- tests */
  function setTestOut(node, kind, message) {
    if (!node) return;
    node.textContent = message;
    node.classList.toggle("is-ok", kind === "ok");
    node.classList.toggle("is-error", kind === "error");
    node.classList.toggle("is-busy", kind === "busy");
  }

  async function testLlm() {
    const resultNode = $("llm-test-result");
    setTestOut(resultNode, "busy", "Testing the connection…");
    try {
      const result = await api("/api/settings/test", {
        method: "POST",
        body: {
          kind: "llm",
          provider: currentProvider(),
          model: ($("llm-model") || {}).value || "",
          base_url: ($("llm-base-url") || {}).value || "",
        },
      });
      setTestOut(resultNode, result.ok ? "ok" : "error",
        (result.ok ? "✓ " : "✕ ") + result.message);
      toast(result.message, result.ok ? "ok" : "error");
    } catch (error) {
      setTestOut(resultNode, "error", "✕ " + error.message);
    }
  }

  async function testTts() {
    const resultNode = $("tts-test-result");
    const engineSelect = $("tts-engine");
    if (!engineSelect) return;
    setTestOut(resultNode, "busy", "Generating a short sample…");
    try {
      const result = await api("/api/settings/test", {
        method: "POST",
        body: { kind: "tts", engine: engineSelect.value, voice: ($("tts-voice") || {}).value },
      });
      setTestOut(resultNode, result.ok ? "ok" : "error",
        (result.ok ? "✓ " : "✕ ") + result.message);
    } catch (error) {
      setTestOut(resultNode, "error", "✕ " + error.message);
    }
  }

  /* -------------------------------------------------------------------- save */
  async function saveSettings() {
    const button = $("save-settings");
    const resultNode = $("save-settings-result");
    const llm = {
      provider: currentProvider(),
      model: (($("llm-model") || {}).value || "").trim(),
      force_template: Boolean(($("force-template") || {}).checked),
    };
    if (llm.provider === "openai_compatible") {
      llm.base_url = (($("llm-base-url") || {}).value || "").trim();
    }
    const tts = {
      engine: ($("tts-engine") || {}).value,
      voice: ($("tts-voice") || {}).value,
      fallback_engine: ($("tts-fallback-engine") || {}).value || "none",
      speed: parseFloat((($("tts-speed") || {}).value) || "1"),
    };
    const video = {};
    const volume = $("default-music-volume");
    if (volume) video.music_volume = Number(volume.value) / 100;
    const tone = $("default-tone");
    if (tone) video.tone = tone.value;

    if (button) button.disabled = true;
    if (resultNode) resultNode.textContent = "Saving…";
    try {
      const payload = { llm, tts };
      if (Object.keys(video).length) payload.video = video;
      const result = await api("/api/settings", { method: "POST", body: payload });
      state.settings = result.settings;
      if (tone && result.settings.video) tone.value = result.settings.video.tone;
      if (resultNode) resultNode.textContent = "Saved.";
      toast("Settings saved.", "ok");
    } catch (error) {
      if (resultNode) resultNode.textContent = "Not saved: " + error.message;
      toast(error.message, "error");
    } finally {
      if (button) button.disabled = false;
    }
  }

  /* -------------------------------------------------------------- data panels */
  async function loadMusicPanels() {
    const listNode = $("settings-builtin-list");
    if (!listNode && !$("settings-library-count")) return;
    try {
      const data = await api("/api/music");
      if (listNode) {
        const tracks = data.builtin || [];
        listNode.innerHTML = tracks.length
          ? tracks.map((track) => (
            '<div class="track">' +
              '<div class="track__meta">' +
                '<div class="track__name">' + escapeHtml(track.name) + "</div>" +
                '<div class="track__sub">' + escapeHtml(track.mood) + " · " +
                  escapeHtml(track.duration_label) + "</div>" +
              "</div>" +
              '<audio controls preload="none" src="' + escapeHtml(track.audio_url) + '"></audio>' +
            "</div>"
          )).join("")
          : '<div class="empty-state">No studio scores are installed.</div>';
        const chip = $("settings-music-count");
        if (chip) chip.textContent = tracks.length + " tracks";
      }
      if ($("settings-library-count")) {
        $("settings-library-count").textContent = (data.usage && data.usage.count) || "0";
        $("settings-library-size").textContent = (data.usage && data.usage.size_label) || "0 MB";
      }
    } catch (error) {
      if (listNode) {
        listNode.innerHTML = '<div class="alert alert--error"><div>' +
          escapeHtml(error.message) + "</div></div>";
      }
    }
  }

  async function loadHealth() {
    const list = $("health-list");
    if (!list) return;
    try {
      const data = await api("/api/health");
      list.innerHTML = (data.checks || []).map((check) => (
        '<div class="track">' +
          '<span class="status-dot ' + (check.ok ? "status-dot--on" : "status-dot--off") + '"></span>' +
          '<div class="track__meta">' +
            '<div class="track__name">' + escapeHtml(check.name) + "</div>" +
            '<div class="track__sub">' + escapeHtml(check.detail) + "</div>" +
          "</div>" +
        "</div>"
      )).join("");
    } catch (error) {
      list.innerHTML = '<div class="alert alert--error"><div>' + escapeHtml(error.message) + "</div></div>";
    }
  }

  /* ------------------------------------------------------------------- wiring */
  function wireSettingsForm() {
    const provider = $("llm-provider");
    if (!provider) return;

    provider.addEventListener("change", () => {
      const meta = (state.catalog.llm || {})[provider.value] || {};
      const model = $("llm-model");
      if (model && !model.value.trim()) model.value = meta.default_model || "";
      const base = $("llm-base-url");
      if (base && provider.value === "openai_compatible" && !base.value.trim()) {
        const preset = (meta.presets || [])[0];
        if (preset) base.value = preset.base_url;
      }
      syncProviderUi();
    });

    const saveKeyBtn = $("llm-save-key");
    if (saveKeyBtn) {
      saveKeyBtn.addEventListener("click", () => {
        const meta = (state.catalog.llm || {})[provider.value] || {};
        const input = $("llm-key");
        const value = input ? input.value.trim() : "";
        if (!value) { toast("Paste a key first.", "error"); return; }
        saveSecret(meta.secret, value, $("llm-test-result")).then((saved) => {
          if (saved && input) input.value = "";  // never leave a key sitting in the field
        });
      });
    }

    const removeKeyBtn = $("llm-remove-key");
    if (removeKeyBtn) {
      removeKeyBtn.addEventListener("click", () => {
        const meta = (state.catalog.llm || {})[provider.value] || {};
        if (meta.secret) removeSecret(meta.secret, $("llm-test-result"));
      });
    }

    const testBtn = $("llm-test");
    if (testBtn) testBtn.addEventListener("click", testLlm);

    const engineSelect = $("tts-engine");
    if (engineSelect) {
      engineSelect.addEventListener("change", syncTtsUi);
      fillVoiceSelect($("tts-voice"), engineSelect.value, state.settings.tts.voice);
    }

    const fallbackSelect = $("tts-fallback-engine");
    if (fallbackSelect) fallbackSelect.addEventListener("change", syncTtsUi);

    const speed = $("tts-speed");
    if (speed) {
      const valueNode = $("tts-speed-value");
      speed.addEventListener("input", () => {
        if (valueNode) valueNode.textContent = parseFloat(speed.value).toFixed(2) + "×";
      });
    }

    document.querySelectorAll("[data-save-secret]").forEach((button) => {
      button.addEventListener("click", () => {
        const name = button.dataset.saveSecret;
        const input = document.querySelector('[data-secret-input="' + name + '"]');
        const value = input ? input.value.trim() : "";
        if (!value) { toast("Paste a key first.", "error"); return; }
        saveSecret(name, value);
      });
    });

    document.querySelectorAll("[data-remove-secret]").forEach((button) => {
      button.addEventListener("click", () => removeSecret(button.dataset.removeSecret));
    });

    const ttsTest = $("tts-test");
    if (ttsTest) ttsTest.addEventListener("click", testTts);

    const save = $("save-settings");
    if (save) save.addEventListener("click", saveSettings);

    const volume = $("default-music-volume");
    if (volume) {
      const label = $("default-music-volume-label");
      volume.addEventListener("input", () => {
        if (label) label.textContent = volume.value + "%";
      });
    }

    const refresh = $("health-refresh");
    if (refresh) refresh.addEventListener("click", loadHealth);

    syncProviderUi();
    syncTtsUi();
    refreshAllSecrets();
    loadMusicPanels();
    loadHealth();
  }

  document.addEventListener("DOMContentLoaded", () => {
    initSectionNav();
    wireSettingsForm();
  });
})();
