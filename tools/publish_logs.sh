#!/usr/bin/env bash
# CI helper: push a folder of logs to the branch ci-logs-<run>-<name> so they
# can be read with plain git (job logs aren't always reachable from tooling).
# Usage: publish_logs.sh <name> <folder>      (needs GITHUB_TOKEN with contents:write)
set -uo pipefail
name="$1"; src="$2"
[ -d "$src" ] || { echo "no logs in $src"; exit 0; }
tmp=$(mktemp -d)
cp -r "$src"/. "$tmp/"
cd "$tmp"
git init -q -b logs
git config user.name "ci-logs"
git config user.email "ci-logs@users.noreply.github.com"
git add -A
git commit -qm "CI logs: run ${GITHUB_RUN_NUMBER} ${name}"
git push -qf "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" \
  "HEAD:refs/heads/ci-logs-${GITHUB_RUN_NUMBER}-${name}" && echo "logs pushed to ci-logs-${GITHUB_RUN_NUMBER}-${name}"
