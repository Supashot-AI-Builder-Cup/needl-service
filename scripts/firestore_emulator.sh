#!/usr/bin/env bash
# Start the Firestore emulator in the foreground (Ctrl+C stops it; data lives in memory).
# Host and port come from FIRESTORE_EMULATOR_HOST, default 127.0.0.1:8085.
#
# Uses the local gcloud CLI (with Java 21+) when available, otherwise Docker with the
# official image. EMULATOR_RUNNER=gcloud or EMULATOR_RUNNER=docker forces one.
set -euo pipefail

HOST="${FIRESTORE_EMULATOR_HOST:-127.0.0.1:8085}"
PORT="${HOST##*:}"
IMAGE="${EMULATOR_IMAGE:-gcr.io/google.com/cloudsdktool/google-cloud-cli:emulators}"
CONTAINER="${EMULATOR_CONTAINER:-needl-firestore-emulator}"

java_major() {
  java -version 2>&1 | head -n 1 | sed -E 's/.*version "([0-9]+).*/\1/'
}

# The emulator in current gcloud releases needs Java 21 or newer.
has_gcloud_emulator() {
  command -v gcloud >/dev/null 2>&1 || return 1
  command -v java >/dev/null 2>&1 || return 1
  local major
  major="$(java_major)"
  case "$major" in '' | *[!0-9]*) return 1 ;; esac
  [ "$major" -ge 21 ]
}

runner="${EMULATOR_RUNNER:-}"
if [ -z "$runner" ]; then
  if has_gcloud_emulator; then
    runner=gcloud
  elif command -v docker >/dev/null 2>&1; then
    runner=docker
  else
    echo "Neither gcloud with Java 21+ nor Docker found. Either:" >&2
    echo "  brew install --cask google-cloud-sdk && gcloud components install cloud-firestore-emulator" >&2
    echo "  brew install openjdk@21      # the emulator needs Java 21 or newer" >&2
    echo "or install Docker Desktop and run this script again." >&2
    exit 1
  fi
fi

echo "Firestore emulator on $HOST via $runner (export FIRESTORE_EMULATOR_HOST=$HOST)"
case "$runner" in
  gcloud)
    exec gcloud emulators firestore start --host-port="$HOST" --quiet
    ;;
  docker)
    if ! docker info >/dev/null 2>&1; then
      echo "Docker is installed but not running. Start Docker Desktop first." >&2
      exit 1
    fi
    # Inside the container the emulator listens on all interfaces; only this machine can reach it.
    exec docker run --rm --name "$CONTAINER" -p "127.0.0.1:$PORT:$PORT" "$IMAGE" \
      gcloud emulators firestore start --host-port="0.0.0.0:$PORT" --quiet
    ;;
  *)
    echo "EMULATOR_RUNNER must be gcloud or docker, not '$runner'." >&2
    exit 1
    ;;
esac
