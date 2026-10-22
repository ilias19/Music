"""End-to-end tests for Practice Room.

Desktop (phone-sized Chromium against tools/dev_server.py):
    python tests/e2e/run_e2e.py --url "http://127.0.0.1:8020/__start?k=dev"

Inside the Android app (emulator/phone with the emu build, WebView debugging on):
    python tests/e2e/run_e2e.py --android

Every check runs even if an earlier one failed; the summary lists them all.
Audio is verified by metering the real Web Audio output, not by UI state.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))

ASSETS = Path(__file__).parent / "assets"
OUT = Path(__file__).parent / "out"
PKG = "com.ilias.practiceroom"

results: list[tuple[str, bool, str]] = []
CURRENT = {"section": "start"}


def check(name: str, cond: bool, detail: str = "") -> bool:
    results.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""), flush=True)
    return bool(cond)


def section(fn):
    """Run a group of checks; an exception fails the group but not the run."""
    def run(*a, **k):
        print(f"\n== {fn.__name__}", flush=True)
        CURRENT["section"] = fn.__name__
        try:
            fn(*a, **k)
        except Exception as e:
            traceback.print_exc()
            check(f"{fn.__name__} finished without error", False, f"{type(e).__name__}: {str(e)[:300]}")
            try:
                a[0].page.screenshot(path=str(OUT / f"error_{fn.__name__}.png"))
            except Exception:
                pass
    run.__name__ = fn.__name__
    return run


def adb(*args: str, check_rc: bool = False) -> str:
    r = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=120)
    if check_rc and r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)}: {r.stderr}")
    return r.stdout


class App:
    """Drives the app UI the way a user would, with helpers for measuring sound."""

    def __init__(self, page, android: bool):
        self.page = page
        self.android = android
        self.errors: list[str] = []
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and self._console(m.text))
        page.on("dialog", lambda d: d.accept())

    def _console(self, text: str) -> None:
        if "Failed to load resource" in text or "TUNNEL" in text:
            return
        self.errors.append(f"[{CURRENT['section']}] {text[:300]}")

    def reattach(self):
        """Android: the activity (and its WebView) was rebuilt; connect to the new page."""
        self.page = self._reattach()
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("console", lambda m: m.type == "error" and self._console(m.text))
        self.page.wait_for_selector("#library, #player", state="attached", timeout=120000)
        self.page.wait_for_timeout(800)
        self.page.evaluate("window.confirm = () => true")

    # ---- navigation ----
    def library(self):
        self.page.evaluate("location.hash = '#/'")
        self.page.wait_for_selector(".card", timeout=30000)
        self.page.wait_for_timeout(300)

    def open(self, title: str):
        self.library()
        card = self.page.locator(".card:not(.busy)", has_text=title).first
        card.wait_for(timeout=15000)   # the grid refreshes every 2 s while a scan runs
        card.click()
        self.wait_loaded()

    def wait_loaded(self, timeout=60000):
        self.page.wait_for_function("document.querySelector('#player') && !document.querySelector('#player').hidden "
                                    "&& document.querySelector('#loading').hidden", timeout=timeout)
        self.page.wait_for_timeout(300)

    def state(self) -> dict:
        return self.page.evaluate("Player.state")

    def bar(self) -> int:
        return int(re.sub(r"\D", "", self.page.inner_text("#pos")) or 0)

    def tap_bar(self, n: int, shift: bool = False):
        self.page.evaluate(f"Player.scrollToBar({n - 1})")
        self.page.wait_for_timeout(250)
        r = self.page.evaluate(f"Player.barRect({n - 1})")
        x, y = r["x"] + r["w"] * 0.5, r["y"] + r["h"] * 0.5
        if shift:
            self.page.keyboard.down("Shift"); self.page.mouse.click(x, y); self.page.keyboard.up("Shift")
        else:
            self.page.mouse.click(x, y)
        self.page.wait_for_timeout(250)

    def set_toggle(self, sel: str, on: bool):
        if (self.page.get_attribute(sel, "aria-pressed") == "true") != on:
            self.page.click(sel)

    def set_tempo(self, bpm: int):
        self.page.evaluate(f"""() => {{ const t = document.querySelector('#tempo'); t.value = {bpm};
            t.dispatchEvent(new Event('input', {{bubbles: true}})); }}""")

    # ---- files ----
    def upload(self, *names: str, title: str | None = None):
        """Same code path as the file picker (handleFiles), with real files from assets/."""
        files = []
        for n in names:
            data = (ASSETS / n).read_bytes()
            mime = {"png": "image/png", "jpg": "image/jpeg", "pdf": "application/pdf", "txt": "text/plain",
                    "musicxml": "application/vnd.recordare.musicxml+xml", "mxl": "application/vnd.recordare.musicxml"}[n.rsplit(".", 1)[1]]
            name = (title.replace(" ", "_") + "." + n.rsplit(".", 1)[1]) if title else n   # the app titles scans by file name
            files.append({"name": name, "mime": mime, "b64": base64.b64encode(data).decode()})
        self.page.evaluate("""async (files) => {
            const list = files.map(f => new File([Uint8Array.from(atob(f.b64), c => c.charCodeAt(0))], f.name, { type: f.mime }));
            await handleFiles(list);
        }""", files)

    def wait_card(self, title_part: str, timeout=900) -> dict:
        """Wait until the newest score whose title contains title_part is ready or failed."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            scores = self.page.evaluate("api('/api/scores')")
            match = [s for s in scores if title_part.lower() in s["title"].lower()]
            if match and match[0]["status"] in ("ready", "failed"):
                match[0]["_seconds"] = round(time.time() - t0, 1)
                return match[0]
            time.sleep(1)
        raise TimeoutError(f"score '{title_part}' not finished after {timeout}s")

    # ---- audio ----
    def audio_tap(self):
        """Count notes/clicks per track and meter the real output."""
        self.page.evaluate("""async () => {
            await Sound.init();
            if (!window.__t) {
                window.__t = { notes: [], clicks: [], levels: [] };
                const n = Sound.note, c = Sound.click;
                Sound.note = (track, midi, dur, time, vel) => { window.__t.notes.push({ track, midi, time, at: Tone.now() }); return n(track, midi, dur, time, vel); };
                Sound.click = (time, acc) => { window.__t.clicks.push({ time, at: Tone.now() }); return c(time, acc); };
                window.__meter = new Tone.Meter({ normalRange: true, smoothing: 0 });
                Tone.getDestination().connect(window.__meter);
            }
            window.__t.notes = []; window.__t.clicks = []; window.__t.levels = [];
            clearInterval(window.__poll);
            window.__poll = setInterval(() => window.__t.levels.push(window.__meter.getValue()), 25);
        }""")

    def audio_read(self) -> dict:
        return self.page.evaluate("""() => { clearInterval(window.__poll); const t = window.__t;
            const L = t.levels; return { notes: t.notes, clicks: t.clicks, peak: Math.max(0, ...L),
            mean: L.reduce((a, b) => a + b, 0) / Math.max(1, L.length) }; }""")

    def play_for(self, seconds: float) -> dict:
        self.audio_tap()
        t0 = self.page.evaluate("Tone.now()")
        self.page.click("#play")
        self.page.wait_for_timeout(int(seconds * 1000))
        self.page.click("#play")
        r = self.audio_read()
        r["t0"] = t0
        return r


