/* Forensic AI front-end. Vanilla JS, no build step. Sections:
   1 helpers  2 theme/tabs/health  3 audio capture + WAV  4 analyze flow  5 results rendering  6 canvases
   7 live monitor  8 dashboard (realtime)  9 about  */
"use strict";

/* ============================================================ 1. helpers */
const CFG = Object.assign({ apiBase: "", supabaseUrl: "", supabaseAnonKey: "" }, window.FORENSIC_CONFIG || {});
const API = (p) => (CFG.apiBase || "").replace(/\/$/, "") + p;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const pct = (v, d = 1) => (v * 100).toFixed(d) + "%";
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const CLIENT_ID = (() => {
  try {
    let id = localStorage.getItem("fai_client");
    if (!id) { id = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)); localStorage.setItem("fai_client", id); }
    return id;
  } catch { return "anon"; }
})();
function toast(msg, ms = 2200) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}
function timeAgo(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 5) return "just now"; if (s < 60) return Math.floor(s) + " s ago"; if (s < 3600) return Math.floor(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + " h ago"; return new Date(iso).toLocaleDateString();
}
function download(name, text, type = "text/plain") {
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name;
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
// mel <-> Hz (HTK scale, 20 Hz - 8 kHz, 128 bins): identical to the model front-end
const hz2mel = (f) => 2595 * Math.log10(1 + f / 700);
const M_LO = hz2mel(20), M_HI = hz2mel(8000);
const binToHz = (i, n = 128) => 700 * (Math.pow(10, (M_LO + (M_HI - M_LO) * (i + 1) / (n + 1)) / 2595) - 1);
const hzToFrac = (f) => clamp((hz2mel(Math.max(f, 20)) - M_LO) / (M_HI - M_LO), 0, 1);

/* ============================================================ 2. theme, tabs, health */
(function theme() {
  const saved = (() => { try { return localStorage.getItem("fai_theme"); } catch { return null; } })();
  const pref = saved || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  document.documentElement.dataset.theme = pref;
  $("#themeBtn").addEventListener("click", () => {
    const n = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = n; try { localStorage.setItem("fai_theme", n); } catch {}
    redrawAll();
  });
})();

function showTab(name) {
  if (!$("#tab-" + name)) name = "analyze";
  $$(".tab").forEach((t) => { const on = t.id === "tab-" + name; t.classList.toggle("active", on); t.hidden = !on; });
  $$(".tab-btn").forEach((b) => { const on = b.dataset.tab === name; b.classList.toggle("active", on); b.setAttribute("aria-selected", on); });
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  if (name === "dash") dash.refresh();
  requestAnimationFrame(redrawAll);
}
$$(".tab-btn").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
addEventListener("hashchange", () => showTab(location.hash.slice(1)));

const app = { health: null, info: null };
async function getJSON(path, opts) {
  const r = await fetch(API(path), opts);
  if (!r.ok) { let d = r.statusText; try { d = (await r.json()).detail || d; } catch {} throw new Error(d); }
  return r.json();
}
async function pollHealth() {
  const pill = $("#apiPill");
  try {
    const h = await getJSON("/api/health"); app.health = h;
    pill.className = "pill ok";
    pill.querySelector("span").textContent = `API online · ${h.device} · ${h.database.backend}`;
    $("#maxSec").textContent = h.limits.max_seconds; $("#maxMb").textContent = h.limits.max_upload_mb;
    $("#footInfo").textContent = `v${h.version} · model ${h.params_m} M params on ${h.device}`;
    $("#aboutVersion").textContent = `API v${h.version} · running on ${h.device} · database: ${h.database.backend}` +
      (h.database.supabase_error ? ` (Supabase error: ${h.database.supabase_error})` : "");
  } catch (e) {
    pill.className = "pill bad"; pill.querySelector("span").textContent = "API offline";
  }
}

/* ============================================================ 3. audio capture + WAV */
function encodeWav(float32, sr) {
  const n = float32.length, buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); w(8, "WAVE"); w(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
  v.setUint16(22, 1, true); v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, "data"); v.setUint32(40, n * 2, true);
  for (let i = 0, o = 44; i < n; i++, o += 2) { const s = clamp(float32[i], -1, 1); v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true); }
  return new Blob([buf], { type: "audio/wav" });
}
async function resampleTo16k(float32, sr) {
  if (sr === 16000) return float32;
  const len = Math.max(1, Math.ceil(float32.length * 16000 / sr));
  const off = new OfflineAudioContext(1, len, 16000);
  const b = off.createBuffer(1, float32.length, sr); b.copyToChannel(float32, 0);
  const src = off.createBufferSource(); src.buffer = b; src.connect(off.destination); src.start();
  return (await off.startRendering()).getChannelData(0);
}
async function fileToWav16k(blob, maxSec) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  try {
    const audio = await ctx.decodeAudioData(await blob.arrayBuffer());
    const len = Math.ceil(Math.min(audio.duration, maxSec) * 16000);
    const off = new OfflineAudioContext(1, len, 16000);   // mono mix-down + resample in one step
    const src = off.createBufferSource(); src.buffer = audio; src.connect(off.destination); src.start();
    return { wav: encodeWav((await off.startRendering()).getChannelData(0), 16000), origSr: audio.sampleRate };
  } finally { ctx.close(); }
}
const WORKLET = `class Cap extends AudioWorkletProcessor{constructor(){super();this.b=new Float32Array(2048);this.n=0;}
process(i){const c=i[0][0];if(!c)return true;for(let k=0;k<c.length;k++){this.b[this.n++]=c[k];if(this.n===2048){this.port.postMessage(this.b.slice(0));this.n=0;}}return true;}}
registerProcessor('cap',Cap);`;
class Mic {   // raw PCM capture with all browser "voice enhancement" switched off (it would alter the signal)
  async start(onChunk) {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 } });
    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (this.ctx.state === "suspended") await this.ctx.resume();
    this.sr = this.ctx.sampleRate;
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.sink = this.ctx.createGain(); this.sink.gain.value = 0; this.sink.connect(this.ctx.destination);
    try {
      await this.ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" })));
      this.node = new AudioWorkletNode(this.ctx, "cap"); this.node.port.onmessage = (e) => onChunk(e.data);
    } catch {   // very old browsers
      this.node = this.ctx.createScriptProcessor(4096, 1, 1); this.node.onaudioprocess = (e) => onChunk(e.inputBuffer.getChannelData(0).slice(0));
    }
    src.connect(this.node); this.node.connect(this.sink);
  }
  stop() { try { this.stream.getTracks().forEach((t) => t.stop()); this.node.disconnect(); this.ctx.close(); } catch {} }
}
const rms = (a) => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * a[i]; return Math.sqrt(s / a.length); };

