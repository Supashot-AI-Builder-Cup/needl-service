#!/usr/bin/env bash
# Run the test suite against the Firestore emulator.
# Uses a running emulator on FIRESTORE_EMULATOR_HOST (default 127.0.0.1:8085), or starts
# one for the duration of the run. Extra arguments go to pytest: scripts/test.sh -k repo -x
set -euo pipefail

cd "$(dirname "$0")/.."
HOST="${FIRESTORE_EMULATOR_HOST:-127.0.0.1:8085}"
PYTHON="${PYTHON:-.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3

started=0
log_file=""

cleanup() {
  if [ "$started" -eq 1 ]; then
    curl -s -X POST "http://$HOST/shutdown" >/dev/null 2>&1 || true
    wait 2>/dev/null || true
    rm -f "$log_file"
  fi
}
trap cleanup EXIT

if ! curl -fsS "http://$HOST/" >/dev/null 2>&1; then
  log_file="$(mktemp "${TMPDIR:-/tmp}/needl-firestore-emulator.XXXXXX")"
  echo "Starting Firestore emulator on $HOST (log: $log_file)"
  FIRESTORE_EMULATOR_HOST="$HOST" scripts/firestore_emulator.sh >"$log_file" 2>&1 &
  started=1
  # Up to 60 s: the first Docker run may still pull the image (about 1 GB).
  for _ in $(seq 1 120); do
    if curl -fsS "http://$HOST/" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
  if ! curl -fsS "http://$HOST/" >/dev/null 2>&1; then
    echo "The Firestore emulator did not start within 60 seconds:" >&2
    cat "$log_file" >&2
    exit 1
  fi
fi

export FIRESTORE_EMULATOR_HOST="$HOST"
"$PYTHON" -m pytest "$@"
