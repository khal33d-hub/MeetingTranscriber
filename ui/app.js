/* Meeting Transcriber — UI logic. Talks to Python through window.pywebview.api;
   Python pushes events back through window.__mt(batch). */
"use strict";

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
let api = null;

const state = {
  settings: {},
  models: {},
  live: false,
  stopping: false,
  fileBusy: false,
  ready: false,
};

const PAGES = {
  live: ["Live meeting", "Transcribes you and everyone you can hear, as they speak."],
  file: ["Transcribe a file", "Meeting recordings, voice notes or videos."],
  history: ["History", "Your saved transcripts."],
  settings: ["Settings", "Changes are saved automatically."],
};
const BEAM_LABEL = { 1: "Fast", 5: "Balanced", 8: "Accurate" };
const EMPTY = {};   // initial empty-state markup per transcript, for Clear

/* ---------------- helpers ---------------- */
function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return [h, m, s].map(n => String(n).padStart(2, "0")).join(":");
}
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}
function modal(title, body, buttons = [{ label: "OK", cls: "primary" }]) {
  $("#modal-title").textContent = title;
  $("#modal-body").textContent = body;
  const acts = $("#modal-actions");
  acts.innerHTML = "";
  buttons.forEach(b => {
    const btn = el("button", b.cls || "", b.label);
    btn.onclick = () => { $("#modal").hidden = true; b.fn && b.fn(); };
    acts.appendChild(btn);
  });
  $("#modal").hidden = false;
  acts.lastChild.focus();
}
function showError(title, msg) { Sounds.play("error"); modal(title, msg); }
function warn(msg) { Sounds.play("warning"); toast(msg); }
function notReady() { Sounds.play("notready"); toast("The speech model is still loading — try again in a moment"); }

/* ---------------- theme ---------------- */
const darkQuery = matchMedia("(prefers-color-scheme: dark)");
function applyTheme() {
  const a = state.settings.appearance || "Dark";
  const dark = a === "Dark" || (a === "System" && darkQuery.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}
darkQuery.addEventListener("change", applyTheme);

/* ---------------- navigation ---------------- */
function show(page) {
  $$(".nav").forEach(b => b.classList.toggle("active", b.dataset.page === page));
  $$(".page").forEach(p => p.classList.toggle("active", p.id === "page-" + page));
  $("#page-title").textContent = PAGES[page][0];
  $("#page-sub").textContent = PAGES[page][1];
  if (page === "history") loadHistory();
}
$$(".nav[data-page]").forEach(b => b.addEventListener("click", () => show(b.dataset.page)));

/* ---------------- status ---------------- */
let lastKind = null;
function setStatus(s) {
  if ((s.kind === "ready" || s.kind === "cpu") && (lastKind === "loading" || lastKind === null)) Sounds.play("ready");
  lastKind = s.kind;
  const box = $("#status");
  box.dataset.kind = s.kind;
  box.title = s.sub || "";
  $("#status-title").textContent = s.title;
  state.ready = s.kind === "ready" || s.kind === "cpu" || s.kind === "recording" || s.kind === "busy";
}

/* ---------------- transcripts ---------------- */
function prepare(out) {
  const e = $(".empty", out);
  if (e) e.remove();
}
function stuck(out) {
  return out.scrollTop + out.clientHeight >= out.scrollHeight - 48;
}
function append(out, node) {
  prepare(out);
  const stick = stuck(out);
  out.appendChild(node);
  if (stick) out.scrollTop = out.scrollHeight;
  else $("#" + out.id.replace("-out", "-jump"))?.classList.add("show");
}
function lineNode(time, text) {
  const row = el("div", "line" + (state.settings.timestamps ? "" : " plain"));
  const t = el("time", "", time);
  row.append(t, el("p", "", text));
  return row;
}
function addLine(out, start, text) { append(out, lineNode(fmt(start), text)); }
function addDivider(out, text) { append(out, el("div", "divider", text)); }
function addNote(out, text) { append(out, el("div", "note", text)); }

function transcriptText(out) {
  const raw = $(".raw", out);
  if (raw) return raw.textContent.trim();
  return $$(".line, .divider", out).map(n => {
    if (n.classList.contains("divider")) return `\n━━━  ${n.textContent}  ━━━`;
    const t = $("time", n).textContent, p = $("p", n).textContent;
    return state.settings.timestamps && t ? `[${t}] ${p}` : p;
  }).join("\n").trim();
}

["live", "file"].forEach(k => {
  const out = $(`#${k}-out`), jump = $(`#${k}-jump`);
  out.addEventListener("scroll", () => { if (stuck(out)) jump.classList.remove("show"); });
  jump.addEventListener("click", () => { out.scrollTop = out.scrollHeight; jump.classList.remove("show"); });
});
$$(".transcript").forEach(o => EMPTY[o.id] = o.innerHTML);

async function copyText(text) {
  if (!text) return toast("Nothing to copy");
  try { await navigator.clipboard.writeText(text); }
  catch { if (!(await api.copy(text))) return toast("Could not copy"); }
  toast("Copied to clipboard");
}

$$(".toolbar").forEach(bar => {
  const out = $("#" + bar.dataset.target);
  bar.addEventListener("click", async ev => {
    const b = ev.target.closest("button");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "copy") copyText(transcriptText(out));
    if (act === "save") {
      const text = transcriptText(out);
      if (!text) return toast("Nothing to save");
      const name = await api.save_as(text, !!bar.dataset.srt);
      if (name) toast("Saved " + name);
    }
    if (act === "clear") { out.innerHTML = EMPTY[out.id]; }
    if (act === "folder") api.open_output_folder();
  });
});