/* ============================================================ 4. analyze flow */
const cur = { res: null, blob: null, name: "", source: "upload", url: null, spec: null, cam: null, audio: null, busy: false };

function setBusy(on, text = "Analysing…") {
  cur.busy = on; $("#status").classList.toggle("hidden", !on); $("#statusText").textContent = text;
  $$("#browseBtn,#recBtn,.sample").forEach((b) => (b.disabled = on));
}
function showError(msg) { const b = $("#errorBox"); b.textContent = msg; b.classList.remove("hidden"); }
function clearError() { $("#errorBox").classList.add("hidden"); }

async function postAudio(file, fname, source, windowIdx, log) {
  const fd = new FormData(); fd.append("file", file, fname);
  const q = new URLSearchParams({ source, client_id: CLIENT_ID, cam: 1, log });
  if (windowIdx !== null) q.set("window", windowIdx);
  const ctrl = new AbortController(), to = setTimeout(() => ctrl.abort(), 90000);
  try { return await fetch(API("/api/analyze?" + q), { method: "POST", body: fd, signal: ctrl.signal }); }
  catch (e) { throw new Error(e.name === "AbortError" ? "The server took too long to respond." : `Cannot reach the API${CFG.apiBase ? " at " + CFG.apiBase : ""}. Is the server running?`); }
  finally { clearTimeout(to); }
}
async function analyze(blob, name, source, { windowIdx = null, log = 1, rerun = false } = {}) {
  if (cur.busy) return;
  clearError(); setBusy(true, rerun ? "Explaining the selected window…" : "Analysing…");
  const t0 = performance.now();
  try {
    let upload = blob, upName = name, r = await postAudio(upload, upName, source, windowIdx, log);
    if (r.status === 400 && !rerun) {   // server could not decode the original (e.g. M4A/AAC): convert in the browser and retry once
      let msg = ""; try { msg = (await r.clone().json()).detail || ""; } catch {}
      if (/decode/i.test(msg)) {
        setBusy(true, "Converting audio in the browser…");
        try {
          const c = await fileToWav16k(blob, app.health ? app.health.limits.max_seconds : 60);
          upload = c.wav; upName = name.replace(/\.[^.]+$/, "") + ".wav"; setBusy(true, "Analysing…");
          r = await postAudio(upload, upName, source, windowIdx, log);
        } catch { /* keep the original error */ }
      }
    }
    if (!r.ok) { let d = r.statusText; try { d = (await r.json()).detail || d; } catch {} throw new Error(d); }
    const res = await r.json();
    res.roundtrip_ms = Math.round(performance.now() - t0);
    if (!rerun) {
      if (cur.url) URL.revokeObjectURL(cur.url);
      cur.blob = upload; cur.name = name; cur.source = source; cur.url = URL.createObjectURL(upload); stopPlayback();
    }
    cur.res = res;
    renderResults(res);
    if (!rerun) $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { showError(e.message || String(e)); }
  finally { setBusy(false); }
}

/* ---- inputs: drop zone, browse, samples, recorder */
const drop = $("#drop");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) analyze(f, f.name, "upload"); });
drop.addEventListener("click", (e) => { if (!e.target.closest("button,.rec-panel,input")) $("#fileInput").click(); });
drop.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === drop) { e.preventDefault(); $("#fileInput").click(); } });
$("#browseBtn").addEventListener("click", () => $("#fileInput").click());
$("#fileInput").addEventListener("change", (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) analyze(f, f.name, "upload"); });

async function loadSamples() {
  const box = $("#sampleList");
  try {
    const list = await getJSON("/api/samples"); box.innerHTML = "";
    list.forEach((s) => {
      const b = document.createElement("button"); b.type = "button"; b.className = "sample " + s.label;
      b.innerHTML = `<span class="ic">${s.label === "real" ? "REAL" : "FAKE"}</span><span><div class="t">${esc(s.title)}</div><div class="s">${esc(s.name.split("__")[1] || s.name)}</div></span>` +
        (s.known_miss ? `<span class="tag miss">known miss</span>` : `<span class="tag">${s.label === "real" ? "genuine" : "attack " + esc(s.attack)}</span>`);
      b.addEventListener("click", async () => {
        try { const blob = await (await fetch(API(s.url))).blob(); analyze(blob, s.name, "sample"); } catch { showError("Could not load the sample clip."); }
      });
      box.appendChild(b);
    });
  } catch { box.innerHTML = `<p class="muted small">Samples unavailable (API offline).</p>`; }
}

