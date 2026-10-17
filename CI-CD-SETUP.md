# CI/CD setup

`.github/workflows/ci-cd.yml` runs tests for pull requests targeting `main`.
Pushes to `main` deploy to Cloud Run after tests pass, then run a smoke check.
GitHub authenticates to GCP with Workload Identity Federation; no service
account key is stored in GitHub.

## 1. Prepare GCP

Use a GCP project with billing enabled. Authenticate locally with an account
that can create service accounts, enable APIs, set IAM policies, and configure
Cloud Run and Firestore:

```bash
gcloud auth login
PROJECT_ID=my-project deploy/setup.sh
```

`deploy/setup.sh` enables required APIs, prepares Firestore and its indexes,
creates the runtime service account, and prompts for secret values. Provide at
least `GEMINI_API_KEY` and one Shopee provider credential (`APIFY_TOKEN`, or
`RAPIDAPI_KEY` with its endpoint configuration). The deployed configuration
uses `NEEDL_SOURCE=live` and `NEEDL_ANALYZER=real` by default, so Gemini and
Shopee credentials are required for a healthy service.

## 2. Create GitHub deploy identity

Set these values. `ORG_REPO` is the exact GitHub owner/repository, for example
`acme/needl-service`:

```bash
PROJECT_ID=my-project
ORG_REPO=my-org/needl-service
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
CI_SA="needl-github-deploy@$PROJECT_ID.iam.gserviceaccount.com"
RUNTIME_SA="needl-service@$PROJECT_ID.iam.gserviceaccount.com"

gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  --project "$PROJECT_ID"
gcloud iam service-accounts create needl-github-deploy --project "$PROJECT_ID"

for ROLE in roles/run.admin roles/run.sourceDeveloper roles/serviceusage.serviceUsageConsumer roles/secretmanager.viewer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$CI_SA" --role="$ROLE"
done
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" --project "$PROJECT_ID" \
  --member="serviceAccount:$CI_SA" --role=roles/iam.serviceAccountUser

gcloud iam workload-identity-pools create github --project "$PROJECT_ID" \
  --location=global --display-name=GitHub-Actions
gcloud iam workload-identity-pools providers create-oidc github \
  --project "$PROJECT_ID" --location=global --workload-identity-pool=github \
  --display-name=GitHub --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='$ORG_REPO' && assertion.ref=='refs/heads/main'"
gcloud iam service-accounts add-iam-policy-binding "$CI_SA" --project "$PROJECT_ID" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$ORG_REPO"
```

These create commands are one-time. If the service account, pool, or provider
already exists, inspect and reuse it. Keep the provider condition restricted to
this repository and the `main` ref; do not remove it to resolve authentication
errors.

## 3. Configure GitHub Actions

In repository **Settings → Secrets and variables → Actions**, add:

| Type | Name | Value |
|---|---|---|
| Variable | `GCP_PROJECT_ID` | GCP project ID from step 1 |
| Variable | `CORS_ORIGINS` | Deployed UI origin, e.g. `https://app.example.com` |
| Secret | `GCP_WIF_PROVIDER` | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github` |
| Secret | `GCP_SERVICE_ACCOUNT` | `needl-github-deploy@PROJECT_ID.iam.gserviceaccount.com` |

`CORS_ORIGINS` must be non-empty. Use the origin only: no path or trailing
slash. Runtime API keys belong in Secret Manager, never in GitHub variables or
workflow files.

## 4. Set runtime environment

Copy and edit `deploy/env.example.yaml` locally as `deploy/env.yaml` for manual
deploys. CI creates this file from the example and substitutes `GCP_PROJECT_ID`
and `CORS_ORIGINS`. Keep `NEEDL_SOURCE: "live"` and
`NEEDL_ANALYZER: "real"` for production. The deploy script attaches enabled
Secret Manager versions among `GEMINI_API_KEY`, `APIFY_TOKEN`, and
`RAPIDAPI_KEY`.

The API accepts an Apify provider when `APIFY_TOKEN` is set. RapidAPI also needs
`RAPIDAPI_SHOPEE_DETAIL_URL` (and optionally its reviews URL) in the runtime
environment. The sample env file documents the endpoint template format.

## 5. Require CI before merging

Protect `main` in GitHub branch rules and require the `test` check. Do not allow
direct pushes if all production changes should go through CI. A merged push to
`main` runs tests, authenticates to GCP, builds and deploys the Cloud Run
service, then checks `/health`, CORS, and rate limiting.

## Verify and recover

- PR test failure: open the `test` job logs; deployment will not start.
- Authentication failure: check the two GitHub secrets, project number,
  repository spelling, `main` ref condition, and Workload Identity binding.
- Deploy failure: check IAM roles, billing, enabled secret versions, and
  `GCP_PROJECT_ID` / `CORS_ORIGINS` variables.
- Smoke failure: check Cloud Run logs and confirm the Gemini/Shopee secrets and
  production source/analyzer settings.
- Manual smoke check: `PROJECT_ID=my-project deploy/smoke.sh`.
- Roll back: `gcloud run revisions list --service needl-service --region asia-southeast2`,
  then `gcloud run services update-traffic needl-service --to-revisions REVISION=100 --region asia-southeast2`.