# =============================================================== sections ==
@section
def library_basics(app: App):
    app.library()
    titles = app.page.locator(".card-title").all_inner_texts()
    check("gallery has the 5 bundled pieces", sum(t in titles for t in [
        "Ode to Joy (Beethoven)", "Chorale BWV 66.6 (Bach)", "Lascia ch'io pianga (Handel)",
        "Der Lindenbaum (Schubert)", "Maple Leaf Rag (Joplin)"]) == 5, str(titles))
    app.page.fill("#search", "bach")
    app.page.wait_for_timeout(200)
    shown = app.page.locator(".card-title").all_inner_texts()
    check("search filters the library", "Chorale BWV 66.6 (Bach)" in shown and all("bach" in t.lower() for t in shown), str(shown))
    app.page.fill("#search", "zzz")
    check("search with no match says so", "No matches" in app.page.inner_text("#grid"))
    app.page.fill("#search", "")
    app.page.screenshot(path=str(OUT / "library.png"))


@section
def import_files(app: App):
    app.library()
    app.upload("ode.musicxml")
    s = app.wait_card("ode", timeout=60)
    check("MusicXML import is ready", s["status"] == "ready" and s["info"]["measures"] == 8, json.dumps(s["info"]))
    app.upload("ode.mxl")
    scores = app.page.evaluate("api('/api/scores')")
    check("compressed .mxl import works", sum(1 for x in scores if x["source"] == "import") >= 2)
    before = len(scores)
    app.upload("notes.txt")
    app.page.wait_for_timeout(800)
    after = len(app.page.evaluate("api('/api/scores')"))
    toast = app.page.inner_text("#toast")
    check("a .txt file is refused with a message and creates nothing", after == before and "Skipped" in toast, toast)


@section
def scanning(app: App, android: bool):
    app.library()
    app.upload("ode_photo.jpg")
    app.page.wait_for_timeout(1500)
    busy = app.page.locator(".card.busy").count()
    check("scanning shows a progress card", busy >= 1)
    s = app.wait_card("ode photo")
    check("photo scan succeeds", s["status"] == "ready", f"{s['_seconds']} s, {s.get('error')}")
    check("photo scan: 8 bars, 4/4", s["info"] and s["info"]["measures"] == 8 and s["info"]["time"] == "4/4", json.dumps(s["info"]))
    xml = app.page.evaluate(f"api('/api/scores/{s['id']}/musicxml')")
    steps = re.findall(r"<step>([A-G])</step>", xml)
    check("photo scan: notes match Ode to Joy", "".join(steps[:15]) == "EEFGGFEDCCDEEDD", "".join(steps[:15]))

    app.library()
    app.upload("page1.png", "page2.png")
    s = app.wait_card("page1")
    check("two images become one score", s["status"] == "ready" and (s["info"] or {}).get("measures") == 8,
          f"{s['info']} in {s['_seconds']} s")
    full = app.page.evaluate(f"api('/api/scores/{s['id']}')")
    check("multi-page score keeps both page images", full["page_count"] == 2, str(full["page_count"]))

    app.library()
    app.upload("bach_chorale.pdf")
    s = app.wait_card("bach chorale")
    check("PDF scan succeeds with 4 parts", s["status"] == "ready" and s["info"]["parts"] == 4, f"{s['info']} in {s['_seconds']} s")

    app.library()
    app.upload("no_music.jpg")
    s = app.wait_card("no music")
    check("an image without music fails with a readable message", s["status"] == "failed" and bool(s["error"]), s.get("error", "")[:120])
    app.library()
    try:
        app.page.wait_for_selector(".card.failed", timeout=15000)
    except Exception:
        pass
    card = app.page.locator(".card.failed").first
    check("failed card is shown", card.count() == 1)
    card.locator(".del").click()
    app.page.wait_for_timeout(600)
    check("failed card can be deleted", app.page.locator(".card.failed").count() == 0)