const rec = { mic: null, chunks: [], t0: 0, timer: null };
async function recStart() {
  if (!navigator.mediaDevices || !window.isSecureContext) { toast("Microphone needs https or localhost."); return; }
  try {
    rec.chunks = []; rec.mic = new Mic();
    await rec.mic.start((c) => {
      rec.chunks.push(c); $("#recMeter").style.width = clamp(rms(c) * 6, 0, 1) * 100 + "%";
    });
  } catch (e) { toast("Microphone unavailable: " + (e.name === "NotAllowedError" ? "permission denied" : e.message)); rec.mic = null; return; }
  rec.t0 = performance.now(); drop.classList.add("recording"); $("#recPanel").hidden = false; $("#recBtn").disabled = true;
  rec.timer = setInterval(() => {
    const s = (performance.now() - rec.t0) / 1000; $("#recTime").textContent = Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0");
    if (s >= (app.health ? app.health.limits.max_seconds : 60)) recStop(true);
  }, 200);
}
function recEnd() { clearInterval(rec.timer); drop.classList.remove("recording"); $("#recPanel").hidden = true; $("#recBtn").disabled = false; $("#recMeter").style.width = "0"; if (rec.mic) rec.mic.stop(); }
async function recStop(analyzeIt) {
  const sr = rec.mic ? rec.mic.sr : 48000, chunks = rec.chunks; recEnd(); rec.mic = null;
  if (!analyzeIt) return;
  const n = chunks.reduce((a, c) => a + c.length, 0);
  if (n < sr * 0.5) { toast("Recording too short."); return; }
  const all = new Float32Array(n); let o = 0; for (const c of chunks) { all.set(c, o); o += c.length; }
  const blob = encodeWav(await resampleTo16k(all, sr), 16000);
  analyze(blob, "microphone-recording.wav", "record");
}
$("#recBtn").addEventListener("click", recStart);
$("#recStop").addEventListener("click", () => recStop(true));
$("#recCancel").addEventListener("click", () => recStop(false));

/* ============================================================ 5. results rendering */
function renderResults(res) {
  const box = $("#results"); box.classList.remove("hidden");
  const fake = res.verdict === "FAKE", vc = $("#verdictCard");
  vc.classList.toggle("fake", fake); vc.classList.toggle("real", !fake);
  $("#verdictWord").textContent = res.verdict; $("#probText").textContent = pct(res.prob_fake);
  const ch = $("#confChip"); ch.textContent = res.confidence + " confidence";
  // gauge (animated from zero on every new result)
  const gf = $("#gFill"), gn = $("#gNeedle"); gf.style.transition = gn.style.transition = "none"; gf.style.strokeDashoffset = 100; gn.style.transform = "rotate(-90deg)";
  void gf.getBoundingClientRect(); gf.style.transition = gn.style.transition = "";
  requestAnimationFrame(() => { gf.style.strokeDashoffset = 100 - res.prob_fake * 100; gn.style.transform = `rotate(${-90 + res.prob_fake * 180}deg)`; });
  const facts = [["Duration", res.duration_s.toFixed(1) + " s"], ["Windows flagged", `${res.n_flagged} / ${res.n_windows}`],
    ["Peak window", pct(res.max_prob)], ["Source rate", (res.orig_sr / 1000).toFixed(res.orig_sr % 1000 ? 1 : 0) + " kHz"]];
  $("#facts").innerHTML = facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("");
  $("#warnList").innerHTML = res.warnings.map((w) => `<li>⚠ ${esc(w)}</li>`).join("");

  // evidence data
  cur.spec = decodeLayer(res.spec); cur.cam = decodeLayer(res.cam); cur.specImg = layerCanvas(cur.spec, window.LUT_MAGMA, false); cur.camImg = layerCanvas(cur.cam, window.LUT_TURBO, true);
  const ew = res.evidence_window;
  $("#evWindowNote").textContent = `Spectrogram shows window ${ew.index + 1} of ${res.n_windows} (${ew.start_s.toFixed(1)}–${ew.end_s.toFixed(1)} s)` + (res.n_windows > 1 ? " – the most suspicious one unless you picked another." : ".");

  // bands
  const b = res.bands, rows = [["Low  0–2.7 kHz", b.low], ["Mid  2.7–5.5 kHz", b.mid], ["High  5.5–8 kHz", b.high]];
  const none = b.low + b.mid + b.high < 0.01;
  $("#bandBars").innerHTML = rows.map(([k, v]) => `<div class="band-row"><span>${k}</span><div class="bar"><i data-w="${(v * 100).toFixed(1)}"></i></div><b class="mono">${(v * 100).toFixed(0)}%</b></div>`).join("");
  requestAnimationFrame(() => $$("#bandBars i").forEach((i) => (i.style.width = i.dataset.w + "%")));
  $("#bandNote").textContent = none ? "Almost no evidence for FAKE was found in this window." : `Share of the Grad-CAM evidence per band. Peak activation ${pct(res.cam.peak, 0)} of a typical detected fake.`;

  // timeline note
  $("#tlNote").textContent = res.n_windows === 1 ? "Short clip: one 4-second window (repeat-padded if shorter)." :
    `${res.n_windows} overlapping 4 s windows (hop 2 s). Verdict uses the mean score; max ${pct(res.max_prob)}.`;

  // report
  const rp = res.report;
  $("#reportBody").innerHTML = `<div class="head"><span>${esc(res.filename)}</span><span>${res.duration_s.toFixed(1)} s</span><span>${res.n_windows} window${res.n_windows > 1 ? "s" : ""}</span></div>` +
    `<h4>Verdict</h4><p><b>${res.verdict}</b> — P[fake] = ${pct(res.prob_fake)}, decision confidence ${esc(res.confidence)}.</p>` +
    rp.sections.map((s) => `<h4>${esc(s.title)}</h4><p>${esc(s.body)}</p>`).join("");

  // pipeline timings
  const tm = res.timings_ms;
  $("#steps").innerHTML = [[`Score ${res.n_windows} window${res.n_windows > 1 ? "s" : ""} (CNN + Transformer)`, tm.inference], ["Grad-CAM explanation", tm.gradcam],
    ["Server total", tm.total], ["Round trip incl. upload", res.roundtrip_ms]].map(([k, v]) => `<li><span>${k}</span><b>${Math.round(v)} ms</b></li>`).join("");

  redrawAll();
}
function decodeLayer(l) {
  const bin = atob(l.data), a = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
  return { w: l.w, h: l.h, a, lo: l.lo, hi: l.hi };
}
function layerCanvas(L, lut, alpha) {
  const c = document.createElement("canvas"); c.width = L.w; c.height = L.h; const x = c.getContext("2d"), img = x.createImageData(L.w, L.h);
  for (let r = 0; r < L.h; r++) for (let f = 0; f < L.w; f++) {
    const v = L.a[r * L.w + f], o = ((L.h - 1 - r) * L.w + f) * 4;   // row 0 = lowest mel bin -> bottom
    img.data[o] = lut[v * 3]; img.data[o + 1] = lut[v * 3 + 1]; img.data[o + 2] = lut[v * 3 + 2];
    img.data[o + 3] = alpha ? clamp(Math.pow(v / 255, 0.8) * 1.35, 0, 1) * 255 : 255;
  }
  x.putImageData(img, 0, 0); return c;
}
$("#copyRep").addEventListener("click", async () => { if (!cur.res) return; try { await navigator.clipboard.writeText(cur.res.report.text); toast("Report copied"); } catch { toast("Copy failed"); } });
$("#dlTxt").addEventListener("click", () => cur.res && download(`forensic-report-${cur.res.filename.replace(/\W+/g, "_")}.txt`, cur.res.report.text));
$("#dlJson").addEventListener("click", () => {
  if (!cur.res) return; const { spec, cam, waveform, ...slim } = cur.res; download(`analysis-${cur.res.filename.replace(/\W+/g, "_")}.json`, JSON.stringify(slim, null, 2), "application/json");
});

