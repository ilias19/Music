/* Practice Room — front end.
 * Library (upload / scan / gallery / stats) and Player (render, play, loop,
 * transpose, metronome, record, export). No build step: plain browser JS. */
"use strict";

// ------------------------------------------------------------------ utils --
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res.text();
}

let toastTimer;
function toast(msg, ms = 3200) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms);
}

function fmtMins(sec) {
  if (sec < 60) return sec ? "<1m" : "0m";
  const m = Math.round(sec / 60);
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`;
}
const fmtClock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// The Android app injects window.AndroidApp (keepScreenOn). In a plain browser it's absent.
const native = window.AndroidApp || null;
// The Android app says whether the phone is in dark mode (and calls this again when it changes).
window.applyNativeTheme = (t) => { document.documentElement.dataset.theme = t === "dark" ? "dark" : "light"; };
if (native?.theme) applyNativeTheme(native.theme());
const isTouch = matchMedia("(pointer: coarse)").matches;

// One colour per voice, so a part looks the same on cards, in "My part" and in Voices.
const VOICE_KINDS = [
  ["soprano", /sopr|^s\.?$|^sop/i, "#e8590c"],
  ["alto", /alt|contralto|mezzo|^a\.?$/i, "#9c36b5"],
  ["tenor", /t[eé]no|^t\.?$/i, "#0c8599"],
  ["bass", /bass|basse|bajo|baryton|bariton|^b\.?$/i, "#364fc7"],
];
const OTHER_COLOURS = ["#2f9e44", "#d6336c", "#f08c00", "#495057", "#1098ad", "#7048e8"];
function voiceKind(name) { const k = VOICE_KINDS.find(([, re]) => re.test((name || "").trim())); return k ? k[0] : null; }
function partColour(name, i = 0) {
  const k = VOICE_KINDS.find(([, re]) => re.test((name || "").trim()));
  return k ? k[2] : OTHER_COLOURS[i % OTHER_COLOURS.length];
}
function partShort(name) {
  const k = voiceKind(name);
  if (k) return k[0].toUpperCase();
  const n = (name || "?").trim();
  return n.length > 12 ? n.slice(0, 11) + "…" : n;
}
let myVoice = (() => { try { return localStorage.getItem("pr.voice") || ""; } catch { return ""; } })();
function showMyVoice() {
  $("#my-voice").value = myVoice;
  $("#voice-dot").style.setProperty("--c", myVoice ? VOICE_KINDS.find(([k]) => k === myVoice)[2] : "var(--muted)");
}
$("#my-voice").addEventListener("change", (e) => {
  myVoice = e.target.value;
  try { localStorage.setItem("pr.voice", myVoice); } catch {}
  showMyVoice();
  renderGrid();
});
showMyVoice();

// Downloads go through real server URLs with Content-Disposition, which both
// desktop browsers and the Android app's download handler can save.
function downloadUrl(url, name) {
  const a = Object.assign(document.createElement("a"), { href: url, download: name || "" });
  document.body.appendChild(a); a.click(); a.remove();
}
async function downloadBlob(blob, name) {
  const res = await api(`/api/downloads?${new URLSearchParams({ name, mime: blob.type })}`, { method: "POST", body: blob });
  downloadUrl(res.url, name);
}

// ----------------------------------------------------------------- router --
function route() {
  const m = location.hash.match(/^#\/score\/([a-f0-9]{12})/);
  if (m) openPlayer(m[1]);
  else showLibrary();
}
window.addEventListener("hashchange", route);

// ================================================================ LIBRARY ==
let scores = [];
let pollTimer = null;

async function showLibrary() {
  await Player.close();
  $("#player").hidden = true;
  $("#library").hidden = false;
  document.title = "Practice Room";
  await Promise.all([loadScores(), loadStats()]);
}

async function loadScores() {
  try { scores = await api("/api/scores"); } catch (e) { toast(e.message); return; }
  renderGrid();
  clearTimeout(pollTimer);
  if (scores.some((s) => s.status === "queued" || s.status === "scanning") && !$("#library").hidden) {
    pollTimer = setTimeout(loadScores, 2000);
  }
}

function renderGrid() {
  const q = $("#search").value.trim().toLowerCase();
  const list = scores.filter((s) => !q || s.title.toLowerCase().includes(q));
  const grid = $("#grid");
  if (!list.length) {
    grid.innerHTML = `<p class="empty">${q ? "No matches." : "Nothing here yet. Add some sheet music above."}</p>`;
    return;
  }
  grid.innerHTML = list.map((s) => {
    const busy = s.status === "queued" || s.status === "scanning";
    const failed = s.status === "failed";
    const info = s.info || {};
    const names = info.part_names && info.part_names.length ? info.part_names : [];
    const partChips = names.length > 1
      ? names.map((n, k) => `<span class="chip part" style="--c:${partColour(n, k)}" title="${esc(n || "Part " + (k + 1))}">${esc(n ? partShort(n) : k + 1)}</span>`).join("")
      : info.parts ? `<span class="chip">${info.parts} part${info.parts > 1 ? "s" : ""}</span>` : "";
    const keyTxt = info.fifths == null ? "" : info.fifths === 0 ? "no ♯/♭" : Math.abs(info.fifths) + (info.fifths > 0 ? "♯" : "♭");
    const metaTxt = [info.measures && `${info.measures} bars`, info.time, info.tempo && `♩ ${info.tempo}`, keyTxt, info.lyrics && "lyrics"]
      .filter(Boolean).join(" · ");
    const mine = names.findIndex((n) => myVoice && voiceKind(n) === myVoice);
    const c = mine >= 0 ? partColour(names[mine]) : "var(--ink)";
    const mins = Math.round((s.practice_seconds || 0) / 60);
    const badge = s.practice_seconds ? "" : s.source === "gallery" ? "Example" : "New";
    let body = `<div class="chips">${partChips}<span class="card-meta">${esc(metaTxt)}</span></div>
      <div class="card-foot"><div class="meter" style="--c:${c}"><i style="width:${Math.min(100, mins / 30 * 100)}%"></i></div>
        <span>${s.practice_seconds ? fmtMins(s.practice_seconds) + " practised" : "not practised yet"}</span></div>`;
    if (busy) {
      const pct = Math.round((s.progress || 0) * 100);
      body = `<div class="muted small">${esc(s.message || "Reading the music…")}</div>
              <div class="progress ${pct < 5 ? "indet" : ""}"><i style="width:${Math.max(pct, 5)}%"></i></div>`;
    } else if (failed) {
      body = `<div class="err">Couldn't read this one. ${esc(s.error || "")}</div>`;
    }
    return `<div class="card ${busy ? "busy" : ""} ${failed ? "failed" : ""}" data-id="${s.id}" tabindex="0" role="button">
      <div class="card-top"><div class="card-title">${esc(s.title)}</div>
        ${busy || failed ? "" : `<span class="badge ${badge === "New" ? "new" : ""}">${badge}</span>`}</div>
      ${body}
      <button class="del" data-del="${s.id}" aria-label="Delete ${esc(s.title)}" title="Delete">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg></button>
    </div>`;
  }).join("");
}

$("#grid").addEventListener("click", async (e) => {
  const del = e.target.closest("[data-del]");
  if (del) {
    e.stopPropagation();
    const s = scores.find((x) => x.id === del.dataset.del);
    if (!confirm(`Delete "${s.title}" and its recordings?`)) return;
    await api(`/api/scores/${s.id}`, { method: "DELETE" });
    loadScores();
    return;
  }
  const card = e.target.closest(".card");
  if (!card || card.classList.contains("busy") || card.classList.contains("failed")) return;
  location.hash = `#/score/${card.dataset.id}`;
});
$("#grid").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.classList.contains("card")) e.target.click();
});
$("#search").addEventListener("input", renderGrid);

async function loadStats() {
  try {
    const s = await api(`/api/practice/summary?tz_offset_minutes=${-new Date().getTimezoneOffset()}`);
    $("#stat-today").textContent = fmtMins(s.today);
    $("#stat-week").textContent = fmtMins(s.week);
    $("#stat-streak").textContent = s.streak;
    const max = Math.max(...s.days.map((d) => d.seconds), 1);
    $("#spark").innerHTML = s.days.map((d) =>
      `<i class="${d.seconds ? "" : "zero"}" style="height:${d.seconds ? Math.max(8, (d.seconds / max) * 100) : 6}%" title="${d.date}: ${fmtMins(d.seconds)}"></i>`).join("");
  } catch {}
}

// ---------------------------------------------------------------- uploads --
const XML_EXT = /\.(musicxml|xml|mxl)$/i;
const SCAN_EXT = /\.(pdf|png|jpe?g|webp|tiff?|bmp)$/i;

async function handleFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  const xmls = files.filter((f) => XML_EXT.test(f.name));
  const scans = files.filter((f) => SCAN_EXT.test(f.name) || (f.type.startsWith("image/") && !XML_EXT.test(f.name)));
  const other = files.length - xmls.length - scans.length;
  if (other) toast(`Skipped ${other} file(s): use PDF, images or MusicXML.`);
  try {
    for (const f of xmls) {
      const fd = new FormData(); fd.append("file", f);
      await api("/api/import", { method: "POST", body: fd });
    }
    if (scans.length) {
      scans.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
      const fd = new FormData();
      scans.forEach((f) => fd.append("files", f, f.name || "photo.jpg"));
      await api("/api/scan", { method: "POST", body: fd });
      toast(scans.length > 1 ? `Reading ${scans.length} pages as one score…` : "Reading the music…");
    }
  } catch (e) { toast(e.message, 5000); }
  loadScores();
}