@section
def lead_sheet_scan(app: App):
    app.library()
    app.upload("lead_sheet.jpg")
    s = app.wait_card("lead sheet")
    check("real lead-sheet screenshot scans", s["status"] == "ready" and s["info"]["measures"] == 80, f"{s['info']} in {s['_seconds']} s")
    app.open("lead sheet")
    st = app.state()
    check("lead sheet keeps its lyrics", st["lyrics"] >= 100, f"{st['lyrics']} syllables")
    check("lead sheet keeps its chord symbols", st["chords"] >= 2, f"{st['chords']} chords")
    check("lead sheet tempo is read from the page (150)", st["bpm"] == 150, f"{st['bpm']} bpm")
    n = app.page.locator("#osmd text", has_text=re.compile(r"^\w{2,}$")).count()
    check("lyrics are drawn under the notes", n >= 50, f"{n} words drawn")


@section
def playback(app: App):
    app.open("Ode to Joy (Beethoven)")
    app.set_toggle("#countin-btn", False)
    app.set_toggle("#metro-btn", False)
    app.set_tempo(100)
    r = app.play_for(2.0)
    check("play makes sound", r["peak"] > 0.01, f"peak {r['peak']:.3f}")
    first = r["notes"][0] if r["notes"] else None
    check("first note is E4 at bar 1", first and first["midi"] == 64, str(first))
    st = app.state()
    check("pause keeps the position", 3.0 <= st["posQ"] <= 4.0, f"posQ {st['posQ']:.2f} after 2 s at 100 bpm")

    # tempo: 200 bpm from bar 1 for 4.8 s = 16 beats -> bar 5
    app.page.click("#stop")
    app.set_tempo(200)
    app.play_for(4.8)
    b = app.bar()
    check("tempo 200 plays twice as fast", b in (4, 5, 6), f"bar {b} after 4.8 s")

    # tempo change while playing: 1.2 s at 100 (2 beats) + 2.4 s at 200 (8 beats) = beat 10 -> bar 3
    app.page.click("#stop")
    app.set_tempo(100)
    app.audio_tap()
    app.page.click("#play")
    app.page.wait_for_timeout(1200)
    app.set_tempo(200)
    app.page.wait_for_timeout(2400)
    app.page.click("#play")
    st = app.state()
    check("tempo changes take effect while playing", 9.0 <= st["posQ"] <= 11.5, f"posQ {st['posQ']:.2f}")
    app.set_tempo(100)

    # count-in: first note one bar (4 beats = 2.4 s) after pressing play
    app.page.click("#stop")
    app.set_toggle("#countin-btn", True)
    r = app.play_for(3.2)
    if r["notes"]:
        delay = r["notes"][0]["time"] - r["t0"]
        check("count-in gives one bar before the music", 2.2 <= delay <= 2.8, f"first note {delay:.2f} s after play")
        check("count-in clicks are heard", len(r["clicks"]) >= 4, f"{len(r['clicks'])} clicks")
    else:
        check("count-in: music starts", False, "no notes")
    app.set_toggle("#countin-btn", False)

    # metronome
    app.page.click("#stop")
    app.set_toggle("#metro-btn", True)
    r = app.play_for(2.5)
    check("metronome clicks on every beat", len(r["clicks"]) >= 4, f"{len(r['clicks'])} clicks in 2.5 s")
    app.set_toggle("#metro-btn", False)
    app.page.click("#stop")
    r = app.play_for(1.5)
    check("metronome off: no clicks", len(r["clicks"]) == 0, f"{len(r['clicks'])}")

    # end of piece stops and rewinds
    app.page.click("#stop")
    app.set_tempo(220)
    app.tap_bar(8)
    app.page.click("#play")
    app.page.wait_for_timeout(2600)
    st = app.state()
    check("playback stops at the end", not st["playing"], json.dumps({k: st[k] for k in ("playing", "posQ")}))
    app.set_tempo(100)


@section
def transpose(app: App):
    app.open("Ode to Joy (Beethoven)")
    app.set_toggle("#countin-btn", False)
    app.page.click("#tr-up"); app.page.wait_for_timeout(200); app.wait_loaded()
    app.page.click("#tr-up"); app.page.wait_for_timeout(200); app.wait_loaded()
    check("transpose display shows +2", app.page.inner_text("#tr-out") == "+2")
    meta = app.page.inner_text("#score-meta")
    check("header says it is transposed", "transposed +2" in meta, meta)
    sid = app.page.evaluate("location.hash.split('/').pop()")
    xml = app.page.evaluate(f"api('/api/scores/{sid}/musicxml?semitones=2')")
    check("score is rewritten in D major", "<fifths>2</fifths>" in xml)
    app.page.click("#stop")
    r = app.play_for(1.0)
    check("transposed playback starts on F#4", r["notes"] and r["notes"][0]["midi"] == 66, str(r["notes"][:1]))
    for _ in range(14):
        app.page.click("#tr-down"); app.page.wait_for_timeout(120)
    app.wait_loaded()
    check("transpose stops at -12", app.page.inner_text("#tr-out") == "-12")
    for _ in range(12):
        app.page.click("#tr-up"); app.page.wait_for_timeout(120)
    app.wait_loaded()
    check("back to original key", app.page.inner_text("#tr-out") == "0")