/* ---- playback */
function stopPlayback() { if (cur.audio) { cur.audio.pause(); cur.audio = null; } setPlayLabel(false); cancelAnimationFrame(cur.raf); }
function setPlayLabel(on) { $("#playBtn span").textContent = on ? "Pause" : "Play"; }
$("#playBtn").addEventListener("click", () => {
  if (!cur.url) return;
  if (!cur.audio) { cur.audio = new Audio(cur.url); cur.audio.addEventListener("ended", () => { setPlayLabel(false); drawPlayheads(); }); }
  if (cur.audio.paused) { cur.audio.play(); setPlayLabel(true); tick(); } else { cur.audio.pause(); setPlayLabel(false); }
});
function tick() { drawPlayheads(); if (cur.audio && !cur.audio.paused) cur.raf = requestAnimationFrame(tick); }
$("#camToggle").addEventListener("change", () => { drawSpec(); });
$("#camOpacity").addEventListener("input", () => { drawSpec(); });

/* ============================================================ 6. canvases */
function fit(canvas) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2), r = canvas.parentElement.getBoundingClientRect();
  const w = Math.max(10, Math.round(r.width)), h = Math.max(10, Math.round(r.height));
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  const x = canvas.getContext("2d"); x.setTransform(dpr, 0, 0, dpr, 0, 0); return { x, w, h };
}
const PAD = { l: 50, r: 12, t: 10, b: 26 };
const plotBox = (w, h) => ({ x: PAD.l, y: PAD.t, w: w - PAD.l - PAD.r, h: h - PAD.t - PAD.b });
const axisText = () => ({ color: cssVar("--canvas-text"), grid: cssVar("--canvas-grid") });

