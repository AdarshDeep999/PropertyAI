/* Shared front-end helpers + provider settings form.
   Loaded on both pages; app.js uses window.Studio for API calls and toasts. */
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

  /* ------------------------------------------------------------------ secrets */
  function secretLabel(name) {
    const labels = {
      gemini: "Gemini key",
      openai_compat: "OpenAI-compatible key",
      openai_tts: "OpenAI speech key",
      elevenlabs: "ElevenLabs key",
    };
    return labels[name] || name;
  }

  function renderSecret(name) {
    const info = state.secrets[name] || { configured: false };
    document.querySelectorAll('[data-secret="' + name + '"]').forEach((node) => {
      if (node.tagName === "INPUT") return;
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
      node.innerHTML = '<span class="' + dot + '"></span><span>' + text + "</span>";
    });
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

  function syncProviderUi() {
    const select = $("llm-provider");
    if (!select) return;
    const meta = (state.catalog.llm || {})[select.value] || {};
    const baseWrap = $("llm-base-url-wrap");
    if (baseWrap) baseWrap.hidden = select.value !== "openai_compatible";

    const list = $("llm-model-options");
    if (list) {
      list.innerHTML = (meta.models || []).map((model) =>
        '<option value="' + escapeHtml(model) + '"></option>').join("");
    }
    const note = $("llm-provider-note");
    if (note) note.textContent = meta.notes || "";

    // Point the key row at whichever credential this provider needs.
    const keyStatus = $("llm-key-status");
    if (keyStatus && meta.secret) {
      keyStatus.dataset.secret = meta.secret;
      renderSecret(meta.secret);
    }
    const label = document.querySelector('label[for="llm-key"]');
    if (label) label.textContent = (meta.label || "Provider") + " API key";
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
      if (engine.requires_key) text += " Requires an API key saved below.";
      note.textContent = text;
    }
  }

  async function testLlm() {
    const resultNode = $("llm-test-result");
    if (resultNode) resultNode.textContent = "Testing…";
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
      if (resultNode) {
        resultNode.textContent = (result.ok ? "✓ " : "✕ ") + result.message;
        resultNode.style.color = result.ok ? "var(--ok)" : "var(--danger)";
      }
      toast(result.message, result.ok ? "ok" : "error");
    } catch (error) {
      if (resultNode) {
        resultNode.textContent = "✕ " + error.message;
        resultNode.style.color = "var(--danger)";
      }
    }
  }

  async function testTts() {
    const resultNode = $("tts-test-result");
    const engineSelect = $("tts-engine");
    if (!engineSelect) return;
    if (resultNode) resultNode.textContent = "Generating a test clip…";
    try {
      const result = await api("/api/settings/test", {
        method: "POST",
        body: { kind: "tts", engine: engineSelect.value, voice: ($("tts-voice") || {}).value },
      });
      if (resultNode) {
        resultNode.textContent = (result.ok ? "✓ " : "✕ ") + result.message;
        resultNode.style.color = result.ok ? "var(--ok)" : "var(--danger)";
      }
    } catch (error) {
      if (resultNode) {
        resultNode.textContent = "✕ " + error.message;
        resultNode.style.color = "var(--danger)";
      }
    }
  }

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
    if (button) button.disabled = true;
    try {
      const result = await api("/api/settings", { method: "POST", body: { llm, tts } });
      state.settings = result.settings;
      if (resultNode) resultNode.textContent = "Saved.";
      toast("Settings saved.", "ok");
    } catch (error) {
      if (resultNode) resultNode.textContent = "Not saved: " + error.message;
      toast(error.message, "error");
    } finally {
      if (button) button.disabled = false;
    }
  }

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
          if (saved) input.value = "";  // never leave a key sitting in the field
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
      syncTtsUi();
    }

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

    syncProviderUi();
    syncTtsUi();
    refreshAllSecrets();
  }

  document.addEventListener("DOMContentLoaded", () => {
    wireSettingsForm();
  });
})();