const drop = $("#drop");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => handleFiles(e.dataTransfer.files));
$("#file-input").addEventListener("change", (e) => { handleFiles(e.target.files); e.target.value = ""; });
$("#camera-input").addEventListener("change", (e) => { handleFiles(e.target.files); e.target.value = ""; });
document.addEventListener("paste", (e) => {
  if ($("#library").hidden) return;
  const imgs = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
  if (imgs.length) handleFiles(imgs.map((f, i) => new File([f], `pasted-${i + 1}.png`, { type: f.type })));
});

// ================================================================= AUDIO ==
const Sound = (() => {
  const SAMPLE_SOURCES = ["samples/", "https://tonejs.github.io/audio/salamander/"];
  const SAMPLE_NOTES = ["A0", "C1", "D#1", "F#1", "A1", "C2", "D#2", "F#2", "A2", "C3", "D#3", "F#3", "A3",
    "C4", "D#4", "F#4", "A4", "C5", "D#5", "F#5", "A5", "C6", "D#6", "F#6", "A6", "C7", "D#7", "F#7", "A7", "C8"];
  let master, metro, metroAccent, buffers = null, loading = null;
  const voices = new Map(); // part index -> { inst, vol }

  function makeSynth() {
    return new Tone.PolySynth(Tone.Synth, {
      oscillator: { type: "fattriangle", count: 2, spread: 12 },
      envelope: { attack: 0.005, decay: 0.6, sustain: 0.25, release: 0.9 },
      volume: -10,
    });
  }

  async function loadFrom(base) {
    const entries = SAMPLE_NOTES.map((n) => [n, `${base}${n.replace("#", "s")}.mp3`]);
    const timeout = new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), 12000));
    const load = Promise.all(entries.map(([n, url]) => new Tone.ToneAudioBuffer().load(url).then((b) => [n, b])));
    return Object.fromEntries(await Promise.race([load, timeout]));
  }
  async function loadSamples() {
    for (const base of SAMPLE_SOURCES) {
      try { return await loadFrom(base); } catch {}
    }
    throw new Error("no samples");
  }

  // Sung voices: recorded "aah"/"ooh" samples (FluidR3 GM, CC-BY 3.0), loaded the first time
  // someone picks them. They're 3 s long; longer notes need more, so each one is extended with
  // crossfaded repeats of its steady middle (a choir "aah" has no attack to repeat there).
  const SUNG = {
    choir: { dir: "choir_aahs", gain: 1.0 },
    ooh: { dir: "voice_oohs", gain: 0.8 },
  };
  const SUNG_NOTES = ["A2", "C3", "Eb3", "Gb3", "A3", "C4", "Eb4", "Gb4", "A4", "C5", "Eb5", "Gb5", "A5", "C6"];
  const sungBuffers = {}, sungLoading = {};

  function extend(buf, seconds = 9) {
    const sr = buf.sampleRate, src = buf.getChannelData(0);
    const other = buf.numberOfChannels > 1 ? buf.getChannelData(1) : null;
    const mono = Float32Array.from(src, (v, i) => other ? (v + other[i]) / 2 : v);
    const L0 = Math.floor(0.9 * sr), L1 = mono.length - Math.floor(0.08 * sr), X = Math.floor(0.35 * sr);
    if (L1 - L0 < 2 * X) return { data: mono, sr };
    const out = new Float32Array(Math.floor(seconds * sr));
    out.set(mono.subarray(0, Math.min(L1, out.length)));
    let pos = L1 - X;                     // each repeat starts by fading in over the previous tail
    while (pos < out.length) {
      for (let i = 0; i < L1 - L0 && pos + i < out.length; i++) {
        const v = mono[L0 + i];
        if (i < X) {
          const a = i / X;                // equal-power crossfade
          out[pos + i] = out[pos + i] * Math.cos(a * Math.PI / 2) + v * Math.sin(a * Math.PI / 2);
        } else out[pos + i] = v;
      }
      pos += (L1 - L0) - X;
    }
    return { data: out, sr };
  }

  function loadSung(kind) {
    if (sungBuffers[kind]) return Promise.resolve(sungBuffers[kind]);
    if (!sungLoading[kind]) {
      const { dir } = SUNG[kind];
      const bases = [`samples/${dir}/`, `https://gleitz.github.io/midi-js-soundfonts/FluidR3_GM/${dir}-mp3/`];
      sungLoading[kind] = (async () => {
        for (const base of bases) {
          try {
            const pairs = await Promise.all(SUNG_NOTES.map((n) =>
              new Tone.ToneAudioBuffer().load(`${base}${n}.mp3`).then((b) => [Tone.Frequency(n).toNote(), b])));
            const ext = pairs.map(([n, b]) => [n, extend(b.get())]);
            // same loudness as the piano: normalise the set to one peak level
            const peak = Math.max(...ext.map(([, e]) => e.data.reduce((m, v) => Math.max(m, Math.abs(v)), 0))) || 1;
            const k = 0.55 * SUNG[kind].gain / peak;
            const map = {};
            for (const [n, e] of ext) {
              for (let i = 0; i < e.data.length; i++) e.data[i] *= k;
              const ab = Tone.getContext().createBuffer(1, e.data.length, e.sr);
              ab.copyToChannel(e.data, 0);
              map[n] = new Tone.ToneAudioBuffer(ab);
            }
            sungBuffers[kind] = map;
            rebuildVoices();
            return map;
          } catch (e) { console.warn("sung samples", base, e); }
        }
        throw new Error("no sung samples");
      })();
      sungLoading[kind].catch(() => toast("Voice samples unavailable — using a built-in voice."));
    }
    return sungLoading[kind];
  }

  async function init() {
    if (master) return;
    await Tone.start();
    master = new Tone.Gain(1).toDestination();
    metro = new Tone.MembraneSynth({ pitchDecay: 0.008, octaves: 2, envelope: { attack: 0.001, decay: 0.08, sustain: 0 }, volume: -8 }).connect(master);
    metroAccent = new Tone.MembraneSynth({ pitchDecay: 0.008, octaves: 2, envelope: { attack: 0.001, decay: 0.1, sustain: 0 }, volume: -3 }).connect(master);
    loading = loadSamples().then((b) => { buffers = b; rebuildVoices(); }).catch(() => {
      toast("Piano samples unavailable — using a built-in synth.");
    });
  }

  // One channel per mixer track: instrument -> volume -> master.
  const INSTRUMENTS = {
    piano: "Piano",
    choir: "Choir (aah)",
    ooh: "Voice (ooh)",
    soft: "Soft synth",
    organ: "Organ",
    bass: "Bass",
  };

  // A sung "aah": a buzzy source (like vocal folds) shaped by the three formants of the
  // vowel /a/, with a slow attack and a vibrato that comes in after the note starts.
  function makeVoice() {
    const out = new Tone.Gain(1);
    const formants = [[800, 0, 5], [1150, -5, 6], [2900, -18, 8], [3900, -26, 9]].map(([f, db, q]) => {
      const bp = new Tone.Filter({ type: "bandpass", frequency: f, Q: q });
      const g = new Tone.Gain(Tone.dbToGain(db));
      bp.connect(g); g.connect(out);
      return [bp, g];
    });
    const vib = new Tone.Vibrato({ frequency: 5.2, depth: 0.06 });
    const poly = new Tone.PolySynth(Tone.Synth, {
      oscillator: { type: "fatsawtooth", count: 3, spread: 14 },
      envelope: { attack: 0.09, decay: 0.15, sustain: 0.9, release: 0.3 },
      volume: -4,
    });
    poly.connect(vib);
    for (const [bp] of formants) vib.connect(bp);
    return {
      connect(dest) { out.connect(dest); return this; },
      triggerAttackRelease(...a) { poly.triggerAttackRelease(...a); },
      releaseAll(t) { poly.releaseAll(t); },
      dispose() { poly.dispose(); vib.dispose(); for (const [bp, g] of formants) { bp.dispose(); g.dispose(); } out.dispose(); },
    };
  }

  function makeInstrument(kind) {
    if (SUNG[kind]) {
      if (sungBuffers[kind]) return new Tone.Sampler({ urls: sungBuffers[kind], attack: 0.02, release: 0.45, volume: -2 });
      loadSung(kind);
      return makeVoice();                 // until the recordings are loaded
    }
    if (kind === "piano") return buffers ? new Tone.Sampler({ urls: buffers, release: 1 }) : makeSynth();
    if (kind === "organ") return new Tone.PolySynth(Tone.Synth, {
      oscillator: { type: "fatsine4", count: 3, spread: 18 },
      envelope: { attack: 0.02, decay: 0.1, sustain: 0.9, release: 0.25 }, volume: -16 });
    if (kind === "bass") return new Tone.PolySynth(Tone.FMSynth, {
      harmonicity: 1, modulationIndex: 3, envelope: { attack: 0.005, decay: 0.4, sustain: 0.4, release: 0.6 },
      volume: -10 });
    return makeSynth();
  }

  // instrument -> volume (dB, the slider) -> gate (0/1, mute and solo) -> master.
  // Mute must be separate from volume: Tone mutes by setting volume to -Infinity,
  // so applying the slider value afterwards would silently un-mute.
  function channel(track) {
    if (!voices.has(track)) {
      const gate = new Tone.Gain(1).connect(master);
      const vol = new Tone.Volume(0).connect(gate);
      const kind = "piano";
      const inst = makeInstrument(kind);
      inst.connect(vol);
      voices.set(track, { inst, vol, gate, muted: false, kind, sampled: isSampledReady(kind) });
    }
    return voices.get(track);
  }

  function isSampledReady(kind) {
    return (kind === "piano" && !!buffers) || (!!SUNG[kind] && !!sungBuffers[kind]);
  }

  function rebuildVoices() {
    for (const v of voices.values()) {
      if (v.sampled || !isSampledReady(v.kind)) continue;
      const inst = makeInstrument(v.kind);
      inst.connect(v.vol);
      v.inst.dispose();
      v.inst = inst; v.sampled = true;
    }
  }

  function note(track, midi, durSec, time, vel = 0.75) {
    const v = channel(track);
    if (v.muted) return;
    v.inst.triggerAttackRelease(Tone.Frequency(midi, "midi").toFrequency(), Math.max(0.05, durSec), time, vel);
  }

  function click(time, accent) { (accent ? metroAccent : metro).triggerAttackRelease(accent ? "C6" : "G5", 0.03, time); }

  function setTrack(track, { mute, db, instrument }) {
    const v = channel(track);
    if (db !== undefined) v.vol.volume.value = db;
    if (mute !== undefined) {
      v.muted = mute;
      v.gate.gain.rampTo(mute ? 0 : 1, 0.02);   // also silences notes already sounding
    }
    if (instrument && instrument !== v.kind) {
      v.inst.releaseAll?.();
      v.inst.dispose();
      v.inst = makeInstrument(instrument);
      v.inst.connect(v.vol);
      v.kind = instrument;
      v.sampled = isSampledReady(instrument);
    }
  }

  function silence() {
    if (!master) return;
    const now = Tone.now();
    master.gain.cancelScheduledValues(now);
    master.gain.setValueAtTime(master.gain.value, now);
    master.gain.linearRampToValueAtTime(0, now + 0.04);
    for (const v of voices.values()) v.inst.releaseAll?.(now + 0.04);
    // Stay closed until the next play(): sample releases ring for ~1 s and notes
    // already scheduled in the look-ahead window would otherwise still be heard.
  }

  function open(at) {
    if (!master) return;
    master.gain.cancelScheduledValues(at);
    master.gain.setValueAtTime(0, at);
    master.gain.linearRampToValueAtTime(1, at + 0.01);
  }

  return { init, note, click, setTrack, silence, open, INSTRUMENTS, get ready() { return !!master; }, get loading() { return loading; },
    get sungLoaded() { return Object.keys(sungBuffers); }, get sungBuffers() { return sungBuffers; },
    prepare(kind) { if (SUNG[kind]) loadSung(kind).catch(() => {}); } };
})();