@section
def loops(app: App):
    app.open("Ode to Joy (Beethoven)")
    app.set_toggle("#countin-btn", False)
    app.set_tempo(200)
    app.tap_bar(3)
    check("tapping a bar moves there", app.bar() == 3, f"bar {app.bar()}")
    app.tap_bar(4, shift=True)
    st = app.state()
    check("shift-tap sets a loop 3-4", st["loop"]["on"] and st["loop"]["a"] == 2 and st["loop"]["b"] == 3, json.dumps(st["loop"]))
    check("loop button shows the range", "3–4" in app.page.inner_text("#loop-range"), app.page.inner_text("#loop-range"))
    check("loop range is highlighted", app.page.locator(".bar-hl").count() >= 2)
    app.audio_tap()
    app.page.click("#play")
    seen = []
    for _ in range(26):
        app.page.wait_for_timeout(250)
        seen.append(app.bar())
    app.page.click("#play")
    r = app.audio_read()
    wraps = sum(1 for a, b in zip(seen, seen[1:]) if b < a)
    check("loop plays only bars 3-4 and repeats", set(seen) <= {3, 4} and wraps >= 2, f"bars {seen}")
    notes = sorted({n["midi"] for n in r["notes"]})
    check("loop notes are those of bars 3-4", set(notes) <= {60, 62, 64}, str(notes))
    app.page.click("#loop-btn")
    check("loop button turns the loop off", not app.state()["loop"]["on"])
    app.page.click("#loop-btn")
    check("loop button turns it back on", app.state()["loop"]["on"])
    app.tap_bar(7)
    check("tapping outside the loop cancels it", not app.state()["loop"]["on"])
    app.set_tempo(100)


@section
def intro_skip(app: App):
    app.open("lead sheet")
    app.set_toggle("#countin-btn", False)
    app.page.click("#stop")
    r = app.play_for(1.0)
    check("play skips the 8 silent intro bars", app.bar() == 9, f"bar {app.bar()}")
    check("sound starts immediately", r["peak"] > 0.01, f"peak {r['peak']:.3f}")
    app.tap_bar(1)
    app.play_for(0.5)
    check("tapping bar 1 plays the intro", app.bar() == 1, f"bar {app.bar()}")


@section
def mixer(app: App):
    app.open("lead sheet")
    app.set_toggle("#countin-btn", False)
    tr = app.state()["tracks"]
    check("lead sheet has Melody and Harmony tracks", [t["name"] for t in tr] == ["Melody", "Harmony"], str(tr))
    app.page.click("#mixer-btn")
    check("mixer opens", app.page.is_visible("#mixer"))

    def run(label):
        app.page.click("#mixer-close")
        app.tap_bar(49)
        r = app.play_for(2.5)
        return r

    def btn(track, act):
        if not app.page.is_visible("#mixer"):
            app.page.click("#mixer-btn")
        app.page.locator(".track", has_text=track).locator(f"[data-act={act}]").click()

    base = run("all")
    btn("Melody", "mute"); btn("Harmony", "mute")
    silent = run("both muted")
    check("muting every track is silent", silent["peak"] < 0.002 and base["peak"] > 0.01, f"{base['peak']:.3f} -> {silent['peak']:.3f}")
    btn("Harmony", "solo")
    solo = run("harmony solo")
    check("solo plays a muted track", solo["peak"] > 0.01, f"{solo['peak']:.3f}")
    btn("Harmony", "solo"); btn("Melody", "mute"); btn("Harmony", "mute")
    if not app.page.is_visible("#mixer"):
        app.page.click("#mixer-btn")
    app.page.locator(".track", has_text="Melody").locator("input[type=checkbox]").uncheck()
    app.page.wait_for_timeout(600)
    vis = app.page.evaluate("[...document.querySelectorAll('#osmd .vf-beam')].filter(e => e.style.display !== 'none').length")
    check("hiding Melody hides its notes and beams", vis == 0 and app.state()["tracks"][0]["hidden"], f"{vis} beams visible")
    app.page.click("#mixer-hide-all")
    app.page.wait_for_timeout(600)
    check("Show all notes brings them back", not any(t["hidden"] for t in app.state()["tracks"]))
    app.page.locator(".track", has_text="Harmony").locator("select").select_option("organ")
    r = run("organ")
    check("instrument change still plays", r["peak"] > 0.01, f"{r['peak']:.3f}")
    app.page.click("#mixer-btn") if not app.page.is_visible("#mixer") else None
    app.page.locator(".track", has_text="Harmony").locator("select").select_option("piano")
    app.page.click("#mixer-close")


