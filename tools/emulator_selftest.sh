#!/usr/bin/env bash
# Install the APK on a running emulator, launch the app in self-test mode and
# wait for its verdict in logcat (tag PR_SELFTEST). Exit 0 only on PASS.
# The self-test scans a bundled page through the app's own HTTP API, checks the
# recognised notes, exports MIDI and transposes (python/server.py: selftest).
set -uo pipefail
APK="$1"
OUT="${2:-selftest-output}"
PKG=com.ilias.practiceroom
mkdir -p "$OUT"

adb install -r "$APK" || exit 1
adb logcat -c
adb shell am start -n "$PKG/.MainActivity" --ez selftest true

verdict=""
for i in $(seq 1 200); do
  sleep 5
  lines=$(adb logcat -d -s PR_SELFTEST:I PR_SELFTEST:E)
  if echo "$lines" | grep -q "PASS"; then verdict=PASS; break; fi
  if echo "$lines" | grep -q "FAIL"; then verdict=FAIL; break; fi
done

sleep 3
adb exec-out screencap -p > "$OUT/screen.png" || true
adb logcat -d > "$OUT/logcat.txt"
echo "----- self-test -----"
adb logcat -d -s PR_SELFTEST:I PR_SELFTEST:E
echo "----- app log (tail) -----"
grep -E "PracticeRoom|python\.std|AndroidRuntime|chaquopy|onnxruntime" "$OUT/logcat.txt" | tail -150

if [ "$verdict" = PASS ]; then echo "SELF-TEST PASSED"; exit 0; fi
echo "SELF-TEST ${verdict:-TIMED OUT}"
exit 1
