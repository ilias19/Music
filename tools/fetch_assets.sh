#!/usr/bin/env bash
# Download the large binary assets that aren't kept in git:
#   - homr's three recognition models (~157 MB, AGPL-3.0, github.com/liebharc/homr)
#   - Salamander Grand Piano samples (CC-BY 3.0, Alexander Holm, via tonejs.github.io)
#   - FluidR3 GM choir and voice samples (CC-BY 3.0, Frank Wen, via gleitz/midi-js-soundfonts)
# Safe to re-run: files already present are skipped.
set -euo pipefail
cd "$(dirname "$0")/.."

MODELS=app/src/main/assets/models
SAMPLES=app/src/main/assets/www/samples
mkdir -p "$MODELS" "$SAMPLES"

HOMR=https://github.com/liebharc/homr/releases/download/onnx_checkpoints
for base in \
  segnet_308-3296ccd40960f90ca6ab9c035cca945675d30a0f \
  encoder_pytorch_model_396-f6feedb42ff90087d898b0941a55d040fa6b2903 \
  decoder_pytorch_model_396-f6feedb42ff90087d898b0941a55d040fa6b2903; do
  if [ ! -s "$MODELS/$base.onnx" ]; then
    echo "Downloading model $base"
    tmp=$(mktemp -d)
    curl -fsSL --retry 3 -o "$tmp/m.zip" "$HOMR/$base.zip"
    unzip -q -o "$tmp/m.zip" -d "$tmp"
    find "$tmp" -name "$base.onnx" -exec mv {} "$MODELS/" \;
    rm -rf "$tmp"
    test -s "$MODELS/$base.onnx" || { echo "Model $base missing after download"; exit 1; }
  fi
done

SALAMANDER=https://tonejs.github.io/audio/salamander
for n in A0 C1 Ds1 Fs1 A1 C2 Ds2 Fs2 A2 C3 Ds3 Fs3 A3 C4 Ds4 Fs4 A4 C5 Ds5 Fs5 A5 C6 Ds6 Fs6 A6 C7 Ds7 Fs7 A7 C8; do
  [ -s "$SAMPLES/$n.mp3" ] || curl -fsSL --retry 3 -o "$SAMPLES/$n.mp3" "$SALAMANDER/$n.mp3"
done

# Sung voices for the Mixer: "Choir Aahs" and "Voice Oohs" from the FluidR3 GM SoundFont
# (Frank Wen, CC-BY 3.0), as rendered by github.com/gleitz/midi-js-soundfonts
GLEITZ=https://raw.githubusercontent.com/gleitz/midi-js-soundfonts/gh-pages/FluidR3_GM
for inst in choir_aahs voice_oohs; do
  mkdir -p "$SAMPLES/$inst"
  for n in A2 C3 Eb3 Gb3 A3 C4 Eb4 Gb4 A4 C5 Eb5 Gb5 A5 C6; do
    [ -s "$SAMPLES/$inst/$n.mp3" ] || curl -fsSL --retry 3 -o "$SAMPLES/$inst/$n.mp3" "$GLEITZ/$inst-mp3/$n.mp3"
  done
done

du -sh "$MODELS" "$SAMPLES"
