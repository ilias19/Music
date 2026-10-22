"""Run the app's on-device backend on a computer, for testing without a phone.

    python tools/dev_server.py --models DIR [--data DIR] [--port 8020]

Needs Python 3.10+, the packages in app/build.gradle.kts's pip block, plus
`onnxruntime` (stands in for the native Android runtime) and `pymupdf`
(stands in for Android's PdfRenderer). Prints the URL to open.
"""
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app/src/main/python"))


_OCR_CHILD = """
import json, sys
try:
    from rapidocr import RapidOCR
except ImportError:
    print("[]"); sys.exit()
import logging; logging.disable(logging.CRITICAL)
r = RapidOCR()(sys.argv[1], return_word_box=True)
out = []
for line in (r.word_results or []):
    for text, score, box in line:
        xs = [float(p[0]) for p in box]; ys = [float(p[1]) for p in box]
        out.append({"t": text, "x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys)})
print(json.dumps(out))
"""


class DesktopPlatform:
    def ocr(self, png: str) -> str:
        """RapidOCR stands in for ML Kit on a computer (pip install rapidocr). It runs in a
        child process because this one has the onnxruntime shim in place of the real package."""
        import subprocess
        r = subprocess.run([sys.executable, "-I", "-c", _OCR_CHILD, png], capture_output=True, text=True, timeout=300)
        return r.stdout.strip() or "[]"

    def renderPdf(self, pdf: str, out_dir: str, dpi: int):
        import fitz
        out = []
        for i, page in enumerate(fitz.open(pdf)):
            p = os.path.join(out_dir, f"p{i:03d}.png")
            page.get_pixmap(dpi=dpi).save(p)
            out.append(p)
        return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--data", default=str(ROOT / "build/dev-data"))
    ap.add_argument("--www", default=str(ROOT / "app/src/main/assets/www"))
    ap.add_argument("--gallery", default=str(ROOT / "app/src/main/assets/gallery"))
    ap.add_argument("--port", type=int, default=8020)
    ap.add_argument("--token", default="dev")
    a = ap.parse_args()

    import onnxruntime as real_ort  # must be imported before the shim replaces it
    import ort_shim
    import server

    bridge = ort_shim.MockBridge(real_ort)
    port = server.start(a.data, a.www, a.gallery, a.models, bridge, DesktopPlatform(), a.token, a.port)
    print(f"Practice Room dev server: http://127.0.0.1:{port}/__start?k={a.token}", flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