function drawWave() {
  const c = $("#waveCanvas"); if (!cur.res || !c.offsetParent) return;
  const { x, w, h } = fit(c), P = { x: PAD.l, y: 8, w: w - PAD.l - PAD.r, h: h - 8 - 22 }, ax = axisText(), res = cur.res, env = res.waveform;
  x.clearRect(0, 0, w, h);
  const ew = res.evidence_window, dur = res.duration_s;
  x.fillStyle = "rgba(255,77,94,.10)"; x.fillRect(P.x + ew.start_s / dur * P.w, P.y, Math.min(4, dur) / dur * P.w, P.h);   // evidence window
  x.strokeStyle = ax.grid; x.lineWidth = 1; x.beginPath(); x.moveTo(P.x, P.y + P.h / 2); x.lineTo(P.x + P.w, P.y + P.h / 2); x.stroke();
  const n = env.min.length; x.fillStyle = cssVar("--primary");
  for (let px = 0; px < P.w; px++) {
    const i = Math.min(n - 1, Math.floor(px / P.w * n)), lo = env.min[i], hi = env.max[i];
    const y1 = P.y + P.h / 2 - hi * P.h / 2, y2 = P.y + P.h / 2 - lo * P.h / 2; x.fillRect(P.x + px, y1, 1, Math.max(1, y2 - y1));
  }
  x.fillStyle = ax.color; x.font = "11px system-ui"; x.textAlign = "center";
  const step = dur > 30 ? 10 : dur > 12 ? 5 : dur > 6 ? 2 : 1;
  for (let t = 0; t <= dur + 1e-6; t += step) { const px = P.x + t / dur * P.w; x.fillText(t.toFixed(0) + "s", px, h - 6); x.fillRect(px, P.y + P.h, 1, 3); }
  x.textAlign = "right"; x.fillText("+1", P.x - 6, P.y + 9); x.fillText("-1", P.x - 6, P.y + P.h);
  cur.waveBase = document.createElement("canvas"); cur.waveBase.width = c.width; cur.waveBase.height = c.height; cur.waveBase.getContext("2d").drawImage(c, 0, 0);
  cur.waveGeom = { P, w, h };
}
function drawSpec() {
  const c = $("#specCanvas"); if (!cur.res || !c.offsetParent) return;
  const { x, w, h } = fit(c), P = plotBox(w, h), ax = axisText(), res = cur.res, ew = res.evidence_window;
  x.clearRect(0, 0, w, h); x.imageSmoothingEnabled = true; x.imageSmoothingQuality = "high";
  x.drawImage(cur.specImg, P.x, P.y, P.w, P.h);
  if ($("#camToggle").checked) { x.globalAlpha = $("#camOpacity").value / 100; x.drawImage(cur.camImg, P.x, P.y, P.w, P.h); x.globalAlpha = 1; }
  x.strokeStyle = ax.grid; x.lineWidth = 1; x.fillStyle = ax.color; x.font = "11px system-ui"; x.textAlign = "right";
  [0.1, 0.5, 1, 2, 4, 8].forEach((f) => { const y = P.y + P.h * (1 - hzToFrac(f * 1000)); x.fillText((f < 1 ? f : f.toFixed(0)) + " kHz", P.x - 6, y + 4); x.beginPath(); x.moveTo(P.x - 3, y); x.lineTo(P.x, y); x.stroke(); });
  x.textAlign = "center";
  for (let t = 0; t <= 4.001; t += 0.5) { const px = P.x + t / 4 * P.w; x.fillText((ew.start_s + t).toFixed(1) + "s", px, h - 8); x.beginPath(); x.moveTo(px, P.y + P.h); x.lineTo(px, P.y + P.h + 3); x.stroke(); }
  x.strokeStyle = cssVar("--border2"); x.strokeRect(P.x + .5, P.y + .5, P.w - 1, P.h - 1);
  cur.specBase = document.createElement("canvas"); cur.specBase.width = c.width; cur.specBase.height = c.height; cur.specBase.getContext("2d").drawImage(c, 0, 0);
  cur.specGeom = { P, w, h };
  drawPlayheads();
}
function drawPlayheads() {
  if (!cur.res) return;
  const t = cur.audio ? cur.audio.currentTime : 0, show = cur.audio && (!cur.audio.paused || t > 0);
  const wc = $("#waveCanvas"), sc = $("#specCanvas");
  [[wc, cur.waveBase, cur.waveGeom, (g) => g.P.x + t / cur.res.duration_s * g.P.w], [sc, cur.specBase, cur.specGeom,
    (g) => { const ew = cur.res.evidence_window, u = (t - ew.start_s) / 4; return u < 0 || u > 1 ? null : g.P.x + u * g.P.w; }]].forEach(([c, base, g, fx]) => {
    if (!base || !g) return; const x = c.getContext("2d"), dpr = c.width / g.w;
    x.setTransform(1, 0, 0, 1, 0, 0); x.clearRect(0, 0, c.width, c.height); x.drawImage(base, 0, 0); x.setTransform(dpr, 0, 0, dpr, 0, 0);
    const px = show ? fx(g) : null;
    if (px !== null) { x.strokeStyle = "#fff"; x.lineWidth = 1.5; x.beginPath(); x.moveTo(px, g.P.y); x.lineTo(px, g.P.y + g.P.h); x.stroke(); }
  });
}
// spectrogram hover tooltip
(function () {
  const box = $(".canvas-box.spec"), tip = $("#specTip");
  box.addEventListener("mousemove", (e) => {
    const g = cur.specGeom; if (!g || !cur.spec) return; const r = box.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    const u = (mx - g.P.x) / g.P.w, v = (my - g.P.y) / g.P.h; if (u < 0 || u > 1 || v < 0 || v > 1) { tip.hidden = true; return; }
    const f = Math.min(cur.spec.w - 1, Math.floor(u * cur.spec.w)), bin = Math.min(cur.spec.h - 1, Math.floor((1 - v) * cur.spec.h));
    const ev = cur.cam.a[bin * cur.cam.w + f] / 255, z = cur.spec.lo + cur.spec.a[bin * cur.spec.w + f] / 255 * (cur.spec.hi - cur.spec.lo);
    tip.innerHTML = `t ${(cur.res.evidence_window.start_s + u * 4).toFixed(2)} s &middot; ${(binToHz(bin) / 1000).toFixed(2)} kHz<br>level ${z.toFixed(1)} σ &middot; fake evidence ${(ev * 100).toFixed(0)}%`;
    tip.hidden = false; tip.style.left = Math.min(mx + 14, r.width - 220) + "px"; tip.style.top = Math.max(4, my - 48) + "px";
  });
  box.addEventListener("mouseleave", () => (tip.hidden = true));
})();

function probColor(p) { const a = [47, 211, 155], m = [255, 184, 77], b = [255, 77, 94]; const t = p < .5 ? p / .5 : (p - .5) / .5, s = p < .5 ? [a, m] : [m, b];
  return `rgb(${s[0].map((v, i) => Math.round(v + (s[1][i] - v) * t)).join(",")})`; }
function drawTimeline() {
  const c = $("#tlCanvas"); if (!cur.res || !c.offsetParent) return;
  const { x, w, h } = fit(c), P = { x: 44, y: 14, w: w - 44 - 12, h: h - 14 - 30 }, ax = axisText(), res = cur.res, n = res.n_windows;
  x.clearRect(0, 0, w, h); x.font = "11px system-ui"; x.fillStyle = ax.color; x.strokeStyle = ax.grid; x.textAlign = "right";
  [0, .25, .5, .75, 1].forEach((v) => { const y = P.y + P.h * (1 - v); x.beginPath(); x.moveTo(P.x, y); x.lineTo(P.x + P.w, y); x.stroke(); x.fillText(Math.round(v * 100) + "%", P.x - 6, y + 4); });
  const bw = n === 1 ? Math.min(P.w, 150) : P.w / n, off = n === 1 ? (P.w - bw) / 2 : 0, pad = Math.min(8, bw * .18); cur.tlBars = [];
  res.window_probs.forEach((p, i) => {
    const bh = Math.max(2, p * P.h), bx = P.x + off + i * bw + pad, by = P.y + P.h - bh, ww = bw - pad * 2;
    x.fillStyle = probColor(p); x.globalAlpha = i === res.worst_window ? 1 : .78; x.beginPath(); x.roundRect(bx, by, ww, bh, 5); x.fill(); x.globalAlpha = 1;
    if (i === res.evidence_window.index) { x.strokeStyle = cssVar("--text"); x.lineWidth = 2; x.beginPath(); x.roundRect(bx - 2, by - 2, ww + 4, bh + 4, 6); x.stroke(); x.lineWidth = 1; }
    x.fillStyle = ax.color; x.textAlign = "center"; if (n <= 16 || i % 2 === 0) x.fillText(res.window_starts_s[i].toFixed(0) + "s", bx + ww / 2, h - 12);
    if (n <= 12) { x.fillStyle = cssVar("--text"); x.fillText(Math.round(p * 100) + "%", bx + ww / 2, Math.max(by - 4, 11)); }
    cur.tlBars.push({ x0: bx - pad, x1: bx + ww + pad, i });
  });
  x.strokeStyle = cssVar("--warn"); x.setLineDash([6, 5]); x.lineWidth = 1.4; const ty = P.y + P.h * .5; x.beginPath(); x.moveTo(P.x, ty); x.lineTo(P.x + P.w, ty); x.stroke(); x.setLineDash([]);
  x.fillStyle = cssVar("--warn"); x.textAlign = "left"; x.fillText("threshold 50%", P.x + 6, ty - 5);
}
$("#tlCanvas").addEventListener("click", (e) => {
  if (!cur.res || !cur.tlBars || cur.busy) return; const r = e.currentTarget.getBoundingClientRect(), mx = e.clientX - r.left;
  const hit = cur.tlBars.find((b) => mx >= b.x0 && mx <= b.x1); if (!hit || hit.i === cur.res.evidence_window.index) return;
  analyze(cur.blob, cur.name, cur.source, { windowIdx: hit.i, log: 0, rerun: true });
});
function redrawAll() { drawWave(); drawSpec(); drawTimeline(); live.draw(); dash.drawSpark(); }
new ResizeObserver(() => redrawAll()).observe(document.body);

