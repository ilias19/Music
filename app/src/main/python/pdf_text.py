"""Words printed in a PDF, with their boxes, read from the PDF itself (no OCR).

Scores exported from MuseScore, Sibelius, Dorico, LilyPond... carry their lyrics,
chord symbols and tempo marks as real text. Reading that text is exact (every
syllable, correctly split, correctly placed) and quick, where OCR on the rendered
page merges close syllables ("Fi la" -> "Fila") and takes seconds.

Uses pdfminer.six (pure Python), which places every character with the PDF's own
font metrics and transformation matrices.

words(pdf, page_index, image_w, image_h) -> [{"t", "x0", "y0", "x1", "y1"}] in the pixels
of the rendered page image: the same shape the OCR returns.
"""
from __future__ import annotations

import re
import sys
import types
import unicodedata


def _stub_cryptography() -> None:
    """pdfminer imports `cryptography` for AES-encrypted PDFs only; the app doesn't ship it."""
    try:
        import cryptography  # noqa: F401
        return
    except ImportError:
        pass

    class _Missing:
        def __init__(self, *a, **k):
            raise RuntimeError("encrypted PDFs (AES) aren't supported")

    names = ["cryptography", "cryptography.hazmat", "cryptography.hazmat.backends",
             "cryptography.hazmat.primitives", "cryptography.hazmat.primitives.ciphers"]
    for n in names:
        sys.modules.setdefault(n, types.ModuleType(n))
    sys.modules["cryptography.hazmat.backends"].default_backend = lambda: None
    ciphers = sys.modules["cryptography.hazmat.primitives.ciphers"]
    ciphers.Cipher = _Missing
    ciphers.algorithms = types.SimpleNamespace(AES=_Missing)
    ciphers.modes = types.SimpleNamespace(CBC=_Missing, ECB=_Missing)


# sharps and flats drawn with the music font (SMuFL: staff accidentals E260.., chord-symbol ones ED60..)
ACCIDENTALS = {"\ue260": "b", "\ue261": "", "\ue262": "#", "\ue263": "##", "\ue264": "bb",
               "\ued60": "b", "\ued61": "", "\ued62": "#", "\ued63": "##", "\ued64": "bb",
               "\u266d": "b", "\u266e": "", "\u266f": "#"}
# SMuFL chord-symbol signs (csymDiminished, csymHalfDiminished, csymAugmented, csymMajorSeventh, csymMinor)
CHORD_SIGNS = {"\ue870": "dim", "\ue871": "m7b5", "\ue872": "aug", "\ue873": "maj7", "\ue874": "m"}
CHORD_HEAD = re.compile(r"^[A-G](b|#)?(m|maj|min|dim|aug|sus|M)?\d*$")


def _is_music_glyph(c: str) -> bool:
    # music fonts (SMuFL, Bravura, Emmentaler...) put noteheads, clefs and rests in the private use area
    return 0xE000 <= ord(c) <= 0xF8FF


def _runs(chars) -> list[dict]:
    """Characters in drawing order, cut into words. A word is one text object (BT..ET, which
    notation programs use per lyric syllable, chord, label...), cut further at spaces and at
    clear gaps; inside it glyphs may be kerned."""
    runs: list[dict] = []
    cur = None
    for c in chars:
        t = c.get_text()
        size = max(c.size, 1.0)
        if not t.strip():
            cur = None
            continue
        gap = c.x0 - cur["x1"] if cur else 0.0
        if (cur and not getattr(c, "_new_text", False) and abs(c.y0 - cur["by"]) < 0.3 * size
                and -0.15 * size <= gap < 0.15 * size):
            cur["t"] += t
            cur["x1"] = max(cur["x1"], c.x1)
            cur["y1"] = max(cur["y1"], c.y1)
            continue
        cur = {"t": t, "x0": c.x0, "x1": c.x1, "y0": c.y0, "y1": c.y1, "by": c.y0, "size": size}
        runs.append(cur)
    return runs


def _glue_chords(runs: list[dict]) -> list[dict]:
    """Chord symbols are often drawn in pieces: "E" + "m", "B" + a flat from the music font + "sus4".
    Glue touching pieces when they make a chord; lyric syllables drawn side by side stay apart."""
    for r in runs:
        r["acc"] = all(ch in ACCIDENTALS or ch in CHORD_SIGNS for ch in r["t"])   # alone: on the staff
        r["t"] = unicodedata.normalize("NFKC", "".join(ACCIDENTALS.get(ch, CHORD_SIGNS.get(ch, ch)) for ch in r["t"]))
    runs = sorted(runs, key=lambda r: (round(r["by"]), r["x0"]))
    out: list[dict] = []
    for r in runs:
        q = out[-1] if out else None
        if (q and abs(q["by"] - r["by"]) < 0.5 * q["size"] and -0.1 * q["size"] <= r["x0"] - q["x1"] < 0.15 * q["size"]
                and CHORD_HEAD.match(q["t"]) and not q["t"].endswith(tuple("0123456789")) or
                q and abs(q["by"] - r["by"]) < 0.5 * q["size"] and 0 <= r["x0"] - q["x1"] < 0.15 * q["size"]
                and r["acc"] and CHORD_HEAD.match(q["t"] or "")):
            q["t"] += r["t"]
            q["acc"] = False
            q["x1"] = r["x1"]
            q["y1"] = max(q["y1"], r["y1"])
            continue
        out.append(dict(r))
    return [r for r in out if r["t"] and not r["acc"] and not all(_is_music_glyph(ch) for ch in r["t"])]


def words(pdf_path: str, index: int, image_w: int, image_h: int) -> list[dict]:
    _stub_cryptography()
    import itertools
    from pdfminer.converter import PDFPageAggregator
    from pdfminer.layout import LTChar, LTFigure
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
    from pdfminer.pdfpage import PDFPage

    state = {"new": True}

    class Device(PDFPageAggregator):
        def render_string(self, *args, **kwargs):
            objs = self.cur_item._objs
            n = len(objs)
            r = super().render_string(*args, **kwargs)
            if len(objs) > n and state["new"]:
                objs[n]._new_text = True     # first character of a text object
                state["new"] = False
            return r

    class Interp(PDFPageInterpreter):
        def do_BT(self):
            state["new"] = True
            return super().do_BT()

    rm = PDFResourceManager()
    dev = Device(rm, laparams=None)          # no layout analysis: characters in drawing order
    interp = Interp(rm, dev)
    with open(pdf_path, "rb") as f:
        page = next(itertools.islice(PDFPage.get_pages(f), index, index + 1), None)
        if page is None:
            return []
        interp.process_page(page)
    layout = dev.get_result()
    x0p, y0p, x1p, y1p = layout.bbox
    pw, ph = x1p - x0p, y1p - y0p
    if pw <= 0 or ph <= 0 or (page.rotate or 0) % 360:
        return []

    def flat(objs):
        for o in objs:
            if isinstance(o, LTChar):
                yield o
            elif isinstance(o, LTFigure):
                yield from flat(o)

    keep = set(ACCIDENTALS) | set(CHORD_SIGNS)
    chars = [c for c in flat(layout) if c.upright and (not _is_music_glyph(c.get_text()) or c.get_text() in keep)]
    sx, sy = image_w / pw, image_h / ph
    return [{"t": w["t"], "x0": (w["x0"] - x0p) * sx, "x1": (w["x1"] - x0p) * sx,
             "y0": (y1p - w["y1"]) * sy, "y1": (y1p - w["y0"]) * sy}
            for w in _glue_chords(_runs(chars))]
