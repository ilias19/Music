#!/usr/bin/env bash
# Run the full end-to-end suite inside the installed app's WebView (see tests/e2e/run_e2e.py).
# Results, screenshots and logcat go to $1/e2e.
set -uo pipefail
OUT="${1:-selftest-output}/e2e"
mkdir -p "$OUT"
adb logcat -c
python tests/e2e/run_e2e.py --android 2>&1 | tee "$OUT/run.txt"
rc=${PIPESTATUS[0]}
cp -r tests/e2e/out/. "$OUT/" 2>/dev/null || true
adb logcat -d > "$OUT/logcat.txt"
exit $rc