@section
def display(app: App):
    app.open("Chorale BWV 66.6 (Bach)")
    app.page.click("#side-btn")
    check("side panel opens", app.page.is_visible("#side"))
    app.page.click("#side-btn")
    w0 = app.page.evaluate("document.querySelector('#osmd svg').getBoundingClientRect().height")
    app.page.click("#zoom-btn")
    check("zoom control opens", app.page.is_visible("#zoom-sheet") and app.page.inner_text("#zoom-val") == "100%", app.page.inner_text("#zoom-val"))
    for _ in range(4):
        app.page.click("#zoom-in"); app.page.wait_for_timeout(300)
    app.page.wait_for_timeout(500)
    w1 = app.page.evaluate("document.querySelector('#osmd svg').getBoundingClientRect().height")
    check("zoom + makes the score bigger", w1 > w0 * 1.15 and app.page.inner_text("#zoom-val") == "140%", f"{w0:.0f} -> {w1:.0f} px, {app.page.inner_text('#zoom-val')}")
    sw = app.page.evaluate("document.querySelector('#paper-scroll').scrollWidth - document.querySelector('#paper-scroll').clientWidth")
    check("zoomed score doesn't scroll sideways", sw <= 2, f"{sw} px")
    for _ in range(7):
        app.page.click("#zoom-out"); app.page.wait_for_timeout(250)
    app.page.wait_for_timeout(500)
    w2 = app.page.evaluate("document.querySelector('#osmd svg').getBoundingClientRect().height")
    check("zoom − makes it smaller", w2 < w0 * 0.9 and app.page.inner_text("#zoom-val") == "70%", f"{w0:.0f} -> {w2:.0f} px")
    app.open("Ode to Joy (Beethoven)")
    check("zoom is remembered for other scores", abs(app.state()["zoom"] - 0.7) < 0.01, str(app.state()["zoom"]))
    check("zoom % shows the remembered value", app.page.inner_text("#zoom-val") == "70%", app.page.inner_text("#zoom-val"))
    app.page.click("#zoom-btn")
    pinched = app.page.evaluate("""async () => {
        const el = document.querySelector('#paper-scroll'), r = el.getBoundingClientRect();
        const cx = r.left + r.width / 2, cy = r.top + 200;
        const mk = (id, x) => new Touch({ identifier: id, target: el, clientX: x, clientY: cy });
        const fire = (type, list) => el.dispatchEvent(new TouchEvent(type, { touches: list, changedTouches: list, bubbles: true, cancelable: true }));
        let a = mk(1, cx - 50), b = mk(2, cx + 50);
        fire('touchstart', [a, b]);
        for (let k = 1; k <= 5; k++) { a = mk(1, cx - 50 - 10 * k); b = mk(2, cx + 50 + 10 * k); fire('touchmove', [a, b]); }
        fire('touchend', []);
        await new Promise((r) => setTimeout(r, 800));
        return Player.state.zoom; }""")
    check("pinching out zooms in", 1.3 <= pinched <= 1.45, f"0.7 -> {pinched}")
    app.page.click("#zoom-reset")
    app.page.wait_for_timeout(500)
    check("Fit resets zoom to 100%", app.page.inner_text("#zoom-val") == "100%" and app.state()["zoom"] == 1)
    app.page.click("#zoom-btn")
    check("zoom control closes", not app.page.is_visible("#zoom-sheet"))

    # typed tempo
    app.page.click("#tempo-out")
    app.page.fill("#tempo-out", "137")
    app.page.keyboard.press("Enter")
    app.page.wait_for_timeout(200)
    check("typed tempo is applied", app.state()["bpm"] == 137 and app.page.input_value("#tempo") == "137", str(app.state()["bpm"]))
    app.page.fill("#tempo-out", "999"); app.page.keyboard.press("Enter"); app.page.wait_for_timeout(200)
    check("typed tempo is clamped", app.state()["bpm"] == 220 and app.page.input_value("#tempo-out") == "220", str(app.state()["bpm"]))
    app.page.fill("#tempo-out", ""); app.page.keyboard.press("Enter"); app.page.wait_for_timeout(200)
    check("empty tempo keeps the old value", app.page.input_value("#tempo-out") == "220")
    app.page.click("#tempo-out"); app.page.keyboard.press("Space"); app.page.wait_for_timeout(300)
    check("typing in tempo box doesn't start playback", not app.state()["playing"])
    app.set_tempo(100)
    app.open("Chorale BWV 66.6 (Bach)")
    app.page.click("#side-btn")
    app.page.click("#side-btn")
    title = app.page.locator("#osmd text", has_text="Chorale").count()
    check("score title is drawn once", title == 1, f"{title}")


@section
def compare_original(app: App):
    app.open("ode photo")
    if app.page.is_visible("#compare-btn"):
        app.page.click("#compare-btn")
        check("'Original scan' reachable from the header", True)
    else:
        app.page.click("#side-btn")
        check("'Original scan' reachable from the side panel on phones", app.page.is_visible("#side-compare"))
        app.page.click("#side-compare")
    app.page.wait_for_timeout(1500)
    ok = app.page.evaluate("[...document.querySelectorAll('#compare-pages img')].every(i => i.complete && i.naturalWidth > 0)")
    n = app.page.locator("#compare-pages img").count()
    check("original page images load", n == 1 and ok, f"{n} images")
    app.page.click("#compare-close")


@section
def rename_and_delete(app: App):
    app.open("ode photo")
    app.page.click("#score-title")
    app.page.keyboard.press("Control+A")
    app.page.keyboard.type("My renamed score")
    app.page.keyboard.press("Enter")
    app.page.wait_for_timeout(600)
    app.library()
    check("rename is saved", app.page.locator(".card", has_text="My renamed score").count() == 1)
    n = app.page.locator(".card").count()
    app.page.locator(".card", has_text="My renamed score").locator(".del").click()
    app.page.wait_for_timeout(700)
    check("delete removes the score", app.page.locator(".card").count() == n - 1)


