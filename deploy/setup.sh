#!/usr/bin/env bash
# One-time GCP setup for needl-service on Cloud Run with Firestore. Safe to run again:
# every step checks what exists and skips it.
#
#   PROJECT_ID=my-project deploy/setup.sh
#
# Optional: REGION (asia-southeast2), FIRESTORE_LOCATION (= REGION), SA_NAME (needl-service),
# SECRETS ("GEMINI_API_KEY APIFY_TOKEN RAPIDAPI_KEY").
set -euo pipefail
cd "$(dirname "$0")/.."

: "${PROJECT_ID:?Set PROJECT_ID, for example: PROJECT_ID=my-project deploy/setup.sh}"
REGION="${REGION:-asia-southeast2}"
FIRESTORE_LOCATION="${FIRESTORE_LOCATION:-$REGION}"
SA_NAME="${SA_NAME:-needl-service}"
SECRETS="${SECRETS:-GEMINI_API_KEY APIFY_TOKEN RAPIDAPI_KEY}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

step() { printf '\n==> %s\n' "$*"; }
gc() { gcloud --project "$PROJECT_ID" "$@"; }
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

# Retries a command a few times: new service accounts take a moment to become usable.
retry() {
  local n
  for n in 1 2 3 4 5; do
    if "$@"; then return 0; fi
    echo "  (retrying in $((n * 3)) s)"
    sleep $((n * 3))
  done
  return 1
}

bind_project_role() {
  gc projects add-iam-policy-binding "$PROJECT_ID" --member="$1" --role="$2" --condition=None --quiet >/dev/null
}

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud CLI not found. Install it: brew install --cask google-cloud-sdk" >&2
  exit 1
fi

step "1/9 Account, project and billing"
account="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -n 1)"
if [ -z "$account" ]; then
  echo "No active gcloud account. Run: gcloud auth login" >&2
  exit 1
fi
echo "Account: $account"
if ! gcloud projects describe "$PROJECT_ID" --format='value(projectId)' >/dev/null 2>&1; then
  echo "Project $PROJECT_ID does not exist or $account cannot see it." >&2
  exit 1
fi
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
billing="$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)' 2>/dev/null || true)"
if [ "$(lower "$billing")" != "true" ]; then
  echo "Billing is not enabled for $PROJECT_ID. Link a billing account in the console first." >&2
  exit 1
fi
echo "Project $PROJECT_ID (#$PROJECT_NUMBER), billing enabled"

step "2/9 Enabling APIs (Cloud Run, Cloud Build, Artifact Registry, Firestore, Secret Manager, IAM)"
gc services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com secretmanager.googleapis.com iam.googleapis.com

step "3/9 Firestore database (default), Native mode, in $FIRESTORE_LOCATION"
if gc firestore databases describe --database="(default)" >/dev/null 2>&1; then
  db_type="$(gc firestore databases describe --database="(default)" --format='value(type)')"
  db_location="$(gc firestore databases describe --database="(default)" --format='value(locationId)')"
  echo "Already exists: type $db_type, location $db_location"
  if [ "$db_type" != "FIRESTORE_NATIVE" ]; then
    echo "needl-service needs a Firestore Native database; this one is $db_type." >&2
    exit 1
  fi
else
  echo "The database location cannot be changed later."
  printf 'Create the (default) Firestore database in %s? [y/N] ' "$FIRESTORE_LOCATION"
  read -r answer
  case "$(lower "$answer")" in
    y | yes) ;;
    *)
      echo "Stopped. Set FIRESTORE_LOCATION to choose another location."
      exit 1
      ;;
  esac
  gc firestore databases create --database="(default)" --location="$FIRESTORE_LOCATION" --type=firestore-native
fi

step "4/9 TTL policy: idempotency_keys.expires_at"
if gc firestore fields ttls list --collection-group=idempotency_keys --format='value(name)' 2>/dev/null | grep -q 'expires_at'; then
  echo "Already configured"
else
  gc firestore fields ttls update expires_at --collection-group=idempotency_keys --enable-ttl --async
  echo "Requested; it takes 10 minutes or more to become active (gcloud firestore fields ttls list)."
fi

step "5/9 Index exemption: verifications.result_json (large JSON string, never queried)"
gc firestore indexes fields update result_json --collection-group=verifications --disable-indexes --async
echo "Requested (running this again is harmless)."

step "6/9 Runtime service account $SA_EMAIL with roles/datastore.user"
if gc iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1; then
  echo "Service account exists"
else
  gc iam service-accounts create "$SA_NAME" --display-name="needl-service runtime"
fi
retry bind_project_role "serviceAccount:$SA_EMAIL" roles/datastore.user
echo "roles/datastore.user granted"

step "7/9 roles/run.builder for the Cloud Build service account (deploy from source)"
retry bind_project_role "serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" roles/run.builder
echo "roles/run.builder granted to ${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

step "8/9 Secrets in Secret Manager: $SECRETS"
for name in $SECRETS; do
  if gc secrets describe "$name" >/dev/null 2>&1; then
    echo "$name exists"
  else
    gc secrets create "$name" --replication-policy=automatic >/dev/null
    echo "$name created"
  fi
  if [ -t 0 ]; then
    printf 'New value for %s (hidden; press Enter to keep the current one or skip): ' "$name"
    value=""
    read -r -s value
    echo
    if [ -n "$value" ]; then
      printf '%s' "$value" | gc secrets versions add "$name" --data-file=- >/dev/null
      echo "  new version of $name added"
    fi
    unset value
  fi
  gc secrets add-iam-policy-binding "$name" --member="serviceAccount:$SA_EMAIL" \
    --role=roles/secretmanager.secretAccessor --condition=None --quiet >/dev/null
  echo "  $SA_EMAIL can read $name"
done

step "9/9 Next steps"
cat <<EOF
1. cp deploy/env.example.yaml deploy/env.yaml
   Set GOOGLE_CLOUD_PROJECT to $PROJECT_ID and CORS_ORIGINS to your UI origin.
2. PROJECT_ID=$PROJECT_ID REGION=$REGION deploy/deploy.sh
3. PROJECT_ID=$PROJECT_ID REGION=$REGION deploy/smoke.sh
If you are not a project Owner, the deploying account also needs roles/run.sourceDeveloper,
roles/serviceusage.serviceUsageConsumer and roles/iam.serviceAccountUser on $SA_EMAIL.
EOF