/* ---------------- live ---------------- */
const rec = $("#rec-btn");
function liveUI() {
  rec.classList.toggle("on", state.live);
  rec.disabled = state.stopping || state.fileBusy;
  $("#rec-state").textContent = state.stopping ? "Finishing…" : state.live ? "Recording" : "Ready to record";
  $("#mic-select").disabled = $("#spk-select").disabled = state.live;
  $("#refresh-devices").disabled = state.live;
  $("#choose-file").disabled = state.live;
}
rec.addEventListener("click", toggleLive);
async function toggleLive() {
  if (rec.disabled) return;
  if (state.live) {
    state.stopping = true;
    liveUI();
    await api.stop_live();
    return;
  }
  rec.disabled = true;
  if (!state.ready) { rec.disabled = false; return notReady(); }
  await Sounds.play("start");          // finish the chime before capture starts
  const r = await api.start_live($("#mic-select").value, $("#spk-select").value);
  rec.disabled = false;
  if (!r.ok) return r.code === "not_ready" ? notReady() : showError("Can't start recording", r.error);
  state.live = true;
  $("#timer").textContent = "00:00:00";
  liveUI();
}

function fillSelect(sel, items, value) {
  sel.innerHTML = "";
  items.forEach(n => { const o = el("option", "", n); o.value = n; sel.appendChild(o); });
  if (value != null) sel.value = value;
}
async function refreshDevices(spin) {
  const btn = $("#refresh-devices");
  if (spin) { btn.classList.remove("spin"); void btn.offsetWidth; btn.classList.add("spin"); }
  const r = await api.refresh_devices();
  if (!r.ok) {
    fillSelect($("#mic-select"), [r.error], r.error);
    fillSelect($("#spk-select"), r.speakers);
    return;
  }
  fillSelect($("#mic-select"), r.mics.length ? r.mics : ["No microphone found"], r.mic);
  fillSelect($("#spk-select"), r.speakers, r.speaker);
  if (spin) toast("Devices refreshed");
}
$("#refresh-devices").addEventListener("click", () => refreshDevices(true));
$("#mic-select").addEventListener("change", e => api.set_setting("mic", e.target.value));
$("#spk-select").addEventListener("change", e => api.set_setting("speaker", e.target.value));