@section
def recording(app: App):
    app.open("Ode to Joy (Beethoven)")
    app.page.click("#rec")
    app.page.wait_for_timeout(3000)
    recording_on = "on" in (app.page.get_attribute("#rec", "class") or "")
    app.page.click("#rec")
    app.page.wait_for_timeout(2500)
    items = app.page.locator(".rec-item")
    check("recording starts", recording_on)
    check("recording is saved", items.count() >= 1, f"{items.count()} takes")
    if items.count():
        dur = app.page.evaluate("""async () => { const a = document.querySelector('.rec-item audio');
            a.preload = 'auto'; a.load(); await new Promise(r => { a.onloadedmetadata = r; a.onerror = r; setTimeout(r, 5000); });
            return a.duration; }""")
        check("saved take plays back (has a duration)", dur and (dur > 1 or dur == float("inf")), f"{dur}")
        label = items.first.inner_text()
        check("take is labelled with tempo", "bpm" in label, label.replace("\n", " | "))
        items.first.locator("[data-delrec]").click()
        app.page.wait_for_timeout(800)
        check("take can be deleted", app.page.locator(".rec-item").count() == 0)


@section
def exports(app: App, ctx_downloads: bool):
    app.open("Chorale BWV 66.6 (Bach)")
    app.page.click("#tr-up"); app.wait_loaded()
    got = {}
    for fmt in ("pdf", "midi", "musicxml"):
        app.page.click("#export-btn")
        if ctx_downloads:
            with app.page.expect_download(timeout=90000) as dl:
                app.page.click(f"#export-menu [data-fmt={fmt}]")
            d = dl.value
            path = OUT / d.suggested_filename
            d.save_as(path)
            got[fmt] = (d.suggested_filename, path.read_bytes()[:8])
        else:
            app.page.click(f"#export-menu [data-fmt={fmt}]")
            app.page.wait_for_timeout(9000 if fmt == "pdf" else 3000)
    if ctx_downloads:
        check("PDF export", got["pdf"][1].startswith(b"%PDF"), got["pdf"][0])
        check("MIDI export", got["midi"][1].startswith(b"MThd"), got["midi"][0])
        check("MusicXML export", got["musicxml"][1].lstrip().startswith(b"<?xml"), got["musicxml"][0])
        check("export file names keep the title", "Chorale BWV 66.6 (Bach) (+1)" in got["pdf"][0], got["pdf"][0])
    app.page.click("#tr-down"); app.wait_loaded()


@section
def practice_stats(app: App):
    app.library()
    today = app.page.inner_text("#stat-today")
    check("practice time is recorded", today not in ("0m", ""), today)
    cards = app.page.evaluate("api('/api/scores')")
    check("score cards show time practised", any(c["practice_seconds"] > 0 for c in cards))


@section
def persistence(app: App, reload):
    n = len(app.page.evaluate("api('/api/scores')"))
    reload()
    app.library()
    n2 = len(app.page.evaluate("api('/api/scores')"))
    check("scores survive a restart", n2 == n, f"{n} -> {n2}")


@section
def big_score(app: App):
    t0 = time.time()
    app.open("Maple Leaf Rag (Joplin)")
    secs = time.time() - t0
    st = app.state()
    check("85-bar piano score opens", st["measures"] == 85, f"{secs:.1f} s")
    check("big score opens in reasonable time", secs < 25, f"{secs:.1f} s")
    app.set_toggle("#countin-btn", False)
    r = app.play_for(2.0)
    check("big score plays", r["peak"] > 0.01, f"{len(r['notes'])} notes in 2 s")


@section
def navigation_edge_cases(app: App):
    app.open("Ode to Joy (Beethoven)")
    app.set_toggle("#countin-btn", False)
    app.audio_tap()
    app.page.click("#play")
    app.page.wait_for_timeout(1000)
    app.page.click("#back")
    app.page.wait_for_timeout(800)
    r = app.audio_read()
    late = app.page.evaluate("""() => { window.__t.levels = []; return new Promise(res => {
        const iv = setInterval(() => window.__t.levels.push(window.__meter.getValue()), 25);
        setTimeout(() => { clearInterval(iv); res(Math.max(0, ...window.__t.levels)); }, 1500); }); }""")
    check("leaving a score while playing stops the sound", late < 0.002, f"level after leaving {late:.3f}")

    app.open("Ode to Joy (Beethoven)")
    app.set_toggle("#countin-btn", False)
    app.audio_tap()
    app.page.click("#play")
    app.page.wait_for_timeout(800)
    app.tap_bar(6)
    app.page.wait_for_timeout(1500)
    playing = app.state()["playing"]
    b = app.bar()
    app.page.click("#play")
    r = app.audio_read()
    check("tapping a bar while playing jumps there and keeps playing", playing and b in (6, 7), f"bar {b}, playing {playing}")

    # rapid transpose while playing must not break playback
    app.page.click("#stop")
    app.page.click("#play")
    for _ in range(4):
        app.page.click("#tr-up")
    app.page.wait_for_timeout(2500)
    app.wait_loaded()
    st = app.state()
    check("rapid transposing while playing settles on +4 and keeps playing", st["semitones"] == 4 and st["playing"],
          json.dumps({k: st[k] for k in ("semitones", "playing")}))
    app.page.click("#play")
    for _ in range(4):
        app.page.click("#tr-down")
    app.wait_loaded()

    # another score starts fresh
    app.set_tempo(150)
    app.open("Chorale BWV 66.6 (Bach)")
    st = app.state()
    check("opening another score resets tempo, key and loop",
          st["bpm"] == 96 and st["semitones"] == 0 and not st["loop"]["on"], json.dumps({k: st[k] for k in ("bpm", "semitones")}))
    check("its mixer shows its own tracks", [t["name"] for t in st["tracks"]] == ["Soprano", "Alto", "Tenor", "Bass"])