/* ============================================================ 7. live monitor */
const live = {
  on: false, mic: null, ring: null, w: 0, filled: 0, timer: null, inflight: false, hist: [], n: 0,
  async start() {
    try {
      if (!navigator.mediaDevices || !window.isSecureContext) throw new Error("Microphone needs https or localhost.");
      this.mic = new Mic(); this.ring = null; this.w = 0; this.filled = 0; this.hist = []; this.n = 0; $("#liveLog").innerHTML = "";
      await this.mic.start((c) => {
        if (!this.ring) this.ring = new Float32Array(Math.ceil(this.mic.sr * 4.2));
        for (let i = 0; i < c.length; i++) { this.ring[this.w] = c[i]; this.w = (this.w + 1) % this.ring.length; }
        this.filled = Math.min(this.ring.length, this.filled + c.length); $("#liveMeter").style.width = clamp(rms(c) * 6, 0, 1) * 100 + "%";
      });
    } catch (e) { toast("Microphone unavailable: " + (e.name === "NotAllowedError" ? "permission denied" : e.message)); this.mic = null; return; }
    this.on = true; const b = $("#liveBtn"); b.classList.add("live-on"); b.querySelector("span").textContent = "Stop listening";
    $("#liveMeta").textContent = "Listening… first verdict after ~2 s of audio."; this.timer = setInterval(() => this.tick(), 1000);
  },
  stop() {
    this.on = false; clearInterval(this.timer); if (this.mic) this.mic.stop(); this.mic = null; const b = $("#liveBtn"); b.classList.remove("live-on"); b.querySelector("span").textContent = "Start listening";
    $("#liveMeter").style.width = "0"; $("#liveMeta").textContent = "Stopped."; const v = $("#liveVerdict"); v.textContent = "IDLE"; v.className = "live-verdict";
  },
  async tick() {
    if (!this.on || this.inflight || !this.ring) return; const sr = this.mic.sr; if (this.filled < sr * 2) return;
    const need = Math.min(this.filled, Math.round(sr * 4)), out = new Float32Array(need);
    for (let i = 0; i < need; i++) out[i] = this.ring[(this.w - need + i + this.ring.length * 2) % this.ring.length];
    this.inflight = true; const t0 = performance.now();
    try {
      const wav = encodeWav(await resampleTo16k(out, sr), 16000), fd = new FormData(); fd.append("file", wav, "live.wav");
      const r = await fetch(API("/api/analyze?cam=0&log=0&live=1&source=record"), { method: "POST", body: fd });
      if (!r.ok) { let d = r.statusText; try { d = (await r.json()).detail || d; } catch {} throw new Error(d); }
      const res = await r.json(); this.push(res, performance.now() - t0);
    } catch (e) { $("#liveMeta").textContent = "Error: " + e.message; }
    finally { this.inflight = false; }
  },
  push(res, rt) {
    this.n++; this.hist.push(res.prob_fake); if (this.hist.length > 60) this.hist.shift();
    const k = parseInt($("#liveSmooth").value, 10), sm = this.hist.slice(-k).reduce((a, b) => a + b, 0) / Math.min(k, this.hist.length), fake = sm > .5;
    const v = $("#liveVerdict"); v.textContent = fake ? "FAKE" : "REAL"; v.className = "live-verdict " + (fake ? "fake" : "real");
    $("#liveProb").textContent = pct(sm); $("#liveMeta").textContent = `raw ${pct(res.prob_fake)} · server ${Math.round(res.latency_ms)} ms · round trip ${Math.round(rt)} ms`;
    $("#liveCount").textContent = this.n + " updates";
    const li = document.createElement("li"); li.innerHTML = `<span class="mono">${new Date().toLocaleTimeString()}</span><span class="badge ${res.verdict === "FAKE" ? "fake" : "real"}">${res.verdict}</span><span class="mono">${pct(res.prob_fake, 0)}</span>`;
    const lg = $("#liveLog"); lg.prepend(li); while (lg.children.length > 80) lg.lastChild.remove(); this.draw();
  },
  draw() {
    const c = $("#liveCanvas"); if (!c.offsetParent) return; const { x, w, h } = fit(c), P = { x: 44, y: 12, w: w - 44 - 12, h: h - 12 - 26 }, ax = axisText();
    x.clearRect(0, 0, w, h); x.font = "11px system-ui"; x.fillStyle = ax.color; x.strokeStyle = ax.grid; x.textAlign = "right";
    [0, .5, 1].forEach((v) => { const y = P.y + P.h * (1 - v); x.beginPath(); x.moveTo(P.x, y); x.lineTo(P.x + P.w, y); x.stroke(); x.fillText(Math.round(v * 100) + "%", P.x - 6, y + 4); });
    x.textAlign = "center"; [0, 15, 30, 45, 60].forEach((s) => x.fillText(s === 60 ? "now" : "-" + (60 - s) + "s", P.x + s / 60 * P.w, h - 8));
    const hs = this.hist; if (!hs.length) { x.fillStyle = ax.color; x.fillText("waiting for audio…", P.x + P.w / 2, P.y + P.h / 2); return; }
    const pts = hs.map((p, i) => [P.x + P.w - (hs.length - 1 - i) / 60 * P.w, P.y + P.h * (1 - p), p]);
    x.beginPath(); x.moveTo(pts[0][0], P.y + P.h); pts.forEach(([px, py]) => x.lineTo(px, py)); x.lineTo(pts[pts.length - 1][0], P.y + P.h); x.closePath();
    const g = x.createLinearGradient(0, P.y, 0, P.y + P.h); g.addColorStop(0, "rgba(255,77,94,.38)"); g.addColorStop(.5, "rgba(255,184,77,.15)"); g.addColorStop(1, "rgba(47,211,155,.12)"); x.fillStyle = g; x.fill();
    x.lineWidth = 2.2; x.lineJoin = "round"; for (let i = 1; i < pts.length; i++) { x.strokeStyle = probColor(pts[i][2]); x.beginPath(); x.moveTo(pts[i - 1][0], pts[i - 1][1]); x.lineTo(pts[i][0], pts[i][1]); x.stroke(); }
    x.strokeStyle = cssVar("--warn"); x.setLineDash([6, 5]); x.lineWidth = 1.2; x.beginPath(); x.moveTo(P.x, P.y + P.h * .5); x.lineTo(P.x + P.w, P.y + P.h * .5); x.stroke(); x.setLineDash([]);
  },
};
$("#liveBtn").addEventListener("click", () => (live.on ? live.stop() : live.start()));
addEventListener("pagehide", () => { if (live.on) live.stop(); });