/* ---------------- file ---------------- */
const drop = $("#drop");
async function startFile(path) {
  if (!path) return;
  if (state.live) return warn("Stop the live recording before transcribing a file");
  const r = await api.transcribe_file(path);
  if (!r.ok) return r.code === "not_ready" ? notReady() : r.code === "busy" ? warn(r.error) : showError("Can't transcribe file", r.error);
}
$("#choose-file").addEventListener("click", async () => {
  if (state.fileBusy) return;
  startFile(await api.choose_file());
});
drop.addEventListener("click", ev => {
  if (!state.fileBusy && drop.classList.contains("busy") && !ev.target.closest("button")) {
    drop.classList.remove("busy");
  }
});
// Visual feedback only; the full path arrives from Python as a "dropped" event.
let dragDepth = 0;
document.addEventListener("dragenter", e => { e.preventDefault(); if (++dragDepth === 1) { show("file"); drop.classList.add("over"); } });
document.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; drop.classList.remove("over"); } });
document.addEventListener("dragover", e => e.preventDefault());
document.addEventListener("drop", e => { e.preventDefault(); dragDepth = 0; drop.classList.remove("over"); });

function setProgress(v) {
  const pct = Math.round(v * 100);
  $("#file-progress").style.width = pct + "%";
  $("#file-pct").textContent = pct + "%";
}