# ------------------------------------------------------------ Android only --
@section
def android_specific(app: App):
    # back button: player -> library
    app.open("Ode to Joy (Beethoven)")
    adb("shell", "input", "keyevent", "4")
    app.page.wait_for_timeout(1200)
    check("Back from a score returns to the library", app.page.evaluate("!document.querySelector('#library').hidden"))

    # downloads land in Downloads/Practice Room
    listing = adb("shell", "ls '/sdcard/Download/Practice Room/'")
    check("exports are saved in Downloads/Practice Room", "Chorale" in listing, listing.strip().replace("\n", " | ")[:200])

    # file picker opens the system picker and coming back doesn't break it
    app.library()
    app.page.click("label:has(#file-input)")
    time.sleep(3)
    top = adb("shell", "dumpsys", "activity", "activities")
    opened = "documentsui" in top.lower() or "DocumentsActivity" in top
    check("Choose files opens Android's file picker", opened)
    adb("shell", "input", "keyevent", "4")
    time.sleep(2)
    app.page.click("label:has(#file-input)")
    time.sleep(3)
    top = adb("shell", "dumpsys", "activity", "activities")
    check("file picker opens again after cancelling", "documentsui" in top.lower() or "DocumentsActivity" in top)
    adb("shell", "input", "keyevent", "4")
    time.sleep(2)

    # a scan keeps going in the background, with a progress notification, and says when it's ready
    app.library()
    app.upload("ode_photo.jpg", title="background scan")
    time.sleep(1.5)
    adb("shell", "input", "keyevent", "3")   # Home: the app goes to the background mid-scan
    time.sleep(2)
    notes = adb("shell", "dumpsys", "notification", "--noredact")
    check("scanning shows a notification", "Scanning" in notes and "background scan" in notes,
          " | ".join(l.strip() for l in notes.splitlines() if "android.title" in l or "android.text" in l)[:300])
    t0 = time.time()
    ready = False
    while time.time() - t0 < 300 and not ready:
        time.sleep(5)
        ready = "Ready to play" in adb("shell", "dumpsys", "notification", "--noredact")
    check("scan finishes while the app is in the background", ready, f"{time.time() - t0:.0f} s")
    adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")
    time.sleep(3)
    app.reattach()
    s = app.wait_card("background scan", timeout=60)
    fg = next((x for x in app.page.evaluate("api('/api/scores')") if x["title"] == "lead sheet"), None)
    fg_s = (fg or {}).get("info", {}).get("scan_seconds")

    # the same lead sheet again, this time with the app in the background the whole time
    app.upload("lead_sheet.jpg", title="lead bg")
    time.sleep(1)
    adb("shell", "input", "keyevent", "3")
    t0, bg_s = time.time(), None
    while time.time() - t0 < 400 and bg_s is None:
        time.sleep(5)
        m = re.search(r"lead bg.{0,400}?read in (?:(\d+) min )?(\d+) s", adb("shell", "dumpsys", "notification", "--noredact"), re.S)
        if m:
            bg_s = int(m.group(1) or 0) * 60 + int(m.group(2))
    check("background scan is about as fast as in the app", bg_s is not None and fg_s and bg_s <= fg_s * 1.5 + 5,
          f"in the app {fg_s} s, in the background {bg_s} s")
    adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")
    time.sleep(3)
    app.reattach()
    check("the background scan is ready in the library", s["status"] == "ready" and s["info"]["measures"] == 8, str(s.get("info")))
    adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", f"practiceroom://open/#/score/{s['id']}",
        "-n", f"{PKG}/.MainActivity")
    time.sleep(3)
    app.reattach()
    app.wait_loaded()
    check("tapping the notification opens the score", app.page.evaluate("location.hash") == f"#/score/{s['id']}",
          app.page.evaluate("location.hash"))

    # rotation keeps the app (no restart) and the player
    app.open("Chorale BWV 66.6 (Bach)")
    adb("shell", "settings", "put", "system", "accelerometer_rotation", "0")
    adb("shell", "settings", "put", "system", "user_rotation", "1")
    time.sleep(3)
    still = app.page.evaluate("!document.querySelector('#player').hidden && !!document.querySelector('#osmd svg')")
    app.page.screenshot(path=str(OUT / "landscape.png"))
    adb("shell", "settings", "put", "system", "user_rotation", "0")
    time.sleep(2)
    check("rotating keeps the open score", still)

    # dark mode: the app rebuilds its screen and comes back to the open score
    app.open("Ode to Joy (Beethoven)")
    route = app.page.evaluate("location.hash")
    adb("shell", "cmd", "uimode", "night", "yes")
    time.sleep(6)
    app.reattach()
    app.wait_loaded()
    bg = app.page.evaluate("getComputedStyle(document.body).backgroundColor")
    back_route = app.page.evaluate("location.hash")
    app.page.screenshot(path=str(OUT / "dark_player.png"))
    app.library()
    app.page.screenshot(path=str(OUT / "dark_library.png"))
    check("dark mode follows the phone setting", bg == "rgb(20, 23, 31)", bg)
    check("after the theme change the same score is still open", back_route == route, f"{route} -> {back_route}")
    adb("shell", "cmd", "uimode", "night", "no")
    time.sleep(6)
    app.reattach()
    bg = app.page.evaluate("getComputedStyle(document.body).backgroundColor")
    check("switching back to light works", bg == "rgb(241, 236, 227)", bg)

    # the exported PDF on the phone is a real PDF
    pdf = subprocess.run(["adb", "exec-out", "cat", "/sdcard/Download/Practice Room/Chorale BWV 66.6 (Bach) (+1).pdf"],
                         capture_output=True, timeout=60).stdout
    (OUT / "exported_on_phone.pdf").write_bytes(pdf)
    pages = pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages")
    check("PDF saved on the phone is valid", pdf.startswith(b"%PDF") and len(pdf) > 10000 and pages >= 1,
          f"{len(pdf)} bytes, {pages} page(s)")


