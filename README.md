# Practice Room for Android

Scan sheet music with your phone's camera, then play it back, loop it, slow it down, transpose it,
record yourself over it and export it. Everything runs **on the phone**: no server, no account,
works offline.

## Install

1. Open the latest release (**Releases** on the repo page) on your phone and download
   `PracticeRoom-…-phone.apk`.
2. Open it. Android asks you to allow installs from your browser or Files app the first time. Allow it, then install.
3. The first launch takes a few seconds while the recognition models unpack.

Requires Android 10 or newer and a 64-bit phone (any phone from the last ~6 years). The APK is about 230 MB.
Updates install over the old version and keep your scores, as long as they're signed with the same key (see *Signing*).

## What's inside

| Part | How |
|---|---|
| UI | The Practice Room web app (`app/src/main/assets/www`) in a WebView: OpenSheetMusicDisplay renders the score, Tone.js plays it with bundled Salamander piano samples, jsPDF + svg2pdf make the PDF |
| Backend | `app/src/main/python/server.py`: the same HTTP API as the desktop version, on the Python standard library, listening on `127.0.0.1` with a per-launch secret cookie |
| Python | [Chaquopy](https://chaquo.com/chaquopy/) 17 with Python 3.10, numpy, OpenCV 4.5, Pillow, music21 9.9 |
| Scanner | [homr](https://github.com/liebharc/homr) 0.7, vendored in `python/homr` unchanged. `omr_android.py` adapts it: no title OCR, and a shim for `cv2.typing` |
| Neural nets | `ort_shim.py` stands in for the `onnxruntime` Python package and forwards to `OrtBridge.kt`, which runs Microsoft's native **onnxruntime-android**. Tensors stay native, so the decoder's KV cache is never copied through Python |
| PDF import | Android's `PdfRenderer` (`PlatformBridge.kt`) |
| Files | Exports and recordings save to **Downloads/Practice Room**. Camera and file picker via the system pickers |

## Build

CI does everything (`.github/workflows/build.yml`) on each push to `main`:

1. Downloads the models (157 MB) and piano samples (`tools/fetch_assets.sh`).
2. Builds a signed release APK for arm64 phones and one for x86_64.
3. **Boots an Android emulator, installs the x86_64 APK, and scans a real page through the app's own HTTP API.**
   This checks that the 15 notes recognised match Ode to Joy, that MIDI export works and that transposition changes the key
   (`tools/emulator_selftest.sh`, `server.selftest`). The screenshot and logcat are kept as the `selftest-output` artifact.
4. Only if that passes, publishes a GitHub Release with the phone APK.

Locally: `bash tools/fetch_assets.sh && ./gradlew assembleRelease`. This needs the Android SDK, JDK 17
and Python 3.10 on PATH (or `PR_BUILD_PYTHON=/path/to/python3.10`).

### Test the backend without a phone

```bash
python3.10 -m pip install numpy==1.26.4 opencv-python-headless==4.5.5.64 pillow onnxruntime==1.22.0 pymupdf \
    music21==9.9.2 --no-deps musicxml==1.4 jsonpickle more-itertools webcolors joblib chardet requests
python3.10 tools/dev_server.py --models app/src/main/assets/models
```

Open the printed URL. It's the exact phone backend, with desktop onnxruntime standing in for the native bridge.

### Signing

CI signs with the key in these repository secrets: `PR_KEYSTORE_B64` (base64 `.jks`), `PR_KEYSTORE_PASSWORD`,
`PR_KEY_ALIAS` and `PR_KEY_PASSWORD`. Keep a copy of the keystore somewhere safe. Android only accepts an update signed
with the same key, so if you lose it, you have to uninstall, and that deletes your scores and recordings. Without
the secrets, CI falls back to a throwaway debug key.

## Accuracy and speed

Same recognition as the desktop version: 30/30 notes on a clean melody, 30/30 on a blurred and rotated phone-style photo,
165/169 on a 4-voice Bach chorale. On a 2-core desktop CPU a page takes 4–17 s. Phones are usually similar or faster.
The emulator self-test logs the time for the device it ran on.

## Licences

homr is **AGPL-3.0** (`python/homr/LICENSE`). That's fine for your own phone, but if you distribute the app to others you must
offer them the source. OpenSheetMusicDisplay BSD-3, Tone.js MIT, jsPDF MIT, svg2pdf.js MIT, music21 BSD,
Chaquopy MIT, ONNX Runtime MIT, Fraunces/Inter OFL-1.1, Salamander Grand Piano samples CC-BY 3.0 (Alexander Holm).
