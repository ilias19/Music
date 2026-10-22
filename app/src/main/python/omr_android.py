"""Run homr (vendored in ./homr) on the phone.

Call `setup(bridge, models_dir)` once, then `scan_page(png_path)` per page.
It returns the path of the MusicXML homr wrote next to the image.

Four things adapt homr 0.7 to Chaquopy's Python 3.10 / OpenCV 4.5.1 stack:
  * cv2.typing doesn't exist before OpenCV 4.8 (homr only uses it in type hints)
  * OpenCV 4.5.1 rejects numpy arrays as sizes (cv2.resize(img, np.array([w, h])))
  * title OCR (rapidocr) isn't shipped: the user names scores themselves
  * onnxruntime is replaced by ort_shim, backed by the native Android runtime
"""
from __future__ import annotations

import os
import sys
import threading
import types
from concurrent.futures import Future

import cv2

_ready = False
_lock = threading.Lock()
_geometry: dict = {}
_model_paths: list = []

SEGNET = "segnet_308-3296ccd40960f90ca6ab9c035cca945675d30a0f.onnx"
ENCODER = "encoder_pytorch_model_396-f6feedb42ff90087d898b0941a55d040fa6b2903.onnx"
DECODER = "decoder_pytorch_model_396-f6feedb42ff90087d898b0941a55d040fa6b2903.onnx"


def _as_size(v):
    """OpenCV < 4.5.5 only accepts a plain tuple of ints as a size (homr passes numpy arrays)."""
    if hasattr(v, "tolist") or isinstance(v, list):
        v = v.tolist() if hasattr(v, "tolist") else v
        return tuple(int(x) for x in v)
    return v


def _wrap_dsize(name: str, position: int) -> None:
    orig = getattr(cv2, name)
    if getattr(orig, "_pr_wrapped", False):
        return

    def wrapper(*args, **kwargs):
        if len(args) > position:
            args = list(args)
            args[position] = _as_size(args[position])
        if "dsize" in kwargs:
            kwargs["dsize"] = _as_size(kwargs["dsize"])
        return orig(*args, **kwargs)

    wrapper._pr_wrapped = True
    setattr(cv2, name, wrapper)


def _compat() -> None:
    # Chaquopy ships OpenCV 4.5.1: sizes must be tuples, not numpy arrays
    _wrap_dsize("resize", 1)
    _wrap_dsize("warpAffine", 2)
    _wrap_dsize("warpPerspective", 2)

    if "cv2.typing" not in sys.modules:
        stub = types.ModuleType("cv2.typing")
        stub.__getattr__ = lambda name: object
        sys.modules["cv2.typing"] = stub
        cv2.typing = stub

    title = types.ModuleType("homr.title_detection")

    def detect_title(debug, top_staff):
        f: Future = Future()
        f.set_result("")
        return f

    title.detect_title = detect_title
    title.download_ocr_weights = lambda *a, **k: None
    sys.modules["homr.title_detection"] = title


def setup(bridge, models_dir: str) -> None:
    """Install the shims and point homr at the model files in models_dir."""
    global _ready
    with _lock:
        if _ready:
            return
        _compat()
        import ort_shim
        ort_shim.install(bridge)

        for name in (SEGNET, ENCODER, DECODER):
            p = os.path.join(models_dir, name)
            if not os.path.exists(p):
                raise FileNotFoundError(f"Model missing: {p}")

        import homr.segmentation.config as seg_cfg
        import homr.transformer.configs as tr_cfg
        seg_path = os.path.join(models_dir, SEGNET)
        _model_paths[:] = [seg_path, os.path.join(models_dir, ENCODER), os.path.join(models_dir, DECODER)]
        seg_cfg.segnet_path_onnx = seg_path

        orig_init = tr_cfg.FilePaths.__init__

        def init(self) -> None:
            orig_init(self)
            self.encoder_path = os.path.join(models_dir, ENCODER)
            self.decoder_path = os.path.join(models_dir, DECODER)

        tr_cfg.FilePaths.__init__ = init
        tr_cfg.default_config = tr_cfg.Config() if hasattr(tr_cfg, "default_config") else None

        import homr.segmentation.inference_segnet as seg_inf
        seg_inf.segnet_path_onnx = seg_path
        import homr.main as hm
        hm.segnet_path_onnx = seg_path

        # keep homr's staff/notehead geometry of the page being scanned (for lyrics placement)
        orig_parse = hm.parse_staffs

        def parse_staffs(debug, staffs, image, config, selected_staff=-1):
            _geometry["staffs"] = staffs
            _geometry["width"] = int(image.shape[1])
            return orig_parse(debug, staffs, image, config, selected_staff)

        hm.parse_staffs = parse_staffs
        _ready = True


def preload() -> None:
    """Load the three models into the native runtime now (it caches them by path), and
    import homr's modules, so the first scan doesn't wait for either."""
    import homr.main  # noqa: F401
    import homr.transformer.staff2score  # noqa: F401
    import numpy as np
    import ort_shim
    for path in _model_paths:
        ort_shim.preload(path)
    # one tiny run each: the runtime sets up its memory and kernels on the first call
    seg, enc = ort_shim.InferenceSession(_model_paths[0]), ort_shim.InferenceSession(_model_paths[1])
    seg.run(None, {seg.get_inputs()[0].name: np.full((1, 3, 320, 320), 255, np.float32)})
    enc.run(None, {enc.get_inputs()[0].name: np.zeros((1, 1, 256, 1280), np.float32)})


def scan_page(png_path: str, ocr=None) -> str:
    """Recognise one page image. Returns the MusicXML path.

    ocr(png_path) -> list of lyrics.Word, if given, adds lyrics, chord symbols and tempo."""
    if not _ready:
        raise RuntimeError("omr_android.setup() was not called")
    from homr.main import ProcessingConfig, process_image
    from homr.music_xml_generator import XmlGeneratorArguments

    config = ProcessingConfig(False, False, False, False, -1, False, False, False)
    words: dict = {}
    reader = None
    if ocr is not None:
        # read the page's text on another thread while homr reads the notes (ML Kit runs natively)
        def read_text():
            try:
                words["w"] = ocr(png_path)
            except Exception:
                import traceback
                traceback.print_exc()
        reader = threading.Thread(target=read_text, name="ocr", daemon=True)
        reader.start()
    with _lock:  # one scan at a time: the models are big
        _geometry.clear()
        process_image(png_path, config, XmlGeneratorArguments(False, None, None))
        xml = os.path.splitext(png_path)[0] + ".musicxml"
        if not os.path.exists(xml):
            raise RuntimeError("No music found on this page")
        if ocr is not None and _geometry.get("staffs"):
            try:
                import lyrics
                from PIL import Image
                reader.join()
                stats = lyrics.annotate(xml, _geometry["staffs"], _geometry["width"], Image.open(png_path).size[0], words.get("w") or [])
                print("text on page:", stats, flush=True)
            except Exception:
                import traceback
                traceback.print_exc()   # notes are still fine without the text
    return xml
