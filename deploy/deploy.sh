#!/usr/bin/env bash
# Build from source with Cloud Build and deploy needl-service to Cloud Run.
#
#   PROJECT_ID=my-project deploy/deploy.sh
#
# Optional: REGION (asia-southeast2), SERVICE (needl-service), SA_NAME (needl-service),
# ENV_FILE (deploy/env.yaml), SECRETS ("GEMINI_API_KEY APIFY_TOKEN RAPIDAPI_KEY").
set -euo pipefail
cd "$(dirname "$0")/.."

: "${PROJECT_ID:?Set PROJECT_ID, for example: PROJECT_ID=my-project deploy/deploy.sh}"
REGION="${REGION:-asia-southeast2}"
SERVICE="${SERVICE:-needl-service}"
SA_NAME="${SA_NAME:-needl-service}"
ENV_FILE="${ENV_FILE:-deploy/env.yaml}"
SECRETS="${SECRETS:-GEMINI_API_KEY APIFY_TOKEN RAPIDAPI_KEY}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

gc() { gcloud --project "$PROJECT_ID" "$@"; }

# Value of KEY: "value" in the env file (empty when missing).
env_value() {
  { grep -E "^$1:" "$ENV_FILE" || true; } | head -n 1 | sed -E 's/^[^:]+:[[:space:]]*"?([^"]*)"?[[:space:]]*$/\1/'
}

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE. Create it: cp deploy/env.example.yaml $ENV_FILE, then edit it." >&2
  exit 1
fi
if grep -qE 'your-project-id|your-ui\.example\.com' "$ENV_FILE"; then
  echo "$ENV_FILE still has placeholder values (your-project-id / your-ui.example.com)." >&2
  exit 1
fi
env_project="$(env_value GOOGLE_CLOUD_PROJECT)"
if [ -n "$env_project" ] && [ "$env_project" != "$PROJECT_ID" ]; then
  echo "GOOGLE_CLOUD_PROJECT in $ENV_FILE is $env_project, but PROJECT_ID is $PROJECT_ID." >&2
  exit 1
fi

# Cloud Run, Cloud Build and Artifact Registry cannot be used without an open billing account.
billing="$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)' 2>/dev/null || true)"
if [ "$(printf '%s' "$billing" | tr '[:upper:]' '[:lower:]')" != "true" ]; then
  echo "Billing is not enabled for $PROJECT_ID (or its billing account is closed)." >&2
  echo "Check with: gcloud billing accounts list   (OPEN must be True), then run deploy/setup.sh." >&2
  exit 1
fi
if [ -z "$(gc services list --enabled --filter='config.name=run.googleapis.com' --format='value(config.name)' 2>/dev/null)" ]; then
  echo "Cloud Run is not enabled on $PROJECT_ID. Run deploy/setup.sh first." >&2
  exit 1
fi

# Only secrets with an enabled version can be mounted.
secret_flags=""
for name in $SECRETS; do
  if [ -n "$(gc secrets versions list "$name" --filter=state=enabled --limit=1 --format='value(name)' 2>/dev/null)" ]; then
    secret_flags="${secret_flags:+$secret_flags,}$name=$name:latest"
  else
    echo "Note: secret $name has no enabled version; deploying without it."
  fi
done

args=(
  run deploy "$SERVICE"
  --source .
  --region "$REGION"
  --service-account "$SA_EMAIL"
  --allow-unauthenticated
  --max-instances 1
  --min-instances 0
  --no-cpu-throttling
  --cpu 1
  --memory 1Gi
  --concurrency 80
  --timeout 300
  --env-vars-file "$ENV_FILE"
)
if [ -n "$secret_flags" ]; then
  args+=(--set-secrets "$secret_flags")
fi

echo "Deploying $SERVICE to $REGION in $PROJECT_ID (single instance, CPU always allocated)"
gc "${args[@]}"

url="$(gc run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
cat <<EOF

Deployed: $url
- Check it:   PROJECT_ID=$PROJECT_ID REGION=$REGION deploy/smoke.sh
- needl-ui:   build with VITE_API_URL=$url
- CORS:       CORS_ORIGINS in $ENV_FILE is "$(env_value CORS_ORIGINS)"; it must contain the UI origin.
EOF