// Many files repeat the title as the movement title; OSMD would print it twice.
function dropDuplicateSubtitle(o) {
  const sh = o.Sheet;
  const t = sh?.Title?.text?.trim(), st = sh?.Subtitle?.text?.trim();
  if (t && st && t.toLowerCase() === st.toLowerCase()) sh.Subtitle = undefined;
}

// A long title is centred on the page and can run off both edges on a phone: shrink it to fit.
const TITLE_HEIGHT = 4;   // OSMD's default SheetTitleHeight
function fitTitle(o, box) {
  const rules = o.EngravingRules;
  if (!rules) return;
  const title = o.Sheet?.Title?.text;
  const find = () => [...box.querySelectorAll("svg text")].find((t) => t.textContent === title);
  if (rules.SheetTitleHeight !== TITLE_HEIGHT) { rules.SheetTitleHeight = TITLE_HEIGHT; o.render(); }
  for (let i = 0; i < 3 && title; i++) {
    const el = find(), svg = box.querySelector("svg");
    if (!el || !svg) return;
    const tw = el.getBoundingClientRect().width, sw = svg.getBoundingClientRect().width;
    if (tw <= sw * 0.94) return;
    rules.SheetTitleHeight = Math.max(1.6, rules.SheetTitleHeight * (sw * 0.9) / tw);
    o.render();
  }
}

// VexFlow sometimes computes a negative width for a short line (e.g. a 1st/2nd-ending
// bracket squeezed on a phone); browsers reject that with console errors. Clamp it to 0
// by patching the shared SVG drawing context once, using a tiny off-screen render.
let rectGuardDone = false;
async function installRectGuard() {
  if (rectGuardDone) return;
  rectGuardDone = true;
  const holder = Object.assign(document.createElement("div"), { className: "pdf-render" });
  document.body.appendChild(holder);
  try {
    const o = new opensheetmusicdisplay.OpenSheetMusicDisplay(holder, { autoResize: false, backend: "svg", drawTitle: false });
    await o.load(`<?xml version="1.0" encoding="UTF-8"?><score-partwise version="3.1"><part-list><score-part id="P1"><part-name></part-name></score-part></part-list>
      <part id="P1"><measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time>
      <clef><sign>G</sign><line>2</line></clef></attributes><note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration><type>whole</type></note></measure></part></score-partwise>`);
    o.render();
    const ctx = o.Drawer?.Backends?.[0]?.getContext?.();
    const proto = ctx && Object.getPrototypeOf(ctx);
    for (const name of ["rect", "fillRect"]) {
      const orig = proto?.[name];
      if (typeof orig !== "function") continue;
      proto[name] = function (x, y, w, h, ...rest) {
        return orig.call(this, x, y, w < 0 ? 0 : w, h < 0 ? 0 : h, ...rest);
      };
    }
  } catch (e) {
    console.warn("rect guard not installed", e);
  } finally {
    holder.remove();
  }
}

