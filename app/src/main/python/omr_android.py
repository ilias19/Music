"""Run homr (vendored in ./homr) on the phone.

Call `setup(bridge, models_dir)` once, then `scan_page(png_path)` per page.
It returns the path of the MusicXML homr wrote next to the image.

Three things adapt homr 0.7 to Chaquopy's Python 3.10 / OpenCV 4.5 stack:
  * cv2.typing doesn't exist before OpenCV 4.8 (homr only uses it in type hints)
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

SEGNET = "segnet_308-3296ccd40960f90ca6ab9c035cca945675d30a0f.onnx"
ENCODER = "encoder_pytorch_model_396-f6feedb42ff90087d898b0941a55d040fa6b2903.onnx"
DECODER = "decoder_pytorch_model_396-f6feedb42ff90087d898b0941a55d040fa6b2903.onnx"


def _compat() -> None:
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
        _ready = True


def scan_page(png_path: str) -> str:
    """Recognise one page image. Returns the MusicXML path."""
    if not _ready:
        raise RuntimeError("omr_android.setup() was not called")
    from homr.main import ProcessingConfig, process_image
    from homr.music_xml_generator import XmlGeneratorArguments

    config = ProcessingConfig(False, False, False, False, -1, False, False, False)
    with _lock:  # one scan at a time: the models are big
        process_image(png_path, config, XmlGeneratorArguments(False, None, None))
    xml = os.path.splitext(png_path)[0] + ".musicxml"
    if not os.path.exists(xml):
        raise RuntimeError("No music found on this page")
    return xml