/* ============================================================ 8. dashboard (realtime) */
const dash = {
  rows: [], seen: new Set(), events: 0, mode: "none", sse: null, sb: null, refreshT: null, stats: null,
  setConn(state, text) { const c = $("#conn"); c.className = "conn " + state; c.querySelector("span").textContent = text; $("#navLive").hidden = state !== "ok"; },
  async refresh() {
    try {
      const mine = $("#onlyMine").checked, [h, s] = await Promise.all([getJSON(`/api/history?limit=40${mine ? "&client_id=" + CLIENT_ID : ""}`), getJSON("/api/stats")]);
      this.rows = h.items; this.seen = new Set(this.rows.map((r) => r.id)); this.stats = s; this.renderTable(); this.renderStats();
    } catch { /* offline: pill already says so */ }
  },
  renderStats() {
    const s = this.stats; if (!s) return;
    $("#stTotal").textContent = s.total.toLocaleString(); $("#stFake").textContent = s.fake.toLocaleString(); $("#stFakePct").textContent = s.total ? pct(s.fake_share, 0) + " of all" : "";
    $("#stLat").textContent = s.total ? Math.round(s.avg_latency_ms) + " ms" : "-"; $("#stDay").textContent = s.last_24h.toLocaleString();
    $("#dnPct").textContent = s.total ? pct(s.fake_share, 0) : "-";
    const C = 2 * Math.PI * 46, fk = s.total ? s.fake_share : 0, d = $("#donut");
    d.innerHTML = `<circle cx="60" cy="60" r="46" fill="none" stroke="${cssVar("--real")}" stroke-width="16" opacity=".85"/>` +
      `<circle cx="60" cy="60" r="46" fill="none" stroke="${cssVar("--fake")}" stroke-width="16" stroke-dasharray="${C * fk} ${C}" style="transition:stroke-dasharray .8s"/>`;
    this.drawSpark();
  },
  rowHtml(r) {
    return `<td title="${esc(r.created_at)}">${timeAgo(r.created_at)}</td><td title="${esc(r.filename)}">${esc(r.filename)}</td><td>${esc(r.source)}</td>` +
      `<td><span class="badge ${r.verdict === "FAKE" ? "fake" : "real"}">${r.verdict}</span></td><td class="mono">${pct(r.prob_fake)}</td><td class="mono">${r.n_flagged}/${r.n_windows}</td><td class="mono">${Math.round(r.latency_ms)} ms</td>`;
  },
  renderTable() {
    const tb = $("#histTable tbody"); tb.innerHTML = ""; this.rows.forEach((r) => { const tr = document.createElement("tr"); tr.dataset.id = r.id; tr.innerHTML = this.rowHtml(r); tb.appendChild(tr); });
    $("#histEmpty").hidden = this.rows.length > 0;
  },
  onRecord(rec) {
    if (!rec || this.seen.has(rec.id)) return; this.seen.add(rec.id); this.events++;
    if ($("#onlyMine").checked && rec.client_id !== CLIENT_ID) return;
    this.rows.unshift(rec); this.rows = this.rows.slice(0, 40);
    const tr = document.createElement("tr"); tr.className = "new"; tr.dataset.id = rec.id; tr.innerHTML = this.rowHtml(rec); $("#histTable tbody").prepend(tr);
    while ($("#histTable tbody").children.length > 40) $("#histTable tbody").lastChild.remove(); $("#histEmpty").hidden = true;
    clearTimeout(this.refreshT); this.refreshT = setTimeout(() => getJSON("/api/stats").then((s) => { this.stats = s; this.renderStats(); }).catch(() => {}), 400);
    if (!$("#tab-dash").classList.contains("active")) toast(`New analysis: ${rec.verdict} (${pct(rec.prob_fake, 0)})`);
  },
  startSSE() {
    if (this.sse) this.sse.close();
    this.sse = new EventSource(API("/api/stream")); this.mode = "sse"; this.setConn("", "connecting…");
    this.sse.onopen = () => this.setConn("ok", "LIVE · server stream (SSE)");
    this.sse.onmessage = (e) => { try { const ev = JSON.parse(e.data); if (ev.type === "analysis") this.onRecord(ev.record); } catch {} };
    this.sse.onerror = () => this.setConn("bad", "reconnecting…");
  },
  async startSupabase() {
    const { createClient } = await import("https://esm.sh/@supabase/supabase-js@2");
    this.sb = createClient(CFG.supabaseUrl, CFG.supabaseAnonKey, { realtime: { params: { eventsPerSecond: 5 } } });
    this.sb.channel("analyses-feed").on("postgres_changes", { event: "INSERT", schema: "public", table: "analyses" }, (p) => this.onRecord(p.new))
      .subscribe((st) => { if (st === "SUBSCRIBED") this.setConn("ok", "LIVE · Supabase Realtime"); else if (st === "CLOSED" || st === "CHANNEL_ERROR" || st === "TIMED_OUT") { this.setConn("bad", "Supabase: " + st); if (st !== "CLOSED") this.startSSE(); } });
    this.mode = "supabase";
  },
  async init() {
    $("#onlyMine").addEventListener("change", () => this.refresh());
    if (CFG.supabaseUrl && CFG.supabaseAnonKey) { try { await this.startSupabase(); } catch (e) { console.warn("Supabase realtime unavailable, using SSE", e); this.startSSE(); } }
    else this.startSSE();
    setInterval(() => { $$("#histTable tbody tr").forEach((tr) => { const r = this.rows.find((x) => x.id === tr.dataset.id); if (r) tr.firstElementChild.textContent = timeAgo(r.created_at); }); }, 15000);
    this.refresh();
  },
  drawSpark() {
    const c = $("#sparkCanvas"); if (!c || !c.offsetParent || !this.stats) return; const { x, w, h } = fit(c), s = this.stats.latency_series;
    x.clearRect(0, 0, w, h); if (!s || s.length < 2) return; const max = Math.max(...s) * 1.15, P = { x: 8, y: 8, w: w - 16, h: h - 16 };
    x.beginPath(); s.forEach((v, i) => { const px = P.x + i / (s.length - 1) * P.w, py = P.y + P.h * (1 - v / max); i ? x.lineTo(px, py) : x.moveTo(px, py); });
    x.strokeStyle = cssVar("--primary"); x.lineWidth = 2; x.stroke(); x.lineTo(P.x + P.w, P.y + P.h); x.lineTo(P.x, P.y + P.h); x.closePath(); x.fillStyle = "rgba(108,157,255,.14)"; x.fill();
  },
};

