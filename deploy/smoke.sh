#!/usr/bin/env bash
# Check a deployed needl-service.
#
#   PROJECT_ID=my-project deploy/smoke.sh [--origin https://ui.example.com] [--with-job [--link URL]]
#
# Checks /health, CORS for the UI origin, and that the rate limit uses the client IP added by
# Cloud Run (not a spoofed X-Forwarded-For). --with-job also runs one real link check, which
# costs provider credit (Apify). SERVICE_URL skips the lookup with gcloud.
# The rate-limit check uses up this machine's job quota for about a minute, so it runs last.
set -euo pipefail
cd "$(dirname "$0")/.."

REGION="${REGION:-asia-southeast2}"
SERVICE="${SERVICE:-needl-service}"
ENV_FILE="${ENV_FILE:-deploy/env.yaml}"
DEMO_LINK="https://shopee.co.id/Dermalune-10-Niacinamide-Alpha-Arbutin-Brightening-Serum-30ml-Original-i.773019284.25810442671"
JOBS_PATH="/api/v1/verification-jobs"

origin=""
with_job=0
link="${SMOKE_LINK:-$DEMO_LINK}"
while [ $# -gt 0 ]; do
  case "$1" in
    --origin) origin="$2"; shift 2 ;;
    --with-job) with_job=1; shift ;;
    --link) link="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

env_value() {
  [ -f "$ENV_FILE" ] || return 0
  { grep -E "^$1:" "$ENV_FILE" || true; } | head -n 1 | sed -E 's/^[^:]+:[[:space:]]*"?([^"]*)"?[[:space:]]*$/\1/'
}
fail() { echo "FAIL $*" >&2; exit 1; }
pass() { echo "ok   $*"; }
json() { python3 -c "import json, sys; d = json.load(sys.stdin); print($1)"; }

if [ -z "${SERVICE_URL:-}" ]; then
  : "${PROJECT_ID:?Set PROJECT_ID (or SERVICE_URL)}"
  # --quiet: never offer to enable APIs here; that is setup.sh's job.
  SERVICE_URL="$(gcloud --project "$PROJECT_ID" run services describe "$SERVICE" --region "$REGION" \
    --format='value(status.url)' --quiet 2>/dev/null || true)"
  if [ -z "$SERVICE_URL" ]; then
    fail "service $SERVICE not found in $PROJECT_ID ($REGION). Run deploy/setup.sh, then deploy/deploy.sh first."
  fi
fi
url="${SERVICE_URL%/}"
[ -n "$origin" ] || origin="$(env_value CORS_ORIGINS | cut -d, -f1 | tr -d ' ')"
limit="$(env_value RATE_CREATE_PER_MIN)"
limit="${limit:-10}"
echo "Service: $url"

# 1. Health
health="$(curl -fsS "$url/health")" || fail "/health did not answer"
[ "$(echo "$health" | json 'd["status"]')" = "ok" ] || fail "/health status: $health"
[ "$(echo "$health" | json 'd["firestore_emulator"]')" = "False" ] || fail "the service points at a Firestore emulator"
source="$(echo "$health" | json 'd["source"]')"
analyzer="$(echo "$health" | json 'd["analyzer"]')"
[ "$source" = "live" ] || fail "expected NEEDL_SOURCE=live, got $source"
[ "$analyzer" = "real" ] || fail "expected NEEDL_ANALYZER=real, got $analyzer"
[ "$(echo "$health" | json 'd["gemini_configured"]')" = "True" ] || fail "Gemini is not configured"
[ "$(echo "$health" | json 'bool(d["shopee_providers"])')" = "True" ] || fail "no Shopee provider is configured"
pass "/health ok (source=$source, analyzer=$analyzer, providers=$(echo "$health" | json 'd["shopee_providers"]'), gemini configured)"

# 2. CORS preflight from the UI origin
if [ -n "$origin" ]; then
  allowed="$(curl -sS -o /dev/null -D - -X OPTIONS "$url$JOBS_PATH" \
    -H "Origin: $origin" -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: content-type" |
    tr -d '\r' | grep -i '^access-control-allow-origin:' | cut -d' ' -f2- || true)"
  [ "$allowed" = "$origin" ] || fail "CORS: $origin is not allowed (got '${allowed:-nothing}'). Check CORS_ORIGINS."
  pass "CORS allows $origin"
else
  echo "skip CORS (no --origin and no CORS_ORIGINS in $ENV_FILE)"
fi

# 3. Optional real job
if [ "$with_job" -eq 1 ]; then
  created="$(curl -sS -X POST "$url$JOBS_PATH" -H 'Content-Type: application/json' -d "{\"url\": \"$link\"}")"
  job_id="$(echo "$created" | json 'd["data"]["id"]')" || fail "could not create a job: $created"
  echo "     job $job_id created, polling…"
  status="pending"
  for _ in $(seq 1 90); do
    sleep 2
    body="$(curl -sS "$url$JOBS_PATH/$job_id")"
    status="$(echo "$body" | json 'd["data"]["status"]' 2>/dev/null)" || status="unknown"
    case "$status" in completed | failed) break ;; esac
  done
  if [ "$status" = "completed" ]; then
    pass "job completed (risk_level=$(echo "$body" | json 'd["data"]["verification"]["risk_level"]'))"
  else
    fail "job ended as $status: $(echo "$body" | json 'd["data"]["error"]')"
  fi
fi

# 4. Rate limit keyed on the IP Cloud Run adds, not on a spoofed X-Forwarded-For.
# An empty body is rejected (422) but counted, so no job is created.
statuses=""
for i in $(seq 1 $((limit + 1))); do
  code="$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$url$JOBS_PATH" \
    -H 'Content-Type: application/json' -H "X-Forwarded-For: 198.51.100.$((RANDOM % 250)), 192.0.2.$i" -d '{}')"
  statuses="$statuses $code"
done
last="${statuses##* }"
if [ "$last" = "429" ]; then
  pass "rate limit holds against spoofed X-Forwarded-For (statuses:$statuses)"
else
  fail "request $((limit + 1)) was $last, expected 429 (statuses:$statuses). The client IP is not the entry TRUSTED_PROXY_HOPS points at; check TRUST_FORWARDED_FOR / TRUSTED_PROXY_HOPS."
fi

echo "All checks passed."