# ==================================================================== main ==
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="desktop: URL that opens the app (with the start token)")
    ap.add_argument("--android", action="store_true", help="drive the app in a connected emulator/phone")
    ap.add_argument("--skip-slow", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    with sync_playwright() as p:
        if a.android:
            adb("shell", "pm", "grant", PKG, "android.permission.RECORD_AUDIO")
            adb("shell", "pm", "grant", PKG, "android.permission.POST_NOTIFICATIONS")
            adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")

            def connect():
                import urllib.request
                last_err = ""
                for attempt in range(40):
                    socks = re.findall(r"@(webview_devtools_remote_\d+)", adb("shell", "cat", "/proc/net/unix"))
                    pid = adb("shell", "pidof", PKG).strip().split()
                    sock = next((x for x in socks if pid and x.endswith("_" + pid[0])), socks[0] if socks else None)
                    if attempt % 5 == 0:
                        print(f"   attach: pid={pid} sockets={sorted(set(socks))}", flush=True)
                    if not sock:
                        time.sleep(2); continue
                    adb("forward", "--remove-all")
                    adb("forward", "tcp:9222", f"localabstract:{sock}")
                    try:
                        version = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=5).read())
                        targets = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/list", timeout=5).read())
                        if attempt % 5 == 0:
                            print(f"   attach: version={version} targets={[t.get('url') for t in targets]}", flush=True)
                    except Exception as e:
                        last_err = f"devtools http: {e}"; time.sleep(2); continue
                    import cdp_relay  # WebView rejects a call Playwright makes while attaching
                    relay = getattr(connect, "relay", None) or cdp_relay.start(version["webSocketDebuggerUrl"], 9223)
                    connect.relay = relay
                    for endpoint in (relay,):
                        if not endpoint:
                            continue
                        try:
                            br = p.chromium.connect_over_cdp(endpoint, timeout=20000)
                            pages = [pg for c in br.contexts for pg in c.pages if "127.0.0.1" in pg.url]
                            if pages:
                                print(f"   attached via {endpoint} to {pages[0].url}", flush=True)
                                return br, pages[0]
                            last_err = f"connected via {endpoint} but no app page: {[pg.url for c in br.contexts for pg in c.pages]}"
                        except Exception as e:
                            last_err = f"{endpoint}: {type(e).__name__}: {str(e)[:400]}"
                    print(f"   attach attempt {attempt}: {last_err}", flush=True)
                    time.sleep(3)
                raise RuntimeError(f"couldn't attach to the app's WebView: {last_err}")

            browser, page = connect()
            page.wait_for_selector(".card", timeout=120000)
            app = App(page, android=True)

            def _reattach():
                nonlocal browser
                for _ in range(20):
                    try:
                        browser, pg = connect()
                        pg.evaluate("1")
                        return pg
                    except Exception:
                        time.sleep(2)
                raise RuntimeError("couldn't reattach")
            app._reattach = _reattach

            def restart():
                nonlocal browser
                adb("shell", "am", "force-stop", PKG)
                time.sleep(2)
                adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")
                browser, page2 = connect()
                page2.wait_for_selector(".card", timeout=120000)
                app.page = page2
                page2.on("dialog", lambda d: d.accept())
        else:
            browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required",
                                              "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
            ctx = browser.new_context(viewport={"width": 412, "height": 860}, accept_downloads=True,
                                      permissions=["microphone"])
            page = ctx.new_page()
            page.goto(a.url)
            app = App(page, android=False)

            def restart():
                page.reload()

        # Android WebView dialogs are native: let confirm() pass in tests
        app.page.evaluate("window.confirm = () => true")

        library_basics(app)
        import_files(app)
        scanning(app, a.android)
        if not a.skip_slow:
            lead_sheet_scan(app)
        playback(app)
        transpose(app)
        loops(app)
        if not a.skip_slow:
            intro_skip(app)
            mixer(app)
        display(app)
        compare_original(app)
        recording(app)
        exports(app, ctx_downloads=not a.android)
        big_score(app)
        navigation_edge_cases(app)
        practice_stats(app)
        rename_and_delete(app)
        persistence(app, restart)
        if a.android:
            app.page.evaluate("window.confirm = () => true")
            android_specific(app)

        check("no JavaScript errors during the run", not app.errors, "; ".join(app.errors[:5]))
        app.page.screenshot(path=str(OUT / "final.png"))

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    for name, _, detail in failed:
        print(f"  FAILED: {name}  {detail}")
    (OUT / "results.json").write_text(json.dumps([{"check": n, "ok": ok, "detail": d} for n, ok, d in results], indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
