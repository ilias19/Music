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
const isTouch = matchMedia("(pointer: coarse)").matches;

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
    const chips = [
      s.source === "scan" ? `<span class="chip src">Scanned</span>` : s.source === "gallery" ? `<span class="chip src">Gallery</span>` : "",
      info.parts ? `<span class="chip">${info.parts} part${info.parts > 1 ? "s" : ""}</span>` : "",
      info.measures ? `<span class="chip">${info.measures} bars</span>` : "",
      info.time ? `<span class="chip">${esc(info.time)}</span>` : "",
      info.fifths != null ? `<span class="chip" title="${esc(info.key || "")}">${info.fifths === 0 ? "no ♯/♭" : Math.abs(info.fifths) + (info.fifths > 0 ? "♯" : "♭")}</span>` : "",
    ].join("");
    let body = `<div class="chips">${chips}</div>`;
    if (busy) {
      const pct = Math.round((s.progress || 0) * 100);
      body = `<div class="muted small">${esc(s.message || "Reading the music…")}</div>
              <div class="progress ${pct < 5 ? "indet" : ""}"><i style="width:${Math.max(pct, 5)}%"></i></div>`;
    } else if (failed) {
      body = `<div class="err">Couldn't read this one. ${esc(s.error || "")}</div>`;
    }
    return `<div class="card ${busy ? "busy" : ""} ${failed ? "failed" : ""}" data-id="${s.id}" tabindex="0" role="button">
      <div class="card-title">${esc(s.title)}</div>
      ${body}
      <div class="card-foot"><span>${s.practice_seconds ? fmtMins(s.practice_seconds) + " practised" : ""}</span>
        <span>${new Date(s.created).toLocaleDateString()}</span></div>
      <button class="del" data-del="${s.id}" aria-label="Delete ${esc(s.title)}" title="Delete">✕</button>
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
if (isTouch) $("#drop-title").textContent = "Add sheet music";
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

  function voiceFor(part) {
    if (!voices.has(part)) {
      const vol = new Tone.Volume(0).connect(master);
      const inst = buffers ? new Tone.Sampler({ urls: buffers, release: 1 }) : makeSynth();
      inst.connect(vol);
      voices.set(part, { inst, vol, sampled: !!buffers });
    }
    return voices.get(part);
  }

  function rebuildVoices() {
    for (const [part, v] of voices) {
      if (v.sampled || !buffers) continue;
      const inst = new Tone.Sampler({ urls: buffers, release: 1 });
      inst.connect(v.vol);
      v.inst.dispose();
      v.inst = inst; v.sampled = true;
      voices.set(part, v);
    }
  }

  function note(part, midi, durSec, time, vel = 0.75) {
    const v = voiceFor(part);
    if (v.vol.mute) return;
    v.inst.triggerAttackRelease(Tone.Frequency(midi, "midi").toFrequency(), Math.max(0.05, durSec), time, vel);
  }

  function click(time, accent) { (accent ? metroAccent : metro).triggerAttackRelease(accent ? "C6" : "G5", 0.03, time); }

  function setPart(part, { mute, db }) {
    const v = voiceFor(part);
    if (mute !== undefined) v.vol.mute = mute;
    if (db !== undefined) v.vol.volume.value = db;
  }

  function silence() {
    if (!master) return;
    const now = Tone.now();
    master.gain.cancelScheduledValues(now);
    master.gain.setValueAtTime(master.gain.value, now);
    master.gain.linearRampToValueAtTime(0, now + 0.04);
    for (const v of voices.values()) v.inst.releaseAll?.(now + 0.04);
    master.gain.setValueAtTime(1, now + 0.3);
  }

  return { init, note, click, setPart, silence, get ready() { return !!master; }, get loading() { return loading; } };
})();

// ================================================================ PLAYER ==
const Player = (() => {
  let osmd = null, sid = null, meta = null;
  let steps = [];          // [{ q, measure, notes:[{part, midi, dur}] , it }]
  let measures = [];       // [{ start, len, beats, beatQ, number }]
  let totalQ = 0;
  let semitones = 0, bpm = 100, scoreBpm = 100;
  let loop = { on: false, a: null, b: null };
  let metroOn = false, countIn = true;
  let partState = [];      // [{ name, mute, db }]

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
    if (sid !== id) { semitones = 0; loop = { on: false, a: null, b: null }; posQ = 0; partState = []; playIntro = false; }
    sid = id;
    $("#loading").hidden = false;
    $("#loading").textContent = "Loading score…";
    meta = await api(`/api/scores/${id}`);
    api(`/api/scores/${id}/opened`, { method: "POST" }).catch(() => {});
    $("#score-title").textContent = meta.title;
    document.title = `${meta.title} · Practice Room`;
    $("#compare-btn").hidden = !meta.page_count;
    updateMeta();
    $("#tr-out").textContent = fmtSemis(semitones);
    await loadXml(true);
    loadRecordings();
    startTick();
  }

  async function loadXml(first = false) {
    const xml = await api(`/api/scores/${sid}/musicxml?semitones=${semitones}`);
    if (!osmd) {
      watchWidth();
      osmd = new opensheetmusicdisplay.OpenSheetMusicDisplay("osmd", {
        autoResize: false, backend: "svg", drawTitle: true, drawComposer: true, drawPartNames: true,
        followCursor: false, cursorsOptions: [{ type: 0, color: "#2f6f6a", alpha: 0.35, follow: false }],
      });
    }
    await osmd.load(xml);
    osmd.zoom = parseFloat($("#zoom").value);
    osmd.render();
    buildTimeline();
    if (first) {
      scoreBpm = Math.round(osmd.Sheet.DefaultStartTempoInBpm || meta.info?.tempo || 100);
      setTempo(scoreBpm);
      buildPartsPanel();
    }
    afterRender();
    $("#loading").hidden = true;
  }

  function afterRender() {
    if (!osmd) return;
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
    let clock = 0, prevTs = null, prevMin = 1;
    while (!it.EndReached) {
      const ts = it.currentTimeStamp.RealValue * 4;
      if (prevTs !== null) clock += ts > prevTs ? ts - prevTs : prevMin;
      prevTs = ts;
      const notes = [];
      let minLen = Infinity;
      for (const ve of it.CurrentVoiceEntries) {
        const part = Math.max(0, insts.indexOf(ve.ParentVoice.Parent));
        for (const n of ve.Notes) {
          const len = n.Length.RealValue * 4;
          if (len > 0) minLen = Math.min(minLen, len);
          if (n.isRest() || n.IsGraceNote || n.halfTone === undefined) continue;
          const tie = n.NoteTie;
          if (tie && tie.StartNote !== n) continue;
          const dur = (tie ? tie.Duration.RealValue : n.Length.RealValue) * 4;
          notes.push({ part, midi: n.halfTone + 12, dur });
        }
      }
      prevMin = isFinite(minLen) ? minLen : 1;
      steps.push({ q: clock, measure: it.CurrentMeasureIndex, notes, it: it.clone() });
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
    cursor.reset();
  }

  function buildPartsPanel() {
    const insts = osmd.Sheet.Instruments;
    if (partState.length !== insts.length) partState = insts.map((inst, i) => ({ name: inst.Name || `Part ${i + 1}`, mute: false, db: 0 }));
    $("#parts").innerHTML = partState.map((p, i) => `
      <div class="part-row">
        <button class="mute" data-part="${i}" aria-pressed="${p.mute}" title="Mute">M</button>
        <span class="name" title="${esc(p.name)}">${esc(p.name)}</span>
        <input type="range" min="-30" max="6" step="1" value="${p.db}" data-vol="${i}" aria-label="Volume ${esc(p.name)}">
      </div>`).join("") || `<p class="muted">No parts.</p>`;
    partState.forEach((p, i) => Sound.ready && Sound.setPart(i, p));
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
    $("#pos").textContent = `Bar ${measures[st.measure]?.number ?? 1}`;
  }

  // ------------------------------------------------------ transport --
  const qAt = (t) => anchorQ + (t - anchorT) * bpm / 60;
  const tAt = (q) => anchorT + (q - anchorQ) * 60 / bpm;
  const loopStart = () => measures[loop.a].start;
  const loopEnd = () => { const m = measures[loop.b ?? loop.a]; return m.start + m.len; };

  async function play() {
    if (!steps.length) return;
    await Sound.init();
    partState.forEach((p, i) => Sound.setPart(i, p));
    if (loop.on && (posQ < loopStart() || posQ >= loopEnd())) posQ = loopStart();
    if (posQ >= totalQ - 1e-6) posQ = 0;
    if (!loop.on && !playIntro && introBars > 0 && posQ < introEndQ - 1e-6) {
      posQ = introEndQ;
      toast(`Skipped ${introBars} empty bar${introBars > 1 ? "s" : ""} at the start (tap bar 1 to hear them)`);
    }
    let start = Tone.now() + 0.08;
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
          for (const n of s.notes) Sound.note(n.part, n.midi, (n.dur * 60) / bpm * 0.97, tAt(s.q));
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
    $("#tempo").value = v; $("#tempo-out").textContent = v;
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
    $("#side").hidden = true; $("#compare").hidden = true;
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
  $("#side-btn").addEventListener("click", () => { $("#side").hidden = !$("#side").hidden; $("#compare").hidden = true; });
  $("#zoom").addEventListener("change", (e) => { osmd.zoom = +e.target.value; osmd.render(); afterRender(); });
  $("#parts").addEventListener("click", (e) => {
    const b = e.target.closest("[data-part]"); if (!b) return;
    const i = +b.dataset.part; partState[i].mute = !partState[i].mute;
    b.setAttribute("aria-pressed", String(partState[i].mute));
    if (Sound.ready) Sound.setPart(i, { mute: partState[i].mute });
  });
  $("#parts").addEventListener("input", (e) => {
    const i = e.target.dataset.vol; if (i === undefined) return;
    partState[i].db = +e.target.value;
    if (Sound.ready) Sound.setPart(+i, { db: partState[i].db });
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
        autoResize: false, backend: "svg", pageFormat: "A4_P", pageBackgroundColor: "#FFFFFF",
        drawTitle: true, drawComposer: true, drawPartNames: true,
      });
      await pdfOsmd.load(xml);
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
  $("#compare-close").addEventListener("click", () => { $("#compare").hidden = true; });
  $("#score-title").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); } });
  $("#score-title").addEventListener("blur", async (e) => {
    const t = e.target.textContent.trim();
    if (!t || t === meta.title) { e.target.textContent = meta.title; return; }
    meta = await api(`/api/scores/${sid}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ title: t }) });
    document.title = `${meta.title} · Practice Room`;
  });
  document.addEventListener("keydown", (e) => {
    if ($("#player").hidden || e.target.matches("input:not([type=range]):not([type=checkbox]), [contenteditable=true], textarea")) return;
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
      resizeT = setTimeout(() => { osmd.render(); afterRender(); }, 200);
    });
    ro.observe($("#paper"));
  }
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(true); });

  return { open, close, get state() { return { playing, posQ, bpm, semitones, loop, steps: steps.length, measures: measures.length, totalQ }; } };
})();

async function openPlayer(id) {
  $("#library").hidden = true;
  $("#player").hidden = false;
  clearTimeout(pollTimer);
  try { await Player.open(id); }
  catch (e) { toast(e.message, 5000); location.hash = "#/"; }
}

route();