/* ---------------- history ---------------- */
let historyItems = [];
let historyActive = null;
async function loadHistory() {
  historyItems = await api.list_history();
  renderHistory();
}
function renderHistory() {
  const q = $("#history-search").value.trim().toLowerCase();
  const list = $("#history-list");
  list.innerHTML = "";
  const items = historyItems.filter(i => !q || i.name.toLowerCase().includes(q));
  if (!items.length) {
    list.appendChild(el("div", "muted small", historyItems.length ? "No matches." : "No transcripts yet."));
    return;
  }
  items.forEach(i => {
    const b = el("button", "item" + (i.path === historyActive ? " active" : ""));
    b.append(el("b", "", i.name), el("span", "", i.when));
    b.title = i.name;
    b.onclick = () => openHistory(i);
    list.appendChild(b);
  });
}
async function openHistory(item) {
  historyActive = item.path;
  renderHistory();
  const out = $("#history-out");
  const r = await api.read_history(item.path);
  $("#viewer-head").textContent = item.name;
  out.innerHTML = "";
  if (!r.ok) { out.appendChild(el("div", "note", "Could not open file: " + r.error)); return; }
  const lines = r.text.split(/\r?\n/).filter(l => l.trim());
  const stamped = lines.length && lines.every(l => /^\[\d\d:\d\d:\d\d\]/.test(l));
  if (stamped) {
    lines.forEach(l => {
      const m = l.match(/^\[(\d\d:\d\d:\d\d)\]\s?(.*)$/);
      const row = lineNode(m[1], m[2]);
      row.classList.remove("plain");
      out.appendChild(row);
    });
  } else {
    out.appendChild(el("pre", "raw", r.text));
  }
  out.scrollTop = 0;
}
let searchTimer;
$("#history-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(renderHistory, 120); });

/* ---------------- settings ---------------- */
async function setSetting(key, value) {
  const r = await api.set_setting(key, value);
  if (!r.ok) { warn(r.error); if (r.settings) { state.settings = r.settings; renderSettings(); } return false; }
  state.settings = r.settings;
  return true;
}
function renderSettings() {
  const s = state.settings;
  $("#set-model").value = s.model;
  $("#model-hint").textContent = (state.models[s.model] || "") + ". Downloaded once, then works offline.";
  $("#set-language").value = s.language;
  $("#set-chunk").value = s.chunk_seconds;
  $("#chunk-val").textContent = s.chunk_seconds + "s";
  $("#set-recordings").checked = !!s.save_recordings;
  $("#set-muted").checked = !!s.sound_muted;
  $("#set-volume").value = s.sound_volume;
  $("#volume-val").textContent = s.sound_volume + "%";
  $("#volume-row").classList.toggle("off", !!s.sound_muted);
  $("#mute-btn").classList.toggle("muted-on", !!s.sound_muted);
  $("#mute-btn use").setAttribute("href", s.sound_muted ? "#i-mute" : "#i-speaker");
  $("#mute-btn").title = s.sound_muted ? "Sounds off — click to turn on  (Ctrl+M)" : "Sounds on — click to mute  (Ctrl+M)";
  Sounds.configure({ volume: s.sound_volume, muted: s.sound_muted });
  $("#set-timestamps").checked = !!s.timestamps;
  $("#folder-path").textContent = s.output_dir;
  $("#folder-path").title = s.output_dir;
  const segVal = { device: s.device, beam_size: BEAM_LABEL[s.beam_size] || "Balanced", appearance: s.appearance };
  $$(".seg").forEach(seg => $$("button", seg).forEach(b => b.classList.toggle("on", b.textContent === segVal[seg.dataset.key])));
  applyTheme();
}
$("#set-model").addEventListener("change", async e => { await setSetting("model", e.target.value); renderSettings(); });
$("#set-language").addEventListener("change", e => setSetting("language", e.target.value));
$("#set-chunk").addEventListener("input", e => $("#chunk-val").textContent = e.target.value + "s");
$("#set-chunk").addEventListener("change", e => setSetting("chunk_seconds", +e.target.value));
$("#set-recordings").addEventListener("change", e => setSetting("save_recordings", e.target.checked));
$("#set-timestamps").addEventListener("change", async e => {
  await setSetting("timestamps", e.target.checked);
  $$("#live-out .line, #file-out .line").forEach(l => l.classList.toggle("plain", !e.target.checked));
});
$$(".seg").forEach(seg => seg.addEventListener("click", async e => {
  const b = e.target.closest("button");
  if (!b) return;
  await setSetting(seg.dataset.key, b.textContent);
  renderSettings();
}));
async function setMuted(m) {
  await setSetting("sound_muted", m);
  renderSettings();
  if (!m) Sounds.play("ready");
  toast(m ? "Sounds muted" : "Sounds on");
}
$("#set-muted").addEventListener("change", e => setMuted(e.target.checked));
$("#mute-btn").addEventListener("click", () => setMuted(!state.settings.sound_muted));
$("#set-volume").addEventListener("input", e => {
  $("#volume-val").textContent = e.target.value + "%";
  Sounds.configure({ volume: +e.target.value });
});
$("#set-volume").addEventListener("change", async e => { await setSetting("sound_volume", +e.target.value); Sounds.play("ready"); });
$("#test-sounds").addEventListener("click", async () => {
  if (state.settings.sound_muted) return toast("Sounds are muted");
  for (const n of ["welcome", "ready", "start", "stop", "done", "notready", "warning", "error"]) {
    toast("Playing: " + n);
    await Sounds.play(n);
    await new Promise(r => setTimeout(r, 250));
  }
});
$("#set-folder").addEventListener("click", async () => {
  const p = await api.choose_folder();
  if (p) { state.settings.output_dir = p; renderSettings(); toast("Save folder changed"); }
});

/* ---------------- events from Python ---------------- */
const handlers = {
  status: setStatus,
  error: d => showError(d.title, d.message),
  meters: d => {
    $("#mic-meter").style.width = Math.min(100, d.mic * 100) + "%";
    $("#spk-meter").style.width = Math.min(100, d.spk * 100) + "%";
    $("#timer").textContent = fmt(d.elapsed);
  },
  live_state: d => {
    const out = $("#live-out");
    if (d.running) {
      state.live = true;
      addDivider(out, d.stamp);
    } else {
      state.live = false;
      state.stopping = false;
      Sounds.play("stop");
      $("#mic-meter").style.width = $("#spk-meter").style.width = "0";
      if (d.note) addNote(out, d.note);
    }
    liveUI();
  },
  live: d => addLine($("#live-out"), d.start, d.text),
  file_state: d => {
    const out = $("#file-out");
    state.fileBusy = d.running;
    if (d.running) {
      drop.classList.add("busy");
      Sounds.loop(true);
      $("#file-name").textContent = d.name;
      $("#file-note").textContent = "Transcribing…";
      setProgress(0);
      addDivider(out, d.name);
    } else if (d.failed) {
      Sounds.loop(false);
      $("#file-note").textContent = "Failed. Click here to choose another file.";
    } else {
      setProgress(1);
      $("#file-note").textContent = d.done + " · click here to transcribe another";
      addNote(out, "✓ " + d.done);
      Sounds.loop(false);
      Sounds.play("done");
      toast("Transcript saved");
    }
    liveUI();
  },
  file_seg: d => { addLine($("#file-out"), d.start, d.text); setProgress(d.progress); },
  dropped: d => { show("file"); if (!state.fileBusy) startFile(d.path); else warn("Wait for the current file to finish"); },
  confirm_close: () => (Sounds.play("warning"), modal("Recording in progress",
    "Stop the recording and close? Everything transcribed so far is already saved.",
    [{ label: "Keep recording" }, { label: "Stop & close", cls: "danger", fn: () => api.confirm_close() }])),
};
window.__mt = batch => batch.forEach(ev => {
  try { handlers[ev.type] && handlers[ev.type](ev.data); }
  catch (e) { console.error(ev.type, e); }
});

/* ---------------- keyboard ---------------- */
document.addEventListener("keydown", e => {
  if (e.key === "F5" || (e.ctrlKey && e.key.toLowerCase() === "r")) {
    e.preventDefault();               // never reload the page mid-recording
    if (e.ctrlKey && e.key.toLowerCase() === "r") toggleLive();
    return;
  }
  if (e.key === "Escape" && !$("#modal").hidden) { $("#modal").hidden = true; return; }
  if (e.ctrlKey && e.key.toLowerCase() === "m") { e.preventDefault(); setMuted(!state.settings.sound_muted); return; }
  if (e.ctrlKey && /^[1-4]$/.test(e.key)) { e.preventDefault(); show(["live", "file", "history", "settings"][+e.key - 1]); }
});
document.addEventListener("contextmenu", e => { if (!e.target.closest(".transcript, input")) e.preventDefault(); });

/* ---------------- boot ---------------- */
async function boot() {
  api = window.pywebview.api;
  const init = await api.init();
  state.settings = init.settings;
  state.models = init.models;
  fillSelect($("#set-model"), Object.keys(init.models));
  fillSelect($("#set-language"), init.languages);
  renderSettings();
  Sounds.play("welcome");
  setStatus(init.status);
  state.live = init.live_running;
  liveUI();
  refreshDevices(false);
}
if (window.pywebview && window.pywebview.api && window.pywebview.api.init) boot();
else window.addEventListener("pywebviewready", boot);

/* Dev preview in a normal browser: open index.html?mock */
if (location.search.includes("mock")) {
  const s = { model: "large-v3-turbo", device: "Auto", language: "English", chunk_seconds: 6, beam_size: 5,
    save_recordings: true, timestamps: true, appearance: "Dark", output_dir: "C:\\Transcripts", mic: "", speaker: "", sound_volume: 70, sound_muted: false };
  window.pywebview = { api: {
    init: async () => ({ settings: s, models: { "large-v3-turbo": "Best - fast & accurate (recommended)", small: "Fast - lower accuracy" },
      languages: ["English", "Auto-detect"], status: { title: "Ready · large-v3-turbo", sub: "Running on GPU", kind: "ready" } }),
    refresh_devices: async () => ({ ok: true, mics: ["Microphone (USB Audio)"], speakers: ["None (microphone only)", "Headphones (Stereo) [Loopback]"],
      mic: "Microphone (USB Audio)", speaker: "Headphones (Stereo) [Loopback]" }),
    set_setting: async (k, v) => { s[k] = v; return { ok: true, settings: s }; },
    list_history: async () => [{ name: "Meeting 2026-11-10 09-30", path: "a", when: "10 Nov 2026, 10:12" },
      { name: "Interview feedback", path: "b", when: "9 Nov 2026, 16:40" }],
    read_history: async () => ({ ok: true, text: "[00:00:00] Morning everyone, let's get started.\n[00:00:06] First item is the release plan." }),
    start_live: async () => { setTimeout(() => window.__mt([{ type: "live_state", data: { running: true, stamp: "Meeting 2026-11-10 09-30" } },
      { type: "live", data: { start: 0, text: "Morning everyone, let's get started." } },
      { type: "meters", data: { mic: .45, spk: .7, elapsed: 7 } }]), 50); return { ok: true }; },
    stop_live: async () => { setTimeout(() => window.__mt([{ type: "live_state", data: { running: false, note: "Saved transcript: Meeting 2026-11-10 09-30.txt" } }]), 300); },
    choose_file: async () => null, transcribe_file: async () => ({ ok: true }), save_as: async () => null,
    open_output_folder: async () => {}, copy: async () => true, choose_folder: async () => null, confirm_close: async () => {},
  } };
  boot();
}
