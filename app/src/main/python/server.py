"""Practice Room's backend, running inside the Android app.

Same HTTP API as the desktop FastAPI server, built on the standard library
only (FastAPI's compiled dependencies don't exist for Android). It listens on
127.0.0.1 and the app's WebView talks to it. A per-launch secret cookie stops
other apps on the phone from using the port.

Kotlin starts it with:
    server.start(data_dir, www_dir, gallery_dir, models_dir, ort_bridge, platform, token) -> port
`platform` supplies the phone-only bits: platform.renderPdf(pdf, out_dir, dpi)
returns the PNG paths of a PDF's pages (Android's PdfRenderer).
"""
from __future__ import annotations

import email.parser
import email.policy
import json
import mimetypes
import os
import queue
import re
import shutil
import sqlite3
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".heic"}
MAX_UPLOAD = 60 << 20
MAX_SIDE = 3500

S: dict = {}  # server state: paths, token, bridge, platform
db_lock = threading.Lock()
jobs: "queue.Queue[tuple]" = queue.Queue()
downloads: dict[str, tuple[str, str, bytes, float]] = {}


class HTTPError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


# ------------------------------------------------------------------ storage --
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(S["db"], timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def q(sql: str, args=(), one=False):
    with db_lock:
        conn = db()
        try:
            cur = conn.execute(sql, args)
            rows = cur.fetchall()
            conn.commit()
        finally:
            conn.close()
    return (rows[0] if rows else None) if one else rows


def init_db() -> None:
    with db_lock:
        conn = db()
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS scores(
            id TEXT PRIMARY KEY, title TEXT, source TEXT, created TEXT,
            status TEXT, progress REAL DEFAULT 0, message TEXT, error TEXT,
            info TEXT, last_opened TEXT);
        CREATE TABLE IF NOT EXISTS practice(
            id INTEGER PRIMARY KEY AUTOINCREMENT, score_id TEXT, started TEXT, seconds INTEGER);
        CREATE TABLE IF NOT EXISTS recordings(
            id TEXT PRIMARY KEY, score_id TEXT, created TEXT, filename TEXT,
            mime TEXT, seconds REAL, label TEXT);
        """)
        conn.execute("UPDATE scores SET status='failed', error='Interrupted when the app closed. Scan it again.' "
                     "WHERE status IN ('queued','scanning')")
        conn.commit()
        conn.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def score_dir(sid: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", sid or ""):
        raise HTTPError(404, "Unknown score")
    return S["scores"] / sid


def load_xml(sid: str) -> str:
    p = score_dir(sid) / "score.musicxml"
    if not p.exists():
        raise HTTPError(404, "Score not ready")
    return p.read_text(encoding="utf-8")


def row_to_score(r) -> dict:
    d = dict(r)
    d["info"] = json.loads(d["info"]) if d.get("info") else None
    return d


def update_score(sid: str, **fields) -> None:
    if fields.get("info") is not None:
        fields["info"] = json.dumps(fields["info"])
    cols = ", ".join(f"{k}=?" for k in fields)
    q(f"UPDATE scores SET {cols} WHERE id=?", (*fields.values(), sid))


def create_score(title: str, source: str, status: str) -> str:
    sid = uuid.uuid4().hex[:12]
    score_dir(sid).mkdir(parents=True)
    q("INSERT INTO scores(id,title,source,created,status,message) VALUES(?,?,?,?,?,?)",
      (sid, title, source, now(), status, ""))
    return sid


def save_score_xml(sid: str, xml: str):
    import music
    (score_dir(sid) / "score.musicxml").write_text(xml, encoding="utf-8")
    try:
        return music.summary(xml)
    except Exception:
        traceback.print_exc()
        return None


def clean_title(name: str) -> str:
    t = Path(name).stem.replace("_", " ").replace("-", " ").strip()
    return t[:120] or "Untitled"


def get_score(sid: str) -> dict:
    r = q("SELECT * FROM scores WHERE id=?", (sid,), one=True)
    if not r:
        raise HTTPError(404, "Unknown score")
    d = row_to_score(r)
    pages = score_dir(sid) / "pages"
    d["page_count"] = len(list(pages.glob("page_[0-9][0-9][0-9].png"))) if pages.exists() else 0
    return d


# -------------------------------------------------------------------- scans --
def _next_page(workdir: Path) -> Path:
    n = len(list(workdir.glob("page_[0-9][0-9][0-9].png")))
    return workdir / f"page_{n + 1:03d}.png"


def pages_from_upload(src: Path, workdir: Path) -> list[Path]:
    from PIL import Image, ImageOps
    ext = src.suffix.lower()
    out: list[Path] = []
    if ext == ".pdf":
        tmp = workdir / f"pdf_{src.stem}"
        tmp.mkdir(exist_ok=True)
        for p in S["platform"].renderPdf(str(src), str(tmp), 300):
            dst = _next_page(workdir)
            os.replace(str(p), dst)
            out.append(dst)
        shutil.rmtree(tmp, ignore_errors=True)
    elif ext in IMAGE_EXTS:
        img = Image.open(src)
        img = ImageOps.exif_transpose(img)  # phone photos are stored rotated
        if img.mode not in ("RGB", "L"):
            bg = Image.new("RGB", img.size, "white")
            alpha = img.convert("RGBA").split()[-1]
            bg.paste(img.convert("RGB"), mask=alpha)
            img = bg
        out.append(_next_page(workdir))
        img.save(out[-1])
    else:
        raise ValueError(f"Unsupported file type: {ext}")
    for p in out:
        img = Image.open(p)
        if max(img.size) > MAX_SIDE:
            img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
            img.save(p)
    return out


def clean_homr_xml(path: Path) -> None:
    """Fix structural quirks in homr's MusicXML, in place.

    * Start-repeats come out on the right edge of the bar they start;
      MusicXML wants them on its left edge, or players show an end-repeat.
    * A start-repeat at the beginning of a line produces an extra bar with no
      notes at all; drop such bars (real empty bars always contain a rest).
    * The final barline is written heavy-heavy instead of light-heavy.
    """
    import xml.etree.ElementTree as ET
    tree = ET.parse(path)
    root = tree.getroot()
    for part in root.findall("part"):
        measures = part.findall("measure")
        # 1) move forward repeats to the left edge of the same bar
        for meas in measures:
            for bl in list(meas.findall("barline")):
                rep = bl.find("repeat")
                if bl.get("location", "right") == "right" and rep is not None and rep.get("direction") == "forward":
                    meas.remove(bl)
                    left = ET.Element("barline", {"location": "left"})
                    ET.SubElement(left, "bar-style").text = "heavy-light"
                    ET.SubElement(left, "repeat", {"direction": "forward"})
                    first_music = next((i for i, el in enumerate(meas) if el.tag not in ("print", "attributes")), len(meas))
                    meas.insert(first_music, left)
        # 2) drop bars without notes, carrying their layout/attributes into the next bar
        measures = part.findall("measure")
        for i, meas in enumerate(measures):
            if meas.find("note") is not None or i + 1 >= len(measures):
                continue
            nxt = measures[i + 1]
            carry = [el for el in meas if el.tag in ("print", "attributes")]
            for el in reversed(carry):
                if el.tag == "print" and nxt.find("print") is not None:
                    continue
                nxt.insert(0, el)
            for bl in meas.findall("barline"):
                rep = bl.find("repeat")
                if rep is not None and rep.get("direction") == "backward":
                    # an end-repeat on a phantom bar belongs to the previous real bar
                    if i > 0:
                        measures[i - 1].append(bl)
            part.remove(meas)
        # 3) renumber, and make the last barline a normal final barline
        measures = part.findall("measure")
        for n, meas in enumerate(measures, 1):
            meas.set("number", str(n))
        if measures:
            for bl in measures[-1].findall("barline"):
                style = bl.find("bar-style")
                if style is not None and style.text == "heavy-heavy" and bl.find("repeat") is None:
                    style.text = "light-heavy"
    tree.write(path, encoding="utf-8", xml_declaration=True)


def merge_pages(xmls: list[Path], title: str) -> str:
    import music21 as m21
    import music
    for x in xmls:
        try:
            clean_homr_xml(x)
        except Exception:
            traceback.print_exc()  # cleaning is best effort; the raw scan is still usable
    score = m21.converter.parse(str(xmls[0]))
    for x in xmls[1:]:
        nxt = m21.converter.parse(str(x))
        for i, part in enumerate(nxt.parts):
            if i >= len(score.parts):
                break
            target = score.parts[i]
            last = target.getElementsByClass(m21.stream.Measure)
            num = last[-1].number if last else 0
            for meas in part.getElementsByClass(m21.stream.Measure):
                num += 1
                meas.number = num
                target.append(meas)
    if score.metadata is None:
        score.insert(0, m21.metadata.Metadata())
    score.metadata.title = title
    score.metadata.movementName = title
    score.metadata.composer = ""   # otherwise music21 writes "Music21" as the composer
    return music.to_xml(score)


def run_scan(sid: str, files: list[Path], title: str) -> None:
    import omr_android
    try:
        update_score(sid, status="scanning", message="Preparing pages", progress=0.02)
        work = score_dir(sid) / "pages"
        work.mkdir(exist_ok=True)
        pages: list[Path] = []
        for f in files:
            pages.extend(pages_from_upload(f, work))
        if not pages:
            raise ValueError("Nothing to scan")
        update_score(sid, message="Loading the recognition models", progress=0.05)
        omr_android.setup(S["bridge"], str(S["models"]))
        xmls, errors = [], []
        for i, p in enumerate(pages):
            update_score(sid, message=f"Reading page {i + 1} of {len(pages)}",
                         progress=round(0.08 + 0.85 * i / len(pages), 3))
            try:
                xmls.append(Path(omr_android.scan_page(str(p))))
            except Exception as e:
                traceback.print_exc()
                errors.append(f"page {i + 1}: {e}")
        if not xmls:
            raise ValueError("; ".join(errors) or "No music found")
        update_score(sid, message="Assembling score", progress=0.95)
        info = save_score_xml(sid, merge_pages(xmls, title))
        for p in work.glob("*_teaser.png"):
            p.unlink()
        msg = f"Skipped {len(errors)} page(s) with no readable music" if errors else ""
        update_score(sid, status="ready", message=msg, progress=1, info=info)
    except Exception as e:
        traceback.print_exc()
        update_score(sid, status="failed", error=str(e)[:500], message="")


def scan_worker() -> None:
    while True:
        sid, files, title = jobs.get()
        run_scan(sid, files, title)


# ------------------------------------------------------------------- routes --
def parse_multipart(body: bytes, ctype: str) -> tuple[dict, list[tuple[str, str, bytes, str]]]:
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    fields, files = {}, []
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_param("filename", header="content-disposition")
        data = part.get_payload(decode=True) or b""
        if filename is not None:
            files.append((name, filename, data, part.get_content_type()))
        else:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files


def save_files(files, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for i, (_, filename, data, _) in enumerate(files):
        p = dest / f"upload_{i:02d}{Path(filename or '').suffix.lower()}"
        p.write_bytes(data)
        out.append(p)
    return out


def r_scan(h, fields, files):
    files = [f for f in files if f[0] == "files"]
    if not files:
        raise HTTPError(400, "No files")
    for _, fn, _, ctype in files:
        ext = Path(fn or "").suffix.lower()
        if ext not in IMAGE_EXTS | {".pdf"} and not ctype.startswith("image/"):
            raise HTTPError(400, f"{fn}: upload PDF or image files")
    # camera pictures may arrive without an extension
    files = [(n, fn if Path(fn or "").suffix else (fn or "photo") + ".jpg", d, c) for n, fn, d, c in files]
    title = (fields.get("title") or "").strip() or clean_title(files[0][1])
    sid = create_score(title, "scan", "queued")
    saved = save_files(files, score_dir(sid) / "upload")
    update_score(sid, message="Waiting in queue")
    jobs.put((sid, saved, title))
    return {"id": sid}


def r_import(h, fields, files):
    import music21 as m21
    import music
    files = [f for f in files if f[0] == "file"]
    if not files:
        raise HTTPError(400, "No file")
    fn = files[0][1] or ""
    ext = Path(fn).suffix.lower()
    if ext not in {".musicxml", ".xml", ".mxl"}:
        raise HTTPError(400, "Use a .musicxml, .xml or .mxl file")
    title = (fields.get("title") or "").strip() or clean_title(fn)
    sid = create_score(title, "import", "scanning")
    [p] = save_files(files[:1], score_dir(sid) / "upload")
    try:
        xml = music.to_xml(m21.converter.parse(str(p))) if ext == ".mxl" else p.read_text("utf-8", "replace")
        music.parse(xml)
    except Exception as e:
        shutil.rmtree(score_dir(sid), ignore_errors=True)
        q("DELETE FROM scores WHERE id=?", (sid,))
        raise HTTPError(400, f"Could not read that file: {e}")
    update_score(sid, status="ready", progress=1, info=save_score_xml(sid, xml))
    return {"id": sid}


def r_list(h):
    rows = q("SELECT * FROM scores ORDER BY COALESCE(last_opened, created) DESC, rowid DESC")
    secs = {r["score_id"]: r["s"] for r in q("SELECT score_id, SUM(seconds) s FROM practice GROUP BY score_id")}
    out = []
    for r in rows:
        d = row_to_score(r)
        d["practice_seconds"] = secs.get(d["id"], 0)
        out.append(d)
    return out


def r_export(h, sid, fmt, params):
    import music
    r = q("SELECT title FROM scores WHERE id=?", (sid,), one=True)
    if not r:
        raise HTTPError(404, "Unknown score")
    semis = int(params.get("semitones", 0))
    base = re.sub(r'[\\/:*?"<>|]+', "", r["title"]).strip() or "score"
    if semis:
        base += f" ({semis:+d})"
    xml = load_xml(sid)
    if fmt == "musicxml":
        return ("raw", music.transposed_xml(xml, semis).encode(), "application/vnd.recordare.musicxml+xml",
                f"{base}.musicxml")
    if fmt == "midi":
        bpm = float(params["bpm"]) if params.get("bpm") else None
        return ("raw", music.to_midi(xml, semis, bpm), "audio/midi", f"{base}.mid")
    raise HTTPError(400, "Format must be musicxml or midi (PDF is made in the app)")


def r_add_recording(h, sid, fields, files):
    get_score(sid)
    files = [f for f in files if f[0] == "file"]
    if not files:
        raise HTTPError(400, "No audio")
    _, _, data, ctype = files[0]
    mime = (ctype or "audio/webm").split(";")[0]
    ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3",
           "audio/wav": "wav"}.get(mime, "webm")
    rid = uuid.uuid4().hex[:12]
    rec = score_dir(sid) / "recordings"
    rec.mkdir(exist_ok=True)
    (rec / f"{rid}.{ext}").write_bytes(data)
    q("INSERT INTO recordings VALUES(?,?,?,?,?,?,?)",
      (rid, sid, now(), f"{rid}.{ext}", mime, float(fields.get("seconds") or 0), (fields.get("label") or "")[:80]))
    return {"id": rid}


def r_practice_summary(h, params):
    tz = timezone(timedelta(minutes=int(params.get("tz_offset_minutes", 0))))
    today = datetime.now(tz).date()
    days = {(today - timedelta(days=i)).isoformat(): 0 for i in range(13, -1, -1)}
    total = 0
    for r in q("SELECT started, seconds FROM practice"):
        d = datetime.fromisoformat(r["started"]).astimezone(tz).date().isoformat()
        total += r["seconds"]
        if d in days:
            days[d] += r["seconds"]
    streak = 0
    for d in sorted(days, reverse=True):
        if days[d] > 0:
            streak += 1
        elif d != today.isoformat():
            break
    return {"days": [{"date": d, "seconds": s} for d, s in days.items()], "today": days[today.isoformat()],
            "week": sum(list(days.values())[-7:]), "total": total, "streak": streak}


def r_downloads_add(h, params, body: bytes):
    """Hold a file the page generated (e.g. PDF) so the app can save it via a normal download."""
    name = re.sub(r'[\\/:*?"<>|]+', "", params.get("name") or "download")[:120] or "download"
    mime = params.get("mime") or mimetypes.guess_type(name)[0] or "application/octet-stream"
    did = uuid.uuid4().hex[:16]
    for k in [k for k, v in downloads.items() if time.time() - v[3] > 600]:
        downloads.pop(k, None)
    downloads[did] = (name, mime, body, time.time())
    return {"url": f"/api/downloads/{did}"}


def seed_gallery() -> None:
    if q("SELECT COUNT(*) c FROM scores", one=True)["c"]:
        return
    manifest = S["gallery"] / "gallery.json"
    if not manifest.exists():
        return
    for item in reversed(json.loads(manifest.read_text())):  # newest rowid first
        xml = (S["gallery"] / item["file"]).read_text(encoding="utf-8")
        sid = create_score(item["title"], "gallery", "ready")
        update_score(sid, progress=1, info=save_score_xml(sid, xml))


# ------------------------------------------------------------------ handler --
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PracticeRoom"

    def log_message(self, fmt, *args):  # quiet; errors still print tracebacks
        pass

    # --- helpers ---
    def _authorised(self) -> bool:
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return "pr" in c and c["pr"].value in S["tokens"]

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise HTTPError(413, "File too large")
        return self.rfile.read(n) if n else b""

    def _form(self):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            raise HTTPError(400, "Expected a file upload")
        return parse_multipart(self._body(), ctype)

    def _file(self, path: Path, mime: str | None = None, attach: str | None = None) -> None:
        if not path.is_file():
            raise HTTPError(404, "Not found")
        data = path.read_bytes()
        extra = {"Content-Disposition": f'attachment; filename="{attach}"'} if attach else None
        self._send(200, data, mime or mimetypes.guess_type(str(path))[0] or "application/octet-stream", extra)

    # --- dispatch ---
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        path = unquote(url.path)
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if path == "/__start":  # the app opens this once with the launch secret
                if params.get("k") not in S["tokens"]:
                    raise HTTPError(403, "Forbidden")
                self._send(302, b"", "text/plain", {
                    "Location": "/", "Set-Cookie": f"pr={params['k']}; Path=/; HttpOnly; SameSite=Strict"})
                return
            if not self._authorised():
                raise HTTPError(403, "Open Practice Room from the app")
            if path.startswith("/api/"):
                self._api(method, path, params)
            else:
                self._static(path)
        except HTTPError as e:
            self._json({"detail": e.detail}, e.status)
        except BrokenPipeError:
            pass
        except Exception as e:
            traceback.print_exc()
            try:
                self._json({"detail": str(e)[:300]}, 500)
            except Exception:
                pass

    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        p = (S["www"] / rel).resolve()
        if not str(p).startswith(str(S["www"].resolve())):
            raise HTTPError(404, "Not found")
        if p.is_dir():
            p = p / "index.html"
        mime = mimetypes.guess_type(str(p))[0]
        if p.suffix == ".js" or p.suffix == ".mjs":
            mime = "text/javascript"
        self._file(p, mime)

    def _api(self, method: str, path: str, params: dict) -> None:
        import music
        parts = path.strip("/").split("/")[1:]  # drop "api"
        m = (method, *parts)
        if m == ("GET", "health"):
            return self._json({"ok": True, "scan_engine": "homr (on device)"})
        if m == ("GET", "scores"):
            return self._json(r_list(self))
        if m == ("POST", "scan"):
            return self._json(r_scan(self, *self._form()))
        if m == ("POST", "import"):
            return self._json(r_import(self, *self._form()))
        if m == ("POST", "practice"):
            p = json.loads(self._body() or b"{}")
            secs = int(p.get("seconds", 0))
            if not 0 < secs <= 6 * 3600:
                raise HTTPError(400, "seconds out of range")
            q("INSERT INTO practice(score_id, started, seconds) VALUES(?,?,?)", (p.get("score_id"), now(), secs))
            return self._json({"ok": True})
        if m == ("GET", "practice", "summary"):
            return self._json(r_practice_summary(self, params))
        if m == ("POST", "downloads"):
            return self._json(r_downloads_add(self, params, self._body()))
        if len(parts) == 2 and method == "GET" and parts[0] == "downloads":
            item = downloads.get(parts[1])
            if not item:
                raise HTTPError(404, "Expired")
            name, mime, body, _ = item
            return self._send(200, body, mime, {"Content-Disposition": f'attachment; filename="{name}"'})
        if len(parts) == 2 and parts[0] == "recordings":
            r = q("SELECT * FROM recordings WHERE id=?", (parts[1],), one=True)
            if not r:
                raise HTTPError(404, "Unknown recording")
            f = score_dir(r["score_id"]) / "recordings" / r["filename"]
            if method == "GET":
                stamp = r["created"][:16].replace(":", "-").replace("T", " ")
                return self._file(f, r["mime"], attach=f"Recording {stamp}.{f.suffix.lstrip('.')}"
                                  if params.get("download") else None)
            if method == "DELETE":
                f.unlink(missing_ok=True)
                q("DELETE FROM recordings WHERE id=?", (parts[1],))
                return self._json({"ok": True})
        if len(parts) >= 2 and parts[0] == "scores":
            sid = parts[1]
            rest = tuple(parts[2:])
            if method == "GET" and rest == ():
                return self._json(get_score(sid))
            if method == "PATCH" and rest == ():
                body = json.loads(self._body() or b"{}")
                update_score(sid, title=(body.get("title") or "").strip()[:120] or "Untitled")
                return self._json(get_score(sid))
            if method == "DELETE" and rest == ():
                shutil.rmtree(score_dir(sid), ignore_errors=True)
                q("DELETE FROM scores WHERE id=?", (sid,))
                q("DELETE FROM recordings WHERE score_id=?", (sid,))
                q("DELETE FROM practice WHERE score_id=?", (sid,))
                return self._json({"ok": True})
            if method == "POST" and rest == ("opened",):
                update_score(sid, last_opened=now())
                return self._json({"ok": True})
            if method == "GET" and rest == ("musicxml",):
                semis = int(params.get("semitones", 0))
                if not -24 <= semis <= 24:
                    raise HTTPError(400, "Transpose by at most two octaves")
                return self._send(200, music.transposed_xml(load_xml(sid), semis).encode(),
                                  "application/vnd.recordare.musicxml+xml")
            if method == "GET" and len(rest) == 2 and rest[0] == "pages":
                return self._file(score_dir(sid) / "pages" / f"page_{int(rest[1]):03d}.png", "image/png")
            if method == "GET" and len(rest) == 2 and rest[0] == "export":
                _, data, mime, name = r_export(self, sid, rest[1], params)
                return self._send(200, data, mime, {"Content-Disposition": f'attachment; filename="{name}"'})
            if rest == ("recordings",):
                if method == "POST":
                    return self._json(r_add_recording(self, sid, *self._form()))
                rows = q("SELECT * FROM recordings WHERE score_id=? ORDER BY created DESC", (sid,))
                return self._json([dict(r) for r in rows])
        raise HTTPError(404, f"No route for {method} {path}")


# -------------------------------------------------------------------- start --
def start(data_dir, www_dir, gallery_dir, models_dir, bridge, platform, token, port: int = 0) -> int:
    """Start the server on a background thread. Returns the port.

    Safe to call again (e.g. the activity was recreated): the running server is
    reused and only the launch secret changes."""
    with _start_lock:
        if S.get("httpd"):
            S["tokens"].add(str(token))
            return S["httpd"].server_address[1]
        return _start(data_dir, www_dir, gallery_dir, models_dir, bridge, platform, token, port)


_start_lock = threading.Lock()


def _start(data_dir, www_dir, gallery_dir, models_dir, bridge, platform, token, port) -> int:
    data = Path(str(data_dir))
    S.update(data=data, scores=data / "scores", db=str(data / "practice.db"), www=Path(str(www_dir)),
             gallery=Path(str(gallery_dir)), models=Path(str(models_dir)), bridge=bridge,
             platform=platform, tokens={str(token)})
    S["scores"].mkdir(parents=True, exist_ok=True)
    init_db()
    seed_gallery()
    threading.Thread(target=scan_worker, name="scan", daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", int(port)), Handler)
    httpd.daemon_threads = True
    S["httpd"] = httpd
    threading.Thread(target=httpd.serve_forever, name="http", daemon=True).start()
    return httpd.server_address[1]


def native_check() -> str:
    """Import every native module the app needs; raise naming the first that fails."""
    import importlib
    found = []
    for mod in ("numpy", "cv2", "PIL.Image", "music21", "music"):
        try:
            m = importlib.import_module(mod)
        except Exception as e:
            raise RuntimeError(f"import {mod} failed: {e}") from e
        v = getattr(m, "__version__", None) or getattr(m, "VERSION", None) or ""
        found.append(f"{mod}={v}" if v else mod)
    return " ".join(found)


def warm_up() -> None:
    """Import the heavy modules ahead of first use (called in the background)."""
    try:
        print("Practice Room imports:", native_check(), flush=True)
    except Exception:
        traceback.print_exc()


# ---------------------------------------------------------------- self-test --
def selftest(image_path: str, token: str) -> str:
    """End-to-end check through the real HTTP API (used by CI on an emulator).

    Scans a known page (Ode to Joy, 30 notes), checks the recognised notes and
    exports MIDI. Raises on any failure, returns a one-line report on success.
    """
    import urllib.request
    import music21 as m21

    base = f"http://127.0.0.1:{S['httpd'].server_address[1]}"
    hdr = {"Cookie": f"pr={token}"}

    def call(method, path, body=None, ctype=None):
        req = urllib.request.Request(base + path, data=body, method=method,
                                     headers={**hdr, **({"Content-Type": ctype} if ctype else {})})
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.read(), r.headers.get("Content-Type", "")

    versions = native_check()
    t0 = time.time()
    boundary = uuid.uuid4().hex
    img = Path(image_path).read_bytes()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nSelf-test\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"ode.png\"\r\n"
            f"Content-Type: image/png\r\n\r\n").encode() + img + f"\r\n--{boundary}--\r\n".encode()
    sid = json.loads(call("POST", "/api/scan", body, f"multipart/form-data; boundary={boundary}")[0])["id"]
    while True:
        st = json.loads(call("GET", f"/api/scores/{sid}")[0])
        if st["status"] in ("ready", "failed"):
            break
        if time.time() - t0 > 900:
            raise RuntimeError("scan timed out")
        time.sleep(1)
    if st["status"] != "ready":
        raise RuntimeError(f"scan failed: {st['error']}")
    scan_s = time.time() - t0

    xml = call("GET", f"/api/scores/{sid}/musicxml")[0].decode()
    notes = [n.nameWithOctave for n in m21.converter.parseData(xml, format="musicxml").flatten().notes]
    expected = "E4 E4 F4 G4 G4 F4 E4 D4 C4 C4 D4 E4 E4 D4 D4".split()
    if notes[:15] != expected:
        raise RuntimeError(f"wrong notes: {notes[:15]}")

    midi = call("GET", f"/api/scores/{sid}/export/midi?semitones=2&bpm=90")[0]
    if not midi.startswith(b"MThd"):
        raise RuntimeError("MIDI export is not a MIDI file")
    tr = call("GET", f"/api/scores/{sid}/musicxml?semitones=-2")[0].decode()
    if "<fifths>-2</fifths>" not in tr:  # C down a tone = B-flat major
        raise RuntimeError("transposition didn't change the key")
    lst = json.loads(call("GET", "/api/scores")[0])
    return (f"{versions} notes={len(notes)} first15=ok scan_seconds={scan_s:.1f} midi_bytes={len(midi)} "
            f"library={len(lst)} live_tensors={S['bridge'].liveTensors() if hasattr(S['bridge'], 'liveTensors') else '?'}")