/* ============================================================ 9. about + hero stats */
async function loadInfo() {
  try {
    const info = await getJSON("/api/model-info"); app.info = info; const R = info.results, f = R.dual_full;
    $("#heroStats").innerHTML = [[pct(f.eer.mean), "EER, unseen attacks"], [f.auc.mean.toFixed(3), "ROC-AUC"], [String(info.dataset.unseen_attacks), "unseen attack types"], [f.params_M.toFixed(1) + " M", "parameters"]]
      .map(([b, s]) => `<li><b>${b}</b><small>${s}</small></li>`).join("");
    const names = { dual_baseline: "Dual, baseline", dual_full: "Dual, final", cnn_only: "CNN only", transformer_only: "Transformer only" };
    $("#aboutResults").innerHTML = `<table class="mini"><thead><tr><th>Model</th><th>EER</th><th>AUC</th><th>Acc@0.5</th></tr></thead><tbody>` +
      Object.keys(names).map((k) => { const r = R[k]; return `<tr class="${k === "dual_full" ? "hl" : ""}"><td>${names[k]}</td><td>${pct(r.eer.mean)} <small class="muted">(${pct(r.eer.min)}–${pct(r.eer.max)})</small></td><td>${r.auc.mean.toFixed(3)}</td><td>${pct(r["accuracy@0.5"].mean)}</td></tr>`; }).join("") +
      `</tbody></table><p class="muted small">14,000 held-out clips, 11 unseen attack types, mean (range) over 2 training seeds. ${esc(info.dataset.note)}.</p>`;
    const rb = info.robustness, conds = [["clean", "clean"], ["snr20", "20 dB"], ["snr10", "10 dB"], ["snr5", "5 dB"], ["telephone", "8 kHz"]];
    const mean = (cfg, c) => { const v = Object.entries(rb).filter(([k]) => k === cfg || k === cfg + "_s1").map(([, o]) => o[c]); return v.reduce((a, b) => a + b, 0) / v.length; };
    $("#aboutRobust").innerHTML = `<table class="mini"><thead><tr><th>Model</th>${conds.map((c) => `<th>${c[1]}</th>`).join("")}</tr></thead><tbody>` +
      Object.keys(names).map((k) => `<tr class="${k === "dual_full" ? "hl" : ""}"><td>${names[k]}</td>${conds.map((c) => `<td>${pct(mean(k, c[0]))}</td>`).join("")}</tr>`).join("") +
      `</tbody></table><p class="muted small">Noise = white noise at that SNR; 8 kHz = telephone-band resampling. Without augmentation the baseline collapses.</p>`;
  } catch { /* API offline */ }
}

/* ============================================================ boot */
(async function boot() {
  const g = $(".legend-bar"); if (g && window.LUT_TURBO) g.style.background = "linear-gradient(90deg," + [0, 64, 128, 192, 255].map((i) => `rgb(${LUT_TURBO[i * 3]},${LUT_TURBO[i * 3 + 1]},${LUT_TURBO[i * 3 + 2]})`).join(",") + ")";
  showTab(location.hash.slice(1) || "analyze");
  pollHealth(); setInterval(pollHealth, 20000);
  loadSamples(); loadInfo(); dash.init();
})();