// ================================================================ PLAYER ==
const Player = (() => {
  let osmd = null, sid = null, meta = null;
  let steps = [];          // [{ q, measure, notes:[{part, midi, dur}] , it }]
  let measures = [];       // [{ start, len, beats, beatQ, number }]
  let totalQ = 0;
  let semitones = 0, bpm = 100, scoreBpm = 100;
  let loop = { on: false, a: null, b: null };
  let metroOn = false, countIn = true;
  // Mixer: one track per part / voice, with chords split into melody (top note) and harmony.
  let tracks = [];          // [{ key, name, count, mute, solo, db, instrument, hidden, notes:[Note] }]
  let xmlStats = { lyrics: 0, chords: 0 };
  const ZOOM_MIN = 0.2, ZOOM_MAX = 2.5, ZOOM_DEFAULT = 0.3;
  let userZoom = (() => { try { return parseFloat(localStorage.getItem("pr.zoom4")) || ZOOM_DEFAULT; } catch { return ZOOM_DEFAULT; } })();
  let trackState = {};      // key -> saved settings, survives transposition
  // "My part": the part you sing. The others play quieter (or not at all), and the page shows
  // your staff. my = part index, or null for the full choir.
  let parts = [];           // [{ index, name, colour }]
  let mix = { my: null, others: 35, mineSilent: false, mineOnly: true };
  const mixKey = () => `pr.mix.${sid}`;
  function saveMix() { try { localStorage.setItem(mixKey(), JSON.stringify(mix)); } catch {} }
  function loadMix() {
    try { const m = JSON.parse(localStorage.getItem(mixKey()) || "null"); if (m) return { ...mix, ...m }; } catch {}
    return null;
  }

  // transport state
  let playing = false, posQ = 0;
  // Where the music actually starts: intros of empty bars are skipped unless the user picks a bar there.
  let introEndQ = 0, introBars = 0, playIntro = false;
  let anchorQ = 0, anchorT = 0, schedQ = 0, schedTimer = null, raf = null, curIdx = -1, stopAtT = null;
  const LOOKAHEAD = 0.15;

  // practice timer
  let sessionSec = 0, unsent = 0, tick = null, rec = null;

  // ---------------------------------------------------------- loading --
  async function open(id) {
    $("#zoom-val").textContent = `${Math.round(userZoom * 100)}%`;
    if (sid !== id) { semitones = 0; loop = { on: false, a: null, b: null }; posQ = 0; tracks = []; trackState = {}; playIntro = false; }
    sid = id;
    mix = loadMix() || { my: undefined, others: 35, mineSilent: false, mineOnly: true };
    $("#loading").hidden = false;
    $("#loading").textContent = "Loading score…";
    meta = await api(`/api/scores/${id}`);
    api(`/api/scores/${id}/opened`, { method: "POST" }).catch(() => {});
    $("#score-title").textContent = meta.title;
    document.title = `${meta.title} · Practice Room`;
    $("#compare-btn").hidden = !meta.page_count;
    $("#side-compare").hidden = !meta.page_count;
    showScanLog(meta.info?.scan_log);
    updateMeta();
    $("#tr-out").textContent = fmtSemis(semitones);
    await loadXml(true);
    loadRecordings();
    startTick();
  }

  async function loadXml(first = false) {
    await installRectGuard();
    const xml = await api(`/api/scores/${sid}/musicxml?semitones=${semitones}`);
    if (!osmd) {
      watchWidth();
      osmd = new opensheetmusicdisplay.OpenSheetMusicDisplay("osmd", {
        autoResize: false, backend: "svg", drawTitle: true, drawComposer: true, drawPartNames: true,
        // one rest per bar instead of "7 bars rest" blocks: every bar can be tapped, looped and counted
        autoGenerateMultipleRestMeasuresFromRestMeasures: false,
        followCursor: false, cursorsOptions: [{ type: 0, color: "#364fc7", alpha: 0.3, follow: false }],
      });
    }
    xmlStats = { lyrics: (xml.match(/<lyric[\s>]/g) || []).length, chords: (xml.match(/<harmony[\s>]/g) || []).length };
    await osmd.load(xml);
    dropDuplicateSubtitle(osmd);
    renderScore();
    buildTimeline();
    findParts();
    if (applyHidden()) renderScore();
    if (first) {
      scoreBpm = Math.round(osmd.Sheet.DefaultStartTempoInBpm || meta.info?.tempo || 100);
      setTempo(scoreBpm);
    }
    renderFocus();
    renderMixer();
    afterRender();
    $("#loading").hidden = true;
  }

  // Render at the chosen zoom, shrinking it if the score would be wider than the screen
  // (part names and dense bars can't fit a phone at 100%, and the page would scroll sideways).
  function renderScore() {
    const box = $("#osmd");
    let zoom = userZoom;
    osmd.zoom = zoom;
    osmd.render();
    for (let i = 0; i < 3; i++) {
      const svg = box.querySelector("svg");
      const avail = box.clientWidth;
      const need = Math.max(box.scrollWidth, svg ? svg.getBoundingClientRect().width : 0);
      if (!avail || need <= avail + 2) break;
      zoom = Math.max(0.2, zoom * (avail / need) * 0.98);
      osmd.zoom = zoom;
      osmd.render();
    }
    fitTitle(osmd, box);
  }

  function afterRender() {
    if (!osmd) return;
    hideOrphanBeams();
    osmd.cursor.show();
    curIdx = -1;
    showPosition(posQ, true);
    drawLoop();
  }

  // Walk OSMD's iterator once: every cursor position becomes a step with
  // its notes; ties are merged into their first note; time is monotonic so
  // repeats (if the iterator follows them) still play in order.
  function buildTimeline() {
    const sheet = osmd.Sheet;
    const insts = sheet.Instruments;
    const cursor = osmd.cursor;
    cursor.reset();
    const it = cursor.Iterator.clone();
    steps = [];
    tracks = [];
    const byKey = new Map();
    function trackFor(part, voice, role) {
      const key = `${part}|${voice}|${role}`;
      if (!byKey.has(key)) {
        const saved = trackState[key] || {};
        const t = { key, part, voice, role, count: 0, notes: [],
          mute: !!saved.mute, solo: !!saved.solo, db: saved.db ?? 0,
          instrument: saved.instrument || "piano", hidden: !!saved.hidden };
        byKey.set(key, t); tracks.push(t);
      }
      return byKey.get(key);
    }
    let clock = 0, prevTs = null, prevMin = 1;
    while (!it.EndReached) {
      const ts = it.currentTimeStamp.RealValue * 4;
      if (prevTs !== null) clock += ts > prevTs ? ts - prevTs : prevMin;
      prevTs = ts;
      const notes = [];
      let minLen = Infinity;
      const lyr = {};
      for (const ve of it.CurrentVoiceEntries) {
        const part = Math.max(0, insts.indexOf(ve.ParentVoice.Parent));
        const voice = ve.ParentVoice.VoiceId ?? 1;
        if (lyr[part] === undefined) {
          const syl = lyricOf(ve);
          if (syl) lyr[part] = syl;
        }
        const pitched = ve.Notes.filter((n) => !n.isRest() && n.halfTone !== undefined);
        const top = pitched.length > 1 ? Math.max(...pitched.map((n) => n.halfTone)) : null;
        for (const n of ve.Notes) {
          const len = n.Length.RealValue * 4;
          if (len > 0) minLen = Math.min(minLen, len);
          if (n.isRest() || n.halfTone === undefined) continue;
          const role = top !== null && n.halfTone !== top ? "harmony" : "melody";
          const t = trackFor(part, voice, role);
          t.notes.push(n);
          if (n.IsGraceNote) continue;
          const tie = n.NoteTie;
          if (tie && tie.StartNote !== n) continue;
          t.count++;
          const dur = (tie ? tie.Duration.RealValue : n.Length.RealValue) * 4;
          notes.push({ track: t.key, midi: n.halfTone + 12, dur });
        }
      }
      prevMin = isFinite(minLen) ? minLen : 1;
      steps.push({ q: clock, measure: it.CurrentMeasureIndex, notes, it: it.clone(), lyr });
      it.moveToNext();
    }
    // measures: first occurrence start + nominal length from the score
    const src = sheet.SourceMeasures;
    measures = src.map((m, i) => {
      const ts = m.ActiveTimeSignature;
      const den = ts ? ts.Denominator : 4, num = ts ? ts.Numerator : 4;
      const beatQ = 4 / den;
      const len = m.Duration.RealValue * 4;
      return { start: null, len, beatQ, beats: Math.max(1, Math.round(len / beatQ)), number: m.MeasureNumber ?? i + 1, num };
    });
    for (const s of steps) if (measures[s.measure] && measures[s.measure].start === null) measures[s.measure].start = s.q;
    let acc = 0;
    for (const m of measures) { if (m.start === null) m.start = acc; acc = m.start + m.len; }
    const last = steps[steps.length - 1];
    totalQ = last ? Math.max(acc, last.q + Math.max(...last.notes.map((n) => n.dur), prevMin)) : 0;
    const firstSound = steps.find((st) => st.notes.length);
    const introBar = firstSound ? firstSound.measure : 0;
    introEndQ = measures[introBar]?.start ?? 0;
    introBars = introBar;
    nameTracks(insts);
    // notes refer to tracks by key; turn that into the track's index for the audio engine
    const idx = new Map(tracks.map((t, i) => [t.key, i]));
    for (const st of steps) for (const n of st.notes) n.track = idx.get(n.track);
    cursor.reset();
  }

  // The first verse's syllable on a voice entry, with a trailing "-" when the word goes on.
  function lyricOf(ve) {
    const d = ve.LyricsEntries;
    if (!d) return "";
    let entries = [];
    try { entries = typeof d.values === "function" ? d.values() : Object.values(d.table || d); } catch { return ""; }
    const e = entries.map((x) => x?.value ?? x).find((x) => x && x.Text !== undefined);
    if (!e || !e.Text) return "";
    const w = e.Word, i = e.SyllableIndex ?? (w?.Syllables ? w.Syllables.indexOf(e) : -1);
    const more = w?.Syllables && i >= 0 && i < w.Syllables.length - 1;
    return e.Text + (more ? "-" : "");
  }

  function findParts() {
    const insts = osmd.Sheet.Instruments;
    const used = [...new Set(tracks.map((t) => t.part))].sort((a, b) => a - b);
    parts = used.map((i) => ({ index: i, name: insts[i]?.Name || `Part ${i + 1}`, colour: partColour(insts[i]?.Name, i) }));
    if (mix.my === undefined) {   // first time on this score: start on the part you sing
      const p = parts.find((x) => myVoice && voiceKind(x.name) === myVoice);
      mix.my = parts.length > 1 && p ? p.index : null;
    }
    if (mix.my !== null && !parts.some((x) => x.index === mix.my)) mix.my = null;
    if (parts.length < 2) mix.my = null;
  }
  const partOf = (i) => parts.find((x) => x.index === i);

  function renderFocus() {
    const multi = parts.length > 1;
    $("#focus").hidden = !multi;
    const my = mix.my !== null ? partOf(mix.my) : null;
    $("#player").style.setProperty("--part", my ? my.colour : "#364fc7");
    $("#paper").classList.toggle("no-part", !my);
    if (!multi) return;
    $("#mypart").innerHTML = parts.map((p) => `<button role="radio" data-part="${p.index}" aria-checked="${mix.my === p.index}"
        style="--c:${p.colour}" title="${esc(p.name)}">${esc(p.name)}</button>`).join("") +
      `<button role="radio" class="all" data-part="all" aria-checked="${mix.my === null}" title="Everyone at the same level">All</button>`;
    $("#others-row").hidden = mix.my === null;
    $("#others").value = mix.mineSilent ? 100 : mix.others;
    $("#others-out").textContent = mix.mineSilent ? "you sing" : othersLabel(mix.others);
  }
  function othersLabel(v) { return v <= 0 ? "off" : v < 25 ? "faint" : v < 60 ? "quiet" : v < 95 ? "softer" : "equal"; }

  function setMix(change) {
    Object.assign(mix, change);
    saveMix();
    syncSound();
    renderFocus();
    renderMixer();
    refreshShown();
    showLyrics(true);
  }

  function nameTracks(insts) {
    tracks = tracks.filter((t) => t.count > 0);
    tracks.sort((a, b) => a.part - b.part || a.voice - b.voice || (a.role === "melody" ? -1 : 1));
    const parts = new Set(tracks.map((t) => t.part));
    for (const t of tracks) {
      const sameVoice = tracks.filter((u) => u.part === t.part && u.voice === t.voice);
      const voices = new Set(tracks.filter((u) => u.part === t.part).map((u) => u.voice));
      const bits = [];
      if (parts.size > 1) bits.push(insts[t.part]?.Name || `Part ${t.part + 1}`);
      if (voices.size > 1) bits.push(`Voice ${t.voice}`);
      if (sameVoice.length > 1) bits.push(t.role === "melody" ? "Melody" : "Harmony");
      t.name = bits.join(" · ") || "Melody";
    }
  }

  // Mark the notes of hidden tracks invisible. Returns true if anything is hidden (needs a re-render).
  // What the page shows. With "Only show the parts you hear" on (the default), a muted track, or
  // every track but the soloed ones, disappears from the page too: its notes, and its whole staff
  // when nothing on that staff is left (a choir part). Never everything: then all is shown.
  let followMix = (() => { try { return localStorage.getItem("pr.followMix") !== "0"; } catch { return true; } })();
  // A track switched off in the mixer (its checkbox) is neither heard nor shown. Among the
  // tracks that are on, solo wins over mute.
  function othersDb() { return mix.others <= 0 ? -Infinity : 20 * Math.log10(mix.others / 100); }
  function trackDb(t) {
    if (mix.my === null || t.part === mix.my || mix.mineSilent) return t.db;
    return t.db + othersDb();
  }
  function audible(t) {
    if (t.hidden) return false;
    if (mix.my !== null && mix.mineSilent && t.part === mix.my) return false;
    if (mix.my !== null && !mix.mineSilent && mix.others <= 0 && t.part !== mix.my) return false;
    return tracks.some((x) => x.solo && !x.hidden) ? t.solo : !t.mute;
  }
  function shown(t) {
    if (mix.my !== null && mix.mineOnly) {
      return t.part === mix.my && !t.hidden;
    }
    const on = followMix ? audible(t) : !t.hidden;
    return tracks.some((x) => (followMix ? audible(x) : !x.hidden)) ? on : true;   // never an empty page
  }
  let shownKey = "";
  function applyHidden() {
    let any = false;
    for (const t of tracks) {
      const on = shown(t);
      for (const n of t.notes) n.PrintObject = on;
      any = any || !on;
    }
    // staves with nothing left to show go away, as long as one staff stays
    const insts = osmd.Sheet.Instruments;
    const partShown = insts.map((_, i) => tracks.some((t) => t.part === i && shown(t)) || !tracks.some((t) => t.part === i));
    const keep = partShown.some(Boolean);
    insts.forEach((inst, i) => { inst.Visible = keep ? partShown[i] : true; });
    shownKey = tracks.map(shown).join() + "|" + insts.map((x) => x.Visible).join();
    return any || insts.some((x) => !x.Visible);
  }
  function refreshShown() {
    const before = shownKey;
    applyHidden();
    if (shownKey !== before) { renderScore(); afterRender(); }
  }

  // Invisible notes still get their beams drawn: hide every beam whose notes are all hidden.
  function hideOrphanBeams() {
    if (!tracks.some((t) => !shown(t))) return;
    const hidden = new Set();
    for (const list of osmd.GraphicSheet.MeasureList) for (const gm of list || []) {
      if (!gm) continue;
      for (const se of gm.staffEntries) for (const gve of se.graphicalVoiceEntries) for (const gn of gve.notes) {
        if (gn.sourceNote && gn.sourceNote.PrintObject === false && gn.vfnote) hidden.add(gn.vfnote[0]);
      }
    }
    for (const list of osmd.GraphicSheet.MeasureList) for (const gm of list || []) {
      if (!gm) continue;
      for (const group of [gm.vfbeams, gm.autoVfBeams]) {
        if (!group) continue;
        for (const beams of Object.values(group)) for (const beam of beams || []) {
          if (beam?.notes?.length && beam.notes.every((n) => hidden.has(n))) {
            // OSMD names a beam's SVG group after its first note: vf-<note id>-beam<n>
            const first = beam.notes[0]?.attrs?.id;
            if (first) document.querySelectorAll(`#osmd [id^="vf-${first}-beam"]`).forEach((el) => (el.style.display = "none"));
          }
        }
      }
    }
  }

  function saveTrack(t) {
    trackState[t.key] = { mute: t.mute, solo: t.solo, db: t.db, instrument: t.instrument, hidden: t.hidden };
  }

  // Effective audibility: solo wins over mute.
  function syncSound() {
    tracks.forEach((t) => Sound.prepare(t.instrument));   // start loading recorded voices right away
    if (!Sound.ready) return;
    tracks.forEach((t, i) => Sound.setTrack(i, { mute: !audible(t), db: trackDb(t), instrument: t.instrument }));
  }

  function faderPos(db) { return Math.max(0, Math.min(1, (db + 30) / 36)); }
  function renderMixer() {
    const anyHidden = tracks.some((t) => t.hidden);
    $("#mixer-hide-all").textContent = anyHidden ? "All on" : "All off";
    const multi = parts.length > 1;
    $("#quick").hidden = !multi;
    $("#mine-only-row").hidden = !multi || mix.my === null;
    $("#mine-only").checked = mix.mineOnly;
    $("#mix-learn").setAttribute("aria-pressed", String(mix.my !== null && !mix.mineSilent));
    $("#mix-sing").setAttribute("aria-pressed", String(mix.my !== null && mix.mineSilent));
    $("#mix-full").setAttribute("aria-pressed", String(mix.my === null));
    $("#mixer-tracks").innerHTML = tracks.map((t, i) => {
      const p = partOf(t.part);
      const c = p ? p.colour : partColour(t.name, i);
      const eff = trackDb(t);
      const dbTxt = !audible(t) ? "silent" : `${eff > 0 ? "+" : ""}${Number.isFinite(eff) ? Math.round(eff) : "−∞"} dB`;
      const pos = faderPos(t.db);
      return `
      <div class="track ${t.solo ? "soloed" : ""}" data-track="${i}" style="--c:${c}">
        <span class="track-name" title="${esc(t.name)}">${esc(t.name)}</span>
        <span class="track-count">${t.count} notes</span>
        <select data-act="instrument" aria-label="Sound for ${esc(t.name)}">
          ${Object.entries(Sound.INSTRUMENTS).map(([k, v]) => `<option value="${k}" ${k === t.instrument ? "selected" : ""}>${v}</option>`).join("")}
        </select>
        <div class="fader">
          <i style="height:calc(${pos} * (100% - 12px))"></i>
          <b style="bottom:calc(${pos} * (100% - 26px) + 6px)"></b>
          <input type="range" min="-30" max="6" step="1" value="${t.db}" data-act="vol" aria-label="Volume ${esc(t.name)}">
        </div>
        <span class="track-db">${dbTxt}</span>
        <div class="track-btns">
          <button data-act="solo" aria-pressed="${t.solo}" aria-label="Solo ${esc(t.name)}">S</button>
          <button data-act="mute" aria-pressed="${t.mute}" aria-label="Mute ${esc(t.name)}">M</button>
        </div>
        <label class="track-show" title="Play this track and show its notes">
          <input type="checkbox" data-act="show" ${t.hidden ? "" : "checked"}> On
        </label>
      </div>`;
    }).join("") || `<p class="muted">This score has no notes to mix.</p>`;
    $("#mixer-btn").classList.toggle("has-changes", tracks.some((t) => t.mute || t.solo || t.hidden || t.db !== 0 || t.instrument !== "piano"));
  }

  async function setHidden(list, hidden) {
    list.forEach((t) => { t.hidden = hidden; saveTrack(t); });
    syncSound();
    applyHidden();
    renderScore();
    afterRender();
    renderMixer();
  }

  // ------------------------------------------------------ geometry --
  function unit() { return 10 * osmd.zoom; }
  function svgOffset() {
    const svg = $("#osmd svg"); const paper = $("#paper");
    if (!svg) return { x: 0, y: 0 };
    const a = svg.getBoundingClientRect(), b = paper.getBoundingClientRect();
    return { x: a.left - b.left, y: a.top - b.top };
  }
  // Horizontal extent from the bar itself, vertical extent from its whole
  // system, so highlighted bars line up into one even band per system.
  function measureBox(i) {
    const list = osmd.GraphicSheet.MeasureList[i];
    if (!list) return null;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const gm of list) {
      if (!gm) continue;
      const ps = gm.PositionAndShape, p = ps.AbsolutePosition;
      x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x + ps.Size.width);
      const sys = gm.ParentMusicSystem || gm.ParentStaffLine?.ParentMusicSystem;
      const lines = sys?.StaffLines?.length ? sys.StaffLines.map((l) => l.PositionAndShape) : [ps];
      for (const l of lines) {
        y0 = Math.min(y0, l.AbsolutePosition.y);
        y1 = Math.max(y1, l.AbsolutePosition.y + Math.max(l.Size.height, 4));
      }
    }
    if (!isFinite(x0)) return null;
    const u = unit(), o = svgOffset();
    return { x: o.x + x0 * u, y: o.y + (y0 - 2.5) * u, w: (x1 - x0) * u, h: (y1 - y0 + 5) * u };
  }
  function measureAt(clientX, clientY) {
    const b = $("#paper").getBoundingClientRect();
    const x = clientX - b.left, y = clientY - b.top;
    let best = null, bestD = Infinity;
    for (let i = 0; i < measures.length; i++) {
      const r = measureBox(i);
      if (!r) continue;
      if (x >= r.x && x <= r.x + r.w && y >= r.y - 10 && y <= r.y + r.h + 10) {
        const d = Math.abs(y - (r.y + r.h / 2));
        if (d < bestD) { best = i; bestD = d; }
      }
    }
    return best;
  }

  function drawLoop() {
    const layer = $("#loop-layer");
    layer.innerHTML = "";
    if (loop.a === null) { $("#loop-range").textContent = "off"; return; }
    const a = loop.a, b = loop.b ?? loop.a;
    for (let i = a; i <= b; i++) {
      const r = measureBox(i);
      if (!r) continue;
      const d = document.createElement("div");
      d.className = "bar-hl" + (loop.on ? "" : " pending");
      Object.assign(d.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });
      layer.appendChild(d);
    }
    const na = measures[a]?.number, nb = measures[b]?.number;
    $("#loop-range").textContent = loop.on ? (a === b ? `bar ${na}` : `bars ${na}–${nb}`) : "off";
  }

  // ------------------------------------------------------ cursor --
  function stepIndexAt(q) {
    let lo = 0, hi = steps.length - 1, ans = 0;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (steps[mid].q <= q + 1e-6) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
    return ans;
  }
  function showPosition(q, scroll = false) {
    if (!steps.length) return;
    const idx = stepIndexAt(q);
    const st = steps[idx];
    if (idx !== curIdx) {
      const prevTop = osmd.cursor.cursorElement?.offsetTop;
      osmd.cursor.iterator = st.it.clone();
      osmd.cursor.update();
      curIdx = idx;
      const el = osmd.cursor.cursorElement;
      if (el && (scroll || el.offsetTop !== prevTop)) {
        const sc = $("#paper-scroll"), r = el.getBoundingClientRect(), sr = sc.getBoundingClientRect();
        if (r.top < sr.top + 40 || r.bottom > sr.bottom - 40 || scroll) {
          sc.scrollBy({ top: r.top - sr.top - sr.height * 0.28, behavior: playing ? "smooth" : "auto" });
        }
      }
    }
    const num = measures[st.measure]?.number ?? 1;
    $("#pos").textContent = num === 0 ? "Pickup" : `Bar ${num}`;
    showLyrics();
  }

  // The words of the current bar for my part (or the first part with words), the sung one lit.
  let lyricKey = "";
  function showLyrics(force = false) {
    const strip = $("#lyric-strip");
    if (!steps.length || curIdx < 0) { strip.hidden = true; return; }
    const withWords = (p) => steps.some((x) => x.lyr && x.lyr[p]);
    let p = mix.my !== null ? mix.my : parts.map((x) => x.index).find(withWords);
    if (p === undefined && steps.some((x) => x.lyr && x.lyr[0])) p = 0;
    if (p === undefined || !withWords(p)) { strip.hidden = true; return; }
    const m = steps[curIdx].measure;
    // this bar, plus the next one when this bar has only a word or two
    let inBar = steps.map((x, i) => ({ x, i })).filter(({ x }) => x.measure === m && x.lyr?.[p]);
    if (inBar.length < 3) inBar = inBar.concat(steps.map((x, i) => ({ x, i })).filter(({ x }) => x.measure === m + 1 && x.lyr?.[p]));
    let now = -1;
    for (const { i } of inBar) if (i <= curIdx) now = i;
    const key = `${p}|${m}|${now}`;
    if (key === lyricKey && !force) return;
    lyricKey = key;
    const name = partOf(p)?.name || "";
    const num = measures[m]?.number ?? m + 1;
    $("#lyric-head").textContent = [num === 0 ? "Pickup" : `Bar ${num}`, parts.length > 1 ? name : ""].filter(Boolean).join(" · ");
    $("#lyric-line").innerHTML = inBar.length ? inBar.map(({ x, i }) => {
      const t = x.lyr[p];
      const cls = i === now ? "now" : i < now ? "past" : "";
      return `<span class="${cls}">${esc(t)}</span>`;
    }).join(" ").replace(/-<\/span> </g, "-</span>") : "<span class=\"past\">…</span>";
    strip.hidden = false;
  }

  // ------------------------------------------------------ transport --
  const qAt = (t) => anchorQ + (t - anchorT) * bpm / 60;
  const tAt = (q) => anchorT + (q - anchorQ) * 60 / bpm;
  const loopStart = () => measures[loop.a].start;
  const loopEnd = () => { const m = measures[loop.b ?? loop.a]; return m.start + m.len; };

  async function play() {
    if (!steps.length) return;
    await Sound.init();
    syncSound();
    if (loop.on && (posQ < loopStart() || posQ >= loopEnd())) posQ = loopStart();
    if (posQ >= totalQ - 1e-6) posQ = 0;
    if (!loop.on && !playIntro && introBars > 0 && posQ < introEndQ - 1e-6) {
      posQ = introEndQ;
      toast(`Skipped ${introBars} empty bar${introBars > 1 ? "s" : ""} at the start (tap bar 1 to hear them)`);
    }
    let start = Tone.now() + 0.08;
    Sound.open(start - 0.01);
    if (countIn) {
      const m = measures[steps[stepIndexAt(posQ)].measure] || measures[0];
      const beatSec = (m.beatQ * 60) / bpm;
      const n = m.num || m.beats;
      for (let b = 0; b < n; b++) Sound.click(start + b * beatSec, b === 0);
      start += n * beatSec;
    }
    anchorQ = posQ; anchorT = start; schedQ = posQ; stopAtT = null;
    playing = true;
    native?.keepScreenOn?.(true);
    $("#play").classList.add("playing"); $("#play").setAttribute("aria-label", "Pause");
    schedule();
    schedTimer = setInterval(schedule, 40);
    raf = requestAnimationFrame(frame);
  }

  function pause(resetTo = null) {
    if (playing) {
      posQ = Math.max(0, Math.min(qAt(Tone.now()), totalQ));
      if (loop.on && posQ >= loopEnd()) posQ = loopStart();
    }
    playing = false;
    native?.keepScreenOn?.(!!rec);
    clearInterval(schedTimer); cancelAnimationFrame(raf);
    Sound.silence();
    $("#play").classList.remove("playing"); $("#play").setAttribute("aria-label", "Play");
    if (resetTo !== null) posQ = resetTo;
    showPosition(posQ);
  }

  function schedule() {
    if (!playing) return;
    const horizon = Tone.now() + LOOKAHEAD;
    let guard = 0;
    while (guard++ < 8) {
      const endQ = qAt(horizon);
      const limit = loop.on ? loopEnd() : totalQ;
      const upto = Math.min(endQ, limit);
      if (upto > schedQ) {
        // notes
        for (let i = stepIndexAt(schedQ); i < steps.length && steps[i].q < upto; i++) {
          const s = steps[i];
          if (s.q < schedQ - 1e-6) continue;
          for (const n of s.notes) Sound.note(n.track, n.midi, (n.dur * 60) / bpm * 0.97, tAt(s.q));
        }
        // metronome
        if (metroOn) {
          for (const m of measures) {
            if (m.start + m.len <= schedQ || m.start >= upto) continue;
            for (let b = 0; b < m.beats; b++) {
              const q = m.start + b * m.beatQ;
              if (q >= schedQ - 1e-6 && q < upto) Sound.click(tAt(q), b === 0);
            }
          }
        }
        schedQ = upto;
      }
      if (endQ < limit) break;
      // reached loop end or piece end inside this window
      if (loop.on) {
        const t = tAt(loopEnd());
        anchorQ = loopStart(); anchorT = t; schedQ = anchorQ;
        continue;
      }
      if (stopAtT === null) stopAtT = tAt(totalQ);
      break;
    }
  }

  function frame() {
    if (!playing) return;
    const now = Tone.now();
    if (stopAtT !== null && now >= stopAtT + 0.3) { pause(0); return; }
    const q = qAt(now);
    if (q >= anchorQ - 1e-6) showPosition(Math.min(q, totalQ - 1e-6));
    raf = requestAnimationFrame(frame);
  }

  function seek(q) {
    const was = playing;
    if (was) pause();
    posQ = q;
    showPosition(q);
    if (was) play();
  }

  function setTempo(v) {
    v = Math.max(30, Math.min(220, Math.round(v)));
    if (playing) { const now = Tone.now(); const q = qAt(now); anchorQ = Math.max(q, anchorQ); anchorT = Math.max(now, anchorT); }
    bpm = v;
    $("#tempo").value = v; $("#tempo-out").value = v;
    $$(".sp").forEach((b) => b.setAttribute("aria-pressed", String(Math.round(scoreBpm * +b.dataset.speed) === v)));
  }

  // ------------------------------------------------------ practice --
  function startTick() {
    clearInterval(tick);
    sessionSec = 0; unsent = 0;
    $("#timer").textContent = "0:00";
    tick = setInterval(() => {
      const active = (playing || rec) && document.visibilityState === "visible";
      $("#timer").classList.toggle("on", !!active);
      if (!active) return;
      sessionSec++; unsent++;
      $("#timer").textContent = fmtClock(sessionSec);
      if (unsent >= 30) flush();
    }, 1000);
  }
  function flush(beacon = false) {
    if (!sid || unsent < 1) return;
    const body = JSON.stringify({ score_id: sid, seconds: unsent });
    unsent = 0;
    if (beacon && navigator.sendBeacon) navigator.sendBeacon("/api/practice", new Blob([body], { type: "application/json" }));
    else fetch("/api/practice", { method: "POST", headers: { "content-type": "application/json" }, body, keepalive: true }).catch(() => {});
  }
  window.addEventListener("pagehide", () => flush(true));

  // ------------------------------------------------------ recording --
  async function toggleRecord() {
    if (rec) { rec.stop(); return; }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      toast("Recording isn't supported in this browser."); return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
    } catch { toast("Microphone access was blocked."); return; }
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
    const r = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    const chunks = []; const started = performance.now();
    r.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    r.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      rec = null; $("#rec").classList.remove("on"); $("#rec").setAttribute("aria-label", "Record");
      native?.keepScreenOn?.(playing);
      const blob = new Blob(chunks, { type: r.mimeType || "audio/webm" });
      const secs = (performance.now() - started) / 1000;
      if (secs < 1) return;
      const fd = new FormData();
      fd.append("file", blob, "take"); fd.append("seconds", secs.toFixed(1));
      const bars = loop.on ? $("#loop-range").textContent : "";
      fd.append("label", `${semitones ? fmtSemis(semitones) + " · " : ""}${bpm} bpm${bars ? " · " + bars : ""}`);
      try { await api(`/api/scores/${sid}/recordings`, { method: "POST", body: fd }); toast("Recording saved."); }
      catch (e) { toast(e.message); }
      loadRecordings();
      $("#side").hidden = false;
    };
    r.start(1000);
    rec = r;
    native?.keepScreenOn?.(true);
    $("#rec").classList.add("on"); $("#rec").setAttribute("aria-label", "Stop recording");
    toast("Recording. Press ● again to stop.");
  }

  async function loadRecordings() {
    const list = await api(`/api/scores/${sid}/recordings`).catch(() => []);
    $("#recordings").innerHTML = list.length ? list.map((r) => `
      <div class="rec-item">
        <div class="rec-head"><strong>${new Date(r.created).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}</strong>
          <span><a href="/api/recordings/${r.id}?download=1" download>Save</a><button data-delrec="${r.id}">Delete</button></span></div>
        <div class="muted small">${fmtClock(r.seconds)}${r.label ? " · " + esc(r.label) : ""}</div>
        <audio controls preload="none" src="/api/recordings/${r.id}"></audio>
      </div>`).join("") : `<p class="muted">Record yourself with the ● button.</p>`;
  }

  // ------------------------------------------------------ actions --
  async function transpose(delta) {
    const next = Math.max(-12, Math.min(12, semitones + delta));
    if (next === semitones) return;
    const was = playing; if (was) pause();
    semitones = next;
    $("#tr-out").textContent = fmtSemis(semitones);
    updateMeta();
    $("#loading").hidden = false; $("#loading").textContent = "Transposing…";
    try { await loadXml(); } catch (e) { toast(e.message); }
    $("#loading").hidden = true;
    if (was) play();
  }

  function setLoopEnd(i) {
    loop.b = i; if (loop.b < loop.a) [loop.a, loop.b] = [loop.b, loop.a];
    loop.on = true; setLoopBtn();
    drawLoop(); seek(loopStart());
  }

  // Touch: long-press a bar to make it the other end of the loop.
  let pressT = null, pressed = false;
  function pressStart(e) {
    if (e.pointerType !== "touch" || loop.a === null) return;
    pressed = false;
    clearTimeout(pressT);
    const { clientX, clientY } = e;
    pressT = setTimeout(() => {
      const i = measureAt(clientX, clientY);
      if (i === null) return;
      pressed = true;
      navigator.vibrate?.(15);
      setLoopEnd(i);
    }, 480);
  }
  const pressCancel = () => clearTimeout(pressT);

  function clickScore(e) {
    if (!osmd || !measures.length) return;
    if (pressed) { pressed = false; return; }  // the long-press already handled it
    const i = measureAt(e.clientX, e.clientY);
    if (i === null) return;
    if (e.shiftKey && loop.a !== null) { setLoopEnd(i); return; }
    if (loop.on) {
      if (i < loop.a || i > (loop.b ?? loop.a)) { loop.on = false; setLoopBtn(); }
    }
    playIntro = measures[i].start < introEndQ - 1e-6;   // the user chose a bar inside the intro
    loop.a = i; loop.b = null;
    drawLoop();
    seek(measures[i].start);
  }

  function setLoopBtn() { $("#loop-btn").setAttribute("aria-pressed", String(loop.on)); }

  function toggleLoop() {
    if (!loop.on) {
      if (loop.a === null) {
        const cur = steps[stepIndexAt(posQ)]?.measure ?? 0;
        loop.a = cur; loop.b = Math.min(cur + 3, measures.length - 1);
        toast(isTouch ? "Looping 4 bars. Tap a bar, then long-press another to change the range."
                      : "Looping 4 bars. Click a bar, then shift-click another to change the range.");
      }
      loop.on = true;
    } else loop.on = false;
    setLoopBtn(); drawLoop();
    if (loop.on && playing && (qAt(Tone.now()) < loopStart() || qAt(Tone.now()) >= loopEnd())) seek(loopStart());
  }

  async function close() {
    if (!sid) return;
    if (playing) pause();
    if (rec) rec.stop();
    flush();
    clearInterval(tick);
    $("#side").hidden = true; $("#compare").hidden = true; $("#mixer").hidden = true; $("#zoom-sheet").hidden = true;
    $("#zoom-btn").setAttribute("aria-pressed", "false");
    $("#mixer-btn").setAttribute("aria-pressed", "false");
  }

  function updateMeta() {
    const i = meta.info || {};
    $("#score-meta").textContent = [semitones ? `transposed ${fmtSemis(semitones)} semitone${Math.abs(semitones) > 1 ? "s" : ""}` : i.key,
      i.time, i.measures && `${i.measures} bars`].filter(Boolean).join(" · ");
  }

  function fmtSemis(n) { return n > 0 ? `+${n}` : String(n); }

  // ------------------------------------------------------ wiring --
  $("#play").addEventListener("click", () => (playing ? pause() : play()));
  $("#stop").addEventListener("click", () => { pause(loop.on ? loopStart() : 0); showPosition(posQ, true); });
  $("#rec").addEventListener("click", toggleRecord);
  $("#tempo").addEventListener("input", (e) => setTempo(+e.target.value));
  $("#tr-up").addEventListener("click", () => transpose(1));
  $("#tr-down").addEventListener("click", () => transpose(-1));
  $("#loop-btn").addEventListener("click", toggleLoop);
  $("#metro-btn").addEventListener("click", (e) => { metroOn = !metroOn; e.currentTarget.setAttribute("aria-pressed", String(metroOn)); });
  $("#countin-btn").addEventListener("click", (e) => { countIn = !countIn; e.currentTarget.setAttribute("aria-pressed", String(countIn)); });
  $("#osmd").addEventListener("click", clickScore);
  $("#osmd").addEventListener("pointerdown", pressStart);
  ["pointerup", "pointercancel", "pointerleave"].forEach((ev) => $("#osmd").addEventListener(ev, pressCancel));
  $("#osmd").addEventListener("contextmenu", (e) => e.preventDefault());
  $("#paper-scroll").addEventListener("scroll", pressCancel, { passive: true });
  $("#back").addEventListener("click", () => (location.hash = "#/"));
  $("#side-btn").addEventListener("click", () => {
    $("#side").hidden = !$("#side").hidden; $("#compare").hidden = true;
    $("#mixer").hidden = true; $("#mixer-btn").setAttribute("aria-pressed", "false");
    $("#zoom-sheet").hidden = true; $("#zoom-btn").setAttribute("aria-pressed", "false");
  });
  $$(".sp").forEach((b) => b.addEventListener("click", () => setTempo(scoreBpm * +b.dataset.speed)));
  $("#mypart").addEventListener("click", (e) => {
    const b = e.target.closest("[data-part]"); if (!b) return;
    setMix({ my: b.dataset.part === "all" ? null : +b.dataset.part, mineSilent: false });
  });
  $("#others").addEventListener("input", (e) => {
    mix.others = +e.target.value; mix.mineSilent = false;
    $("#others-out").textContent = othersLabel(mix.others);
    saveMix(); syncSound(); renderMixer();
  });
  $("#others").addEventListener("change", () => refreshShown());
  const firstMine = () => mix.my ?? (parts.find((x) => myVoice && voiceKind(x.name) === myVoice) || parts[0])?.index ?? null;
  $("#mix-learn").addEventListener("click", () => setMix({ my: firstMine(), mineSilent: false, others: Math.min(mix.others, 35) || 35 }));
  $("#mix-sing").addEventListener("click", () => setMix({ my: firstMine(), mineSilent: true }));
  $("#mix-full").addEventListener("click", () => setMix({ my: null, mineSilent: false }));
  $("#mine-only").addEventListener("change", (e) => setMix({ mineOnly: e.target.checked }));
  // ---- scan details: raw timings and phone state, to compare scans ----
  function showScanLog(log) {
    $("#scan-log-block").hidden = !log;
    if (!log) return;
    const lines = [];
    const walk = (o, pre) => {
      for (const [k, v] of Object.entries(o)) {
        if (v && typeof v === "object" && !Array.isArray(v)) walk(v, `${pre}${k}.`);
        else if (Array.isArray(v) && v.length && typeof v[0] === "object") v.forEach((x, i) => walk(x, `${pre}${k}[${i + 1}].`));
        else lines.push(`${pre}${k}: ${Array.isArray(v) ? v.join(" ") : v}`);
      }
    };
    walk(log, "");
    $("#scan-log").textContent = lines.join("\n");
  }
  $("#scan-log-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("#scan-log").textContent); toast("Copied"); }
    catch {
      const r = document.createRange(); r.selectNodeContents($("#scan-log"));
      const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); document.execCommand("copy"); toast("Copied");
    }
  });

  // ---- zoom: buttons, typed-in steps and pinch; remembered on this device ----
  function setZoom(z, rerender = true) {
    userZoom = Math.round(Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, z)) * 100) / 100;
    $("#zoom-val").textContent = `${Math.round(userZoom * 100)}%`;
    try { localStorage.setItem("pr.zoom4", String(userZoom)); } catch {}
    if (rerender && osmd) { renderScore(); afterRender(); }
  }
  $("#zoom-btn").addEventListener("click", () => {
    const z = $("#zoom-sheet"); z.hidden = !z.hidden;
    $("#zoom-btn").setAttribute("aria-pressed", String(!z.hidden));
    $("#mixer").hidden = true; $("#mixer-btn").setAttribute("aria-pressed", "false");
  });
  $("#zoom-in").addEventListener("click", () => setZoom(userZoom + 0.1));
  $("#zoom-out").addEventListener("click", () => setZoom(userZoom - 0.1));
  $("#zoom-reset").addEventListener("click", () => setZoom(ZOOM_DEFAULT));

  // pinch: scale the page live with CSS, re-engrave at the new size when the fingers lift
  // Touch events (not pointer events): they keep firing even after the page starts scrolling.
  let pinch = null;
  const paperEl = $("#paper"), scrollEl = $("#paper-scroll");
  const gap = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
  scrollEl.addEventListener("touchstart", (e) => {
    if (e.touches.length === 2) { pinch = { d0: gap(e.touches) || 1, z0: userZoom, scale: 1 }; pressCancel(); }
  }, { passive: true });
  scrollEl.addEventListener("touchmove", (e) => {
    if (!pinch || e.touches.length !== 2) return;
    e.preventDefault();
    pinch.scale = Math.max(ZOOM_MIN / pinch.z0, Math.min(ZOOM_MAX / pinch.z0, gap(e.touches) / pinch.d0));
    paperEl.style.transformOrigin = "50% 0";
    paperEl.style.transform = `scale(${pinch.scale})`;
    $("#zoom-val").textContent = `${Math.round(pinch.z0 * pinch.scale * 100)}%`;
  }, { passive: false });
  const endPinch = (e) => {
    if (!pinch || e.touches.length >= 2) return;
    const z = pinch.z0 * pinch.scale;
    pinch = null;
    paperEl.style.transform = "";
    if (Math.abs(z - userZoom) > 0.02) setZoom(z);
  };
  scrollEl.addEventListener("touchend", endPinch);
  scrollEl.addEventListener("touchcancel", endPinch);

  // ---- typed tempo ----
  const tempoBox = $("#tempo-out");
  const commitTempo = () => {
    const v = parseInt(tempoBox.value, 10);
    if (Number.isFinite(v)) setTempo(v); else tempoBox.value = bpm;
  };
  tempoBox.addEventListener("change", commitTempo);
  tempoBox.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); tempoBox.blur(); } });
  tempoBox.addEventListener("focus", () => tempoBox.select());
  $("#mixer-btn").addEventListener("click", () => {
    $("#zoom-sheet").hidden = true; $("#zoom-btn").setAttribute("aria-pressed", "false");
    $("#side").hidden = true;
    const m = $("#mixer"); m.hidden = !m.hidden;
    $("#mixer-btn").setAttribute("aria-pressed", String(!m.hidden));
  });
  $("#mixer-close").addEventListener("click", () => { $("#mixer").hidden = true; $("#mixer-btn").setAttribute("aria-pressed", "false"); });
  $("#mixer-follow").checked = followMix;
  $("#mixer-follow").addEventListener("change", (e) => {
    followMix = e.target.checked;
    try { localStorage.setItem("pr.followMix", followMix ? "1" : "0"); } catch {}
    refreshShown();
  });
  $("#mixer-hide-all").addEventListener("click", () => {
    const anyHidden = tracks.some((t) => t.hidden);
    setHidden(tracks, !anyHidden);
  });
  $("#mixer-tracks").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]"); if (!b) return;
    const t = tracks[+b.closest("[data-track]").dataset.track];
    if (b.dataset.act === "solo") t.solo = !t.solo;
    if (b.dataset.act === "mute") t.mute = !t.mute;
    saveTrack(t); syncSound(); renderMixer(); refreshShown();
  });
  $("#mixer-tracks").addEventListener("input", (e) => {
    const el = e.target; const row = el.closest("[data-track]"); if (!row) return;
    const t = tracks[+row.dataset.track];
    if (el.dataset.act === "vol") {
      t.db = +el.value; saveTrack(t); syncSound();
      const pos = faderPos(t.db);
      row.querySelector(".fader i").style.height = `calc(${pos} * (100% - 12px))`;
      row.querySelector(".fader b").style.bottom = `calc(${pos} * (100% - 26px) + 6px)`;
      const eff = trackDb(t);
      row.querySelector(".track-db").textContent = !audible(t) ? "silent" : `${eff > 0 ? "+" : ""}${Number.isFinite(eff) ? Math.round(eff) : "−∞"} dB`;
    }
  });
  $("#mixer-tracks").addEventListener("change", (e) => {
    const el = e.target; const row = el.closest("[data-track]"); if (!row) return;
    const t = tracks[+row.dataset.track];
    if (el.dataset.act === "instrument") { t.instrument = el.value; saveTrack(t); syncSound(); renderMixer(); }
    if (el.dataset.act === "show") setHidden([t], !el.checked);
  });
  $("#recordings").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-delrec]"); if (!b) return;
    if (!confirm("Delete this recording?")) return;
    await api(`/api/recordings/${b.dataset.delrec}`, { method: "DELETE" });
    loadRecordings();
  });
  $("#export-btn").addEventListener("click", (e) => { e.stopPropagation(); $("#export-menu").hidden = !$("#export-menu").hidden; });
  document.addEventListener("click", () => ($("#export-menu").hidden = true));
  $("#export-menu").addEventListener("click", async (e) => {
    const fmt = e.target.dataset.fmt; if (!fmt) return;
    const base = (meta.title.replace(/[\\/:*?"<>|]+/g, "").trim() || "score") + (semitones ? ` (${fmtSemis(semitones)})` : "");
    try {
      if (fmt === "pdf") {
        toast("Preparing PDF…", 20000);
        await downloadBlob(await makePdf(), `${base}.pdf`);
        toast(native ? "PDF ready" : `Downloaded ${base}.pdf`);
        return;
      }
      const params = new URLSearchParams({ semitones });
      if (fmt === "midi") params.set("bpm", bpm);
      downloadUrl(`/api/scores/${sid}/export/${fmt}?${params}`, `${base}.${fmt === "midi" ? "mid" : fmt}`);
    } catch (err) { toast(`Export failed: ${err.message}`, 5000); }
  });

  // Engrave the current (transposed) score onto A4 pages and turn the SVGs into a PDF.
  async function makePdf() {
    const holder = Object.assign(document.createElement("div"), { className: "pdf-render" });
    document.body.appendChild(holder);
    try {
      const xml = await api(`/api/scores/${sid}/musicxml?semitones=${semitones}`);
      const pdfOsmd = new opensheetmusicdisplay.OpenSheetMusicDisplay(holder, {
        autoResize: false, backend: "svg", pageFormat: "A4_P",
        drawTitle: true, drawComposer: true, drawPartNames: true,
      });
      await pdfOsmd.load(xml);
      dropDuplicateSubtitle(pdfOsmd);
      pdfOsmd.render();
      const pages = [...holder.querySelectorAll("svg")];
      if (!pages.length) throw new Error("Nothing to print");
      const { jsPDF } = window.jspdf;
      const doc = new jsPDF({ unit: "mm", format: "a4", orientation: "portrait" });
      for (let i = 0; i < pages.length; i++) {
        if (i) doc.addPage("a4", "portrait");
        await doc.svg(pages[i], { x: 0, y: 0, width: 210, height: 297 });
      }
      return doc.output("blob");
    } finally {
      holder.remove();
    }
  }
  $("#compare-btn").addEventListener("click", () => {
    const c = $("#compare");
    if (c.hidden) {
      $("#compare-pages").innerHTML = Array.from({ length: meta.page_count }, (_, i) =>
        `<img src="/api/scores/${sid}/pages/${i + 1}" alt="Page ${i + 1} of the original scan" loading="lazy">`).join("");
      $("#side").hidden = true;
    }
    c.hidden = !c.hidden;
  });
  $("#side-compare").addEventListener("click", () => $("#compare-btn").click());
  $("#compare-close").addEventListener("click", () => { $("#compare").hidden = true; });
  $("#score-title").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); } });
  $("#score-title").addEventListener("blur", async (e) => {
    const t = e.target.textContent.trim();
    if (!t || t === meta.title) { e.target.textContent = meta.title; return; }
    meta = await api(`/api/scores/${sid}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ title: t }) });
    document.title = `${meta.title} · Practice Room`;
  });
  document.addEventListener("keydown", (e) => {
    if ($("#player").hidden || e.target.matches("input:not([type=range]):not([type=checkbox]), [contenteditable=true], textarea, select")) return;
    if (e.code === "Space") { e.preventDefault(); if (!e.repeat) playing ? pause() : play(); }
    else if (e.key === "Home") { pause(loop.on ? loopStart() : 0); }
    else if (e.key === "l" || e.key === "L") toggleLoop();
    else if (e.key === "m" || e.key === "M") $("#metro-btn").click();
    else if (e.key === "ArrowUp" && e.altKey) transpose(1);
    else if (e.key === "ArrowDown" && e.altKey) transpose(-1);
    else if (e.key === "+" || e.key === "=") setTempo(bpm + 5);
    else if (e.key === "-") setTempo(bpm - 5);
  });
  // Space on a focused button would also "click" it on keyup; the shortcut owns Space.
  document.addEventListener("keyup", (e) => {
    if (e.code === "Space" && !$("#player").hidden && e.target.matches("button, input[type=range]")) e.preventDefault();
  });
  // Re-lay out the score whenever its column changes width (window resize,
  // side panel, comparison view, phone rotation).
  let resizeT, lastW = 0, ro = null;
  function watchWidth() {
    if (ro) return;
    ro = new ResizeObserver(([entry]) => {
      const w = Math.round(entry.contentRect.width);
      if (!osmd || !w || Math.abs(w - lastW) < 8) return;
      lastW = w;
      clearTimeout(resizeT);
      resizeT = setTimeout(() => { renderScore(); afterRender(); }, 200);
    });
    ro.observe($("#paper"));
  }
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(true); });

  // Screen rectangle of bar i (0-based), for tests and debugging.
  function barRect(i) {
    const r = measureBox(i); if (!r) return null;
    const p = $("#paper").getBoundingClientRect();
    return { x: p.left + r.x, y: p.top + r.y, w: r.w, h: r.h };
  }
  function scrollToBar(i) {
    const r = barRect(i); if (!r) return;
    const sc = $("#paper-scroll"), sr = sc.getBoundingClientRect();
    sc.scrollTop += r.y - sr.top - 30;
  }

  return { open, close, barRect, scrollToBar, get state() { return { playing, posQ, bpm, semitones, loop, steps: steps.length, measures: measures.length, totalQ, zoom: userZoom, ...xmlStats,
    tracks: tracks.map((t) => ({ name: t.name, count: t.count, mute: t.mute, solo: t.solo, db: t.db, instrument: t.instrument, hidden: t.hidden, shown: shown(t) })), staves: osmd ? osmd.Sheet.Instruments.filter((i) => i.Visible).length : 0,
    my: mix.my, mineSilent: mix.mineSilent, others: mix.others, parts: parts.map((p) => p.name),
    lyricShown: !$("#lyric-strip").hidden, lyricLine: $("#lyric-line").textContent }; } };
})();

async function openPlayer(id) {
  $("#library").hidden = true;
  $("#player").hidden = false;
  clearTimeout(pollTimer);
  try { await Player.open(id); }
  catch (e) { toast(e.message, 5000); location.hash = "#/"; }
}

route();
