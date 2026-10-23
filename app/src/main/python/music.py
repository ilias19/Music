"""Score transformations: transposition, MIDI export, summaries.

PDF export happens in the app's web view (jsPDF), so no Verovio/Cairo here."""
from __future__ import annotations

from functools import lru_cache

import music21 as m21

# For each semitone offset within an octave, spellings to try. We pick the one
# whose resulting key signature has the fewest accidentals (C+1 -> Db, not C#).
SPELLINGS = {
    0: ["P1"], 1: ["m2", "A1"], 2: ["M2", "d3"], 3: ["m3", "A2"],
    4: ["M3", "d4"], 5: ["P4", "A3"], 6: ["d5", "A4"], 7: ["P5", "d6"],
    8: ["m6", "A5"], 9: ["M6", "d7"], 10: ["m7", "A6"], 11: ["M7", "d8"],
}


def parse(xml_text: str) -> m21.stream.Score:
    s = m21.converter.parseData(xml_text, format="musicxml")
    if not isinstance(s, m21.stream.Score):
        sc = m21.stream.Score()
        sc.insert(0, s)
        s = sc
    return s


def to_xml(score: m21.stream.Score) -> str:
    from music21.musicxml.m21ToXml import GeneralObjectExporter
    return GeneralObjectExporter(score).parse().decode("utf-8")


def _key_cost(score: m21.stream.Score) -> int:
    ks = score.recurse().getElementsByClass(m21.key.KeySignature).first()
    return abs(ks.sharps) if ks is not None else 0


def _transpose_by(score: m21.stream.Score, semitones: int) -> m21.stream.Score:
    octaves, rem = divmod(semitones, 12)
    best, best_cost = None, None
    for name in SPELLINGS[rem]:
        s = score.transpose(name) if name != "P1" else score
        for _ in range(abs(octaves)):
            s = s.transpose("P8" if octaves > 0 else "-P8")
        cost = _key_cost(s)
        if best is None or cost < best_cost:
            best, best_cost = s, cost
    return best


@lru_cache(maxsize=64)
def transposed_xml(xml_text: str, semitones: int) -> str:
    if semitones == 0:
        return xml_text
    return to_xml(_transpose_by(parse(xml_text), semitones))


def _with_tempo(score: m21.stream.Score, bpm: float | None) -> m21.stream.Score:
    if not bpm:
        return score
    for mm in list(score.recurse().getElementsByClass(m21.tempo.MetronomeMark)):
        mm.activeSite.remove(mm)
    for part in score.parts:
        first = part.getElementsByClass(m21.stream.Measure).first()
        (first or part).insert(0, m21.tempo.MetronomeMark(number=bpm))
    return score


def to_midi(xml_text: str, semitones: int = 0, bpm: float | None = None) -> bytes:
    score = _with_tempo(parse(transposed_xml(xml_text, semitones)), bpm)
    mf = m21.midi.translate.music21ObjectToMidiFile(score)
    return mf.writestr()


def summary(xml_text: str) -> dict:
    """Small facts shown in the library: parts, measures, key, time signature."""
    s = parse(xml_text)
    parts = list(s.parts)
    measures = len(parts[0].getElementsByClass(m21.stream.Measure)) if parts else 0
    ks = s.recurse().getElementsByClass(m21.key.KeySignature).first()
    ts = s.recurse().getElementsByClass(m21.meter.TimeSignature).first()
    mm = s.recurse().getElementsByClass(m21.tempo.MetronomeMark).first()
    key_name = None
    if ks is not None:
        try:
            maj = ks.asKey("major").tonic.name.replace("-", "b")
            mnr = ks.asKey("minor").tonic.name.replace("-", "b")
            key_name = f"{maj} major / {mnr} minor"
        except Exception:
            key_name = f"{ks.sharps:+d} accidentals"
    return {
        "parts": len(parts),
        "measures": measures,
        "key": key_name,
        "fifths": ks.sharps if ks is not None else None,
        "time": ts.ratioString if ts is not None else None,
        "tempo": round(mm.number) if mm is not None and mm.number else None,
        "part_names": [(p.partName or "").strip() for p in parts],
        "lyrics": "<lyric" in xml_text,
    }
