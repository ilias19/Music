"""Put the text of a scanned page back into homr's MusicXML: lyrics, chord symbols, tempo.

homr reads notation only. OCR (ML Kit on the phone, RapidOCR on a computer) gives
words with boxes; homr's own detection gives every notehead and barline with page
coordinates. Notes in the MusicXML are matched, line by line and bar by bar, to the
detected noteheads; then

  * words in the band just below a staff become lyrics of the notes above them
    (several text rows = several verses; "-" joins syllables),
  * chord names just above the top staff of a system become <harmony> symbols,
  * a "= 120" style tempo mark above the first system becomes the score's tempo.

Everything is best effort: a page we can't line up keeps its notes untouched.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

CHORD_RE = re.compile(
    r"^([A-G])([#b♯♭]?)(maj7|maj9|maj|M7|m7b5|m7|m9|m6|m11|min|m|dim7|dim|°|ø|aug|\+|sus2|sus4|sus|add9|add2|7sus4|"
    r"6/9|69|6|7|9|11|13)?(?:\(?(b5|#5|b9|#9|#11|b13)\)?)?(?:/([A-G])([#b♯♭]?))?$")
TEMPO_RE = re.compile(r"[=:\-\u2013\u2014]\s*(\d{2,3})\b")   # "= 120"; OCR sometimes reads "=" as ":" or "-"
BARE_NUM_RE = re.compile(r"^[^\w]*[A-Za-z]?[^\w]*(\d{2,3})$")    # "J150", "150": the note glyph read as a letter, or lost
WORDISH = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ'’]")
# Performance markings printed in the same space as lyrics; a lone one is not a syllable.
MARKINGS = {"p", "pp", "ppp", "mp", "mf", "f", "ff", "fff", "fp", "sf", "sfz", "sfp", "fz", "rf", "rfz",
            "rit", "rit.", "ritard", "ritard.", "rall", "rall.", "cresc", "cresc.", "dim", "dim.", "decresc",
            "decresc.", "a tempo", "fine", "coda", "d.c.", "d.s.", "tutti", "solo", "unis", "unis.", "div", "div.",
            "lento", "swing", "legato", "staccato", "accel", "accel.", "poco", "molto", "simile"}

KIND = {
    None: "major", "": "major", "maj": "major", "M7": "major-seventh", "maj7": "major-seventh", "maj9": "major-ninth",
    "m": "minor", "min": "minor", "m7": "minor-seventh", "m9": "minor-ninth", "m6": "minor-sixth", "m11": "minor-11th",
    "m7b5": "half-diminished", "ø": "half-diminished", "dim": "diminished", "°": "diminished", "dim7": "diminished-seventh",
    "aug": "augmented", "+": "augmented", "7": "dominant", "9": "dominant-ninth", "11": "dominant-11th",
    "13": "dominant-13th", "6": "major-sixth", "69": "major-sixth", "6/9": "major-sixth", "sus2": "suspended-second",
    "sus4": "suspended-fourth", "sus": "suspended-fourth", "7sus4": "suspended-fourth", "add9": "major", "add2": "major",
}


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class StaffGeom:
    top: float
    bottom: float
    left: float
    right: float
    unit: float
    onsets: list[float]          # x of each detected note onset (chord heads merged)
    barlines: list[float]        # x of detected barlines
    words_below: list[Word] = field(default_factory=list)
    words_above: list[Word] = field(default_factory=list)


# ------------------------------------------------------------------ geometry --
def staff_geometry(multi_staffs, scale_words: float, words: list[Word]) -> list[list[StaffGeom]]:
    """Per system, per staff: lines, noteheads, barlines and the OCR words around it."""
    words = [Word(w.text, w.x0 * scale_words, w.y0 * scale_words, w.x1 * scale_words, w.y1 * scale_words) for w in words]
    systems: list[list[StaffGeom]] = []
    for ms in multi_staffs:
        row = []
        for st in ms.staffs:
            unit = float(getattr(st, "average_unit_size", 0) or (st.max_y - st.min_y) / 4)
            heads = [n for n in st.get_notes() if st.min_y - 4.5 * unit <= n.center[1] <= st.max_y + 4.5 * unit]
            xs = sorted(float(n.center[0]) for n in heads)
            onsets: list[float] = []
            for x in xs:
                if not onsets or x - onsets[-1] > unit * 0.7:
                    onsets.append(x)
            bars = sorted(float(s.center[0]) for s in st.symbols if type(s).__name__ == "BarLine")
            merged: list[float] = []
            for x in bars:
                if not merged or x - merged[-1] > unit * 1.5:
                    merged.append(x)
            row.append(StaffGeom(float(st.min_y), float(st.max_y), float(st.min_x), float(st.max_x), unit, onsets, merged))
        systems.append(row)

    # bands: below each staff up to (most of) the gap to the next staff; above the top staff of a system
    flat = [g for row in systems for g in row]
    for i, g in enumerate(flat):
        nxt_top = flat[i + 1].top if i + 1 < len(flat) else g.bottom + 12 * g.unit
        prev_bottom = flat[i - 1].bottom if i > 0 else max(0.0, g.top - 14 * g.unit)
        for w in words:
            if not (g.left - 2 * g.unit <= w.cx <= g.right + 2 * g.unit):
                continue
            chordlike = is_chord(w.text)
            if g.bottom + 0.3 * g.unit < w.cy < g.bottom + min(nxt_top - g.bottom, 9 * g.unit) * 0.85:
                # a chord name sitting closer to the next staff belongs above that staff, not in these lyrics
                if chordlike and (nxt_top - w.cy) < (w.cy - g.bottom):
                    continue
                g.words_below.append(w)
            elif max(prev_bottom, g.top - 6 * g.unit) < w.cy < g.top - 0.2 * g.unit:
                if chordlike and i > 0 and (w.cy - prev_bottom) < (g.top - w.cy) and not _is_top_of_system(flat, i):
                    continue
                g.words_above.append(w)
    return systems


def _is_top_of_system(flat, i) -> bool:
    return True


def is_chord(text: str) -> bool:
    return CHORD_RE.match(text.strip().replace("♯", "#").replace("♭", "b")) is not None


# ---------------------------------------------------------------- MusicXML --
def _notes_by_system(part: ET.Element) -> list[list[tuple[ET.Element, list[ET.Element]]]]:
    """[(measure, [onset note elements])] per system, in order. Chord members and rests excluded."""
    systems: list[list[tuple[ET.Element, list[ET.Element]]]] = [[]]
    for i, meas in enumerate(part.findall("measure")):
        pr = meas.find("print")
        if i > 0 and pr is not None and pr.get("new-system") == "yes":
            systems.append([])
        onsets = [n for n in meas.findall("note")
                  if n.find("rest") is None and n.find("chord") is None and n.find("grace") is None]
        systems[-1].append((meas, onsets))
    return systems


def _match(onset_xs: list[float], notes_per_measure: list[list[ET.Element]], barlines: list[float],
           left: float, right: float) -> dict[int, float]:
    """id(note element) -> x on the page."""
    flat = [n for ms in notes_per_measure for n in ms]
    out: dict[int, float] = {}
    if not flat or not onset_xs:
        return out
    if len(flat) == len(onset_xs):
        return {id(n): x for n, x in zip(flat, onset_xs)}
    # bar by bar, if the detected barlines split the line into the right number of bars
    inner = [b for b in barlines if left + 10 < b < right - 10]
    if len(inner) == len(notes_per_measure) - 1:
        edges = [left, *inner, right]
        for k, notes in enumerate(notes_per_measure):
            xs = [x for x in onset_xs if edges[k] <= x < edges[k + 1]]
            if len(xs) == len(notes):
                out.update({id(n): x for n, x in zip(notes, xs)})
            elif notes and xs:
                for j, n in enumerate(notes):   # spread proportionally inside the bar
                    out[id(n)] = xs[min(len(xs) - 1, round(j * (len(xs) - 1) / max(1, len(notes) - 1)))]
        return out
    # last resort: proportional over the whole line
    for j, n in enumerate(flat):
        out[id(n)] = onset_xs[min(len(onset_xs) - 1, round(j * (len(onset_xs) - 1) / max(1, len(flat) - 1)))]
    return out


def _measure_edges(g: "StaffGeom", measures, xs: dict[int, float]) -> list[float]:
    """x boundaries of the bars of one staff line: detected barlines if they fit, else estimated."""
    n = len(measures)
    inner = [b for b in g.barlines if g.left + 10 < b < g.right - 10]
    if len(inner) == n - 1:
        return [g.left, *inner, g.right]
    # estimate: between the last note of one bar and the first note of the next; empty bars share the gap
    edges = [g.left]
    for k in range(1, n):
        prev_xs = [xs[id(x)] for x in measures[k - 1][1] if id(x) in xs]
        next_xs = [xs[id(x)] for x in measures[k][1] if id(x) in xs]
        if prev_xs and next_xs:
            edges.append((max(prev_xs) + min(next_xs)) / 2)
        else:
            edges.append(None)
    edges.append(g.right)
    # fill unknown edges by spreading evenly between known ones
    known = [i for i, e in enumerate(edges) if e is not None]
    for a, b in zip(known, known[1:]):
        for i in range(a + 1, b):
            edges[i] = edges[a] + (edges[b] - edges[a]) * (i - a) / (b - a)
    return edges


def _rows(words: list[Word], unit: float) -> list[list[Word]]:
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: w.cy):
        if rows and abs(w.cy - sum(x.cy for x in rows[-1]) / len(rows[-1])) < unit * 1.2:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w.cx) for r in rows]


def _syllables(row: list[Word]) -> list[tuple[Word, str]]:
    """Split OCR words into syllables with MusicXML syllabic values, using '-' between them."""
    toks: list[Word] = []
    for w in row:
        t = w.text.strip()
        if not t:
            continue
        # OCR sometimes glues hyphens to syllables: "li-" or "-a"
        parts = [p for p in re.split(r"(\s*[-–—]\s*)", t) if p and p.strip()]
        if len(parts) == 1:
            toks.append(Word(parts[0].strip(), w.x0, w.y0, w.x1, w.y1))
        else:
            step = (w.x1 - w.x0) / max(1, len(t))
            pos = 0
            for p in parts:
                x0 = w.x0 + pos * step
                pos += len(p)
                toks.append(Word(p.strip(), x0, w.y0, w.x0 + pos * step, w.y1))
    out: list[tuple[Word, str]] = []
    joined_prev = False
    for i, w in enumerate(toks):
        if re.fullmatch(r"[-–—_]+", w.text):
            joined_prev = True
            continue
        if not WORDISH.search(w.text):
            joined_prev = False
            continue
        if not joined_prev and w.text.lower() in MARKINGS:
            continue   # a dynamic or tempo word, not part of a hyphenated lyric
        joins_next = i + 1 < len(toks) and re.fullmatch(r"[-–—]+", toks[i + 1].text) is not None
        syl = {(False, False): "single", (False, True): "begin", (True, True): "middle", (True, False): "end"}[(joined_prev, joins_next)]
        out.append((w, syl))
        joined_prev = False
    return out


def _add_lyric(note: ET.Element, text: str, syllabic: str, number: int) -> None:
    lyr = ET.Element("lyric", {"number": str(number)})
    ET.SubElement(lyr, "syllabic").text = syllabic
    ET.SubElement(lyr, "text").text = text
    # <lyric> goes after notations/stem etc. but before <play>/<listen>: appending is valid for homr's notes
    note.append(lyr)


def _is_tie_continuation(note: ET.Element) -> bool:
    return any(t.get("type") == "stop" for t in note.findall("tie")) and \
        not any(t.get("type") == "start" for t in note.findall("tie"))


def _harmony(text: str) -> ET.Element | None:
    t = text.strip().replace("♯", "#").replace("♭", "b")
    m = CHORD_RE.match(t)
    if not m:
        return None
    root, acc, kind, alt, bass, bacc = m.groups()
    h = ET.Element("harmony", {"print-frame": "no"})
    r = ET.SubElement(h, "root")
    ET.SubElement(r, "root-step").text = root
    if acc:
        ET.SubElement(r, "root-alter").text = "1" if acc == "#" else "-1"
    k = ET.SubElement(h, "kind", {"text": (kind or "") + (f"({alt})" if alt else "")})
    k.text = KIND.get(kind, "major")
    if bass:
        b = ET.SubElement(h, "bass")
        ET.SubElement(b, "bass-step").text = bass
        if bacc:
            ET.SubElement(b, "bass-alter").text = "1" if bacc == "#" else "-1"
    return h


# -------------------------------------------------------------------- main --
def annotate(xml_path: str, multi_staffs, homr_width: int, image_width: int, words: list[Word]) -> dict:
    """Add lyrics, chord symbols and tempo to the MusicXML at xml_path (in place). Returns counts."""
    stats = {"lyrics": 0, "chords": 0, "tempo": None, "lines_matched": 0, "lines": 0}
    if not words or not multi_staffs:
        return stats
    systems = staff_geometry(multi_staffs, homr_width / max(1, image_width), words)
    tree = ET.parse(xml_path)
    root = tree.getroot()
    parts = root.findall("part")
    if not parts:
        return stats

    # parts are homr "voices" = the staff index within each system
    for p_idx, part in enumerate(parts):
        xml_systems = _notes_by_system(part)
        for s_idx, geom_row in enumerate(systems):
            if s_idx >= len(xml_systems) or p_idx >= len(geom_row):
                continue
            g = geom_row[p_idx]
            measures = xml_systems[s_idx]
            stats["lines"] += 1
            xs = _match(g.onsets, [ons for _, ons in measures], g.barlines, g.left, g.right)
            if not xs:
                continue
            stats["lines_matched"] += 1
            targets = sorted(((x, n) for n in (n for _, ons in measures for n in ons)
                              if (x := xs.get(id(n))) is not None and not _is_tie_continuation(n)), key=lambda t: t[0])
            # lyrics: one row of text per verse
            for verse, row in enumerate(_rows(g.words_below, g.unit)[:4], start=1):
                used = -1
                for w, syl in _syllables(row):
                    best, best_d = None, 3.2 * g.unit
                    for k, (x, n) in enumerate(targets):
                        if k <= used:
                            continue
                        d = abs(x - w.cx)
                        if d < best_d:
                            best, best_d = k, d
                    if best is not None:
                        _add_lyric(targets[best][1], w.text, syl, verse)
                        used = best
                        stats["lyrics"] += 1
            # chord symbols above the top staff of the system, placed in the bar they stand over
            if p_idx == 0:
                edges = _measure_edges(g, measures, xs)
                for w in sorted(g.words_above, key=lambda w: w.cx):
                    h = _harmony(w.text)
                    if h is None:
                        continue
                    k = next((k for k in range(len(measures)) if edges[k] <= w.cx < edges[k + 1]),
                             0 if w.cx < edges[0] else len(measures) - 1)
                    meas, ons = measures[k]
                    # before the bar's note closest under the chord, or at the start of the bar
                    near = [n for n in ons if id(n) in xs and abs(xs[id(n)] - w.cx) < 2.5 * g.unit]
                    anchor = min(near, key=lambda n: abs(xs[id(n)] - w.cx)) if near else None
                    if anchor is None:
                        first = next((el for el in meas if el.tag in ("note", "backup", "forward")), None)
                        anchor = first
                    meas.insert(list(meas).index(anchor) if anchor is not None else len(meas), h)
                    stats["chords"] += 1

    # tempo "= 120" above the first system (OCR may split "=" and "120" into two words)
    first = systems[0][0] if systems and systems[0] else None
    if first is not None:
        scale = homr_width / max(1, image_width)
        ordered = sorted(words, key=lambda w: (round(w.cy / 8), w.x0))
        candidates = [(w, w.text) for w in ordered]
        for a, b in zip(ordered, ordered[1:]):
            if abs(a.cy - b.cy) < (a.y1 - a.y0) and 0 <= b.x0 - a.x1 < 3 * (a.y1 - a.y0):
                candidates.append((a, a.text + " " + b.text))
        above = [w for w in ordered if w.cy * scale < first.top]
        print("lyrics: text above the first staff:", [w.text for w in above][:20], flush=True)
        # a lone number above the music, in the left half, is the tempo when no "=" was read
        candidates += [(w, "=" + BARE_NUM_RE.match(w.text).group(1)) for w in above
                       if BARE_NUM_RE.match(w.text) and w.x1 < image_width * 0.5]
        for w, text in candidates:
            m = TEMPO_RE.search(text)
            if m and w.cy * scale < first.top and 40 <= int(m.group(1)) <= 260:
                bpm = int(m.group(1))
                meas = parts[0].find("measure")
                d = ET.Element("direction", {"placement": "above"})
                dt = ET.SubElement(d, "direction-type")
                mt = ET.SubElement(dt, "metronome")
                ET.SubElement(mt, "beat-unit").text = "quarter"
                ET.SubElement(mt, "per-minute").text = str(bpm)
                ET.SubElement(d, "sound", {"tempo": str(bpm)})
                first_note = meas.find("note")
                meas.insert(list(meas).index(first_note) if first_note is not None else len(meas), d)
                stats["tempo"] = bpm
                break
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
    return stats
