# needl-service

FastAPI backend for needl. It runs verification jobs for a Shopee listing, from
a product link or from screenshots, and returns the result the needl-ui result
page renders. Pydantic models in `app/contract/` define the API contract. Jobs,
results and idempotency keys are stored in Cloud Firestore; locally and in tests
that is the Firestore emulator. Production runs on Cloud Run (section 10).

Implemented now:

| Endpoint                                      | Purpose                                                   |
| --------------------------------------------- | --------------------------------------------------------- |
| `POST /api/v1/verification-jobs`              | Start a job from a link (JSON) or screenshots (multipart) |
| `GET /api/v1/verification-jobs/{job_id}`      | Poll progress, preview and the final result               |
| `GET /api/v1/verifications`                   | List saved scans with filters and cursor pagination       |
| `GET /api/v1/verifications/{verification_id}` | Read one saved result                                     |
| `GET /health`                                 | Service check                                             |

The analyzer supports sample mode for local development and Gemini analysis for
live listings. History and detail endpoints read saved Firestore results.

---

## 1. Requirements

| Requirement        | Version        | Notes                                                                 |
| ------------------ | -------------- | --------------------------------------------------------------------- |
| Python             | 3.11 or newer  | the contract models use `StrEnum`; tested on 3.13 (image) and 3.14    |
| gcloud CLI         | current        | deploys to Cloud Run; can also run the Firestore emulator             |
| Firestore emulator | -              | either gcloud with Java 21 or newer, or Docker (see below)            |
| Docker             | optional       | runs the emulator without a local Java, and tries the container image |

`scripts/firestore_emulator.sh` picks the emulator runner on its own:

- **gcloud**, when `gcloud` and Java 21 or newer are on `PATH`. On macOS:
  `brew install --cask google-cloud-sdk`, `gcloud components install cloud-firestore-emulator`,
  `brew install openjdk@21` (then follow brew's note to put it on `PATH`).
- **Docker** otherwise, with the official image
  `gcr.io/google.com/cloudsdktool/google-cloud-cli:emulators` (about 1 GB, pulled on first use).
  Docker Desktop must be running.

`EMULATOR_RUNNER=gcloud` or `EMULATOR_RUNNER=docker` forces one.

## 2. Installation

```bash
cd needl-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt     # app + test tools
cp .env.example .env
```

## 3. Configuration (`.env`)

| Variable                                                     | Default                                                                                  | Description                                                                                                                                                                                        |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `NEEDL_SOURCE`                                               | `stub`                                                                                   | `stub`: sample listing, no network. `live`: Shopee data providers for links, Gemini Vision for screenshots                                                                                        |
| `NEEDL_ANALYZER`                                             | `stub`                                                                                   | `stub`: sample checks from `app/analysis/stub_data.json`. `real`: Gemini analysis; requires `NEEDL_SOURCE=live`, `GEMINI_API_KEY`, and a configured Shopee provider                                |
| `STUB_STEP_MS`                                               | `600`                                                                                    | Delay per step in stub mode, so polling looks like a real run                                                                                                                                      |
| `GEMINI_API_KEY`                                             | -                                                                                        | Needed for screenshot extraction and `NEEDL_ANALYZER=real`                                                                                                                                         |
| `GEMINI_MODEL`                                               | `gemini-2.5-flash`                                                                       | Gemini model name                                                                                                                                                                                  |
| `SHOPEE_PROVIDERS`                                           | `apify,rapidapi`                                                                         | Providers to try, in order. One without credentials is skipped                                                                                                                                     |
| `SHOPEE_MAX_REVIEWS`                                         | `100`                                                                                    | Reviews fetched per product                                                                                                                                                                        |
| `APIFY_TOKEN`                                                | -                                                                                        | Apify API token (Settings, API & Integrations in the Apify console)                                                                                                                                |
| `APIFY_DETAIL_ACTOR` / `APIFY_REVIEWS_ACTOR`                 | `zen-studio~shopee-product-detail-scraper` / `zen-studio~shopee-product-reviews-scraper` | Actors used for product data and reviews                                                                                                                                                           |
| `APIFY_TIMEOUT_S`                                            | `120`                                                                                    | Longest wait for one actor run                                                                                                                                                                     |
| `APIFY_MAX_CHARGE_USD`                                       | -                                                                                        | Optional spending cap per actor run                                                                                                                                                                |
| `RAPIDAPI_KEY`                                               | -                                                                                        | RapidAPI key                                                                                                                                                                                       |
| `RAPIDAPI_SHOPEE_DETAIL_URL` / `RAPIDAPI_SHOPEE_REVIEWS_URL` | -                                                                                        | Endpoint templates of the RapidAPI Shopee API, see section 5                                                                                                                                       |
| `CORS_ORIGINS`                                               | `http://localhost:5173`                                                                  | Comma-separated origins allowed to call the API                                                                                                                                                    |
| `GOOGLE_CLOUD_PROJECT`                                       | -                                                                                        | Firestore project. Locally any name (`needl-local`); empty = taken from Application Default Credentials                                                                                            |
| `FIRESTORE_DATABASE`                                         | `(default)`                                                                              | Firestore database ID                                                                                                                                                                              |
| `FIRESTORE_EMULATOR_HOST`                                    | -                                                                                        | `127.0.0.1:8085` locally. When set, the client talks to the emulator, without credentials                                                                                                          |
| `UPLOAD_DIR`                                                 | `data/uploads`                                                                           | Screenshots, deleted when their job finishes                                                                                                                                                       |
| `MAX_UPLOAD_TOTAL_MB`                                        | `30`                                                                                     | All screenshots of one request together, 1 to 30. Cloud Run rejects HTTP/1 requests over 32 MiB before they reach the app (without CORS headers), so 30 MiB plus multipart overhead stays under it |
| `WORKER_COUNT`                                               | `2`                                                                                      | Jobs processed at the same time                                                                                                                                                                    |
| `JOB_TIMEOUT_S`                                              | `180`                                                                                    | A job fails with `timeout` after this                                                                                                                                                              |
| `JOB_MAX_ATTEMPTS`                                           | `2`                                                                                      | Tries per job before it fails with `interrupted`                                                                                                                                                   |
| `JOB_STALE_AFTER_S`                                          | `JOB_TIMEOUT_S + 60`                                                                     | A pending or processing job not updated for this long is taken over (its worker is gone)                                                                                                           |
| `JOB_SWEEP_INTERVAL_S`                                       | `60`                                                                                     | How often to look for such jobs                                                                                                                                                                    |
| `CACHE_TTL_S`                                                | `21600`                                                                                  | A product checked within this time is answered from the saved result                                                                                                                               |
| `IDEMPOTENCY_TTL_S`                                          | `86400`                                                                                  | How long an `Idempotency-Key` is remembered                                                                                                                                                        |
| `RATE_CREATE_PER_MIN` / `RATE_POLL_PER_MIN`                  | `10` / `60`                                                                              | Per-IP limits for creating and polling jobs                                                                                                                                                        |
| `TRUST_FORWARDED_FOR`                                        | `false`                                                                                  | Read the client IP from `X-Forwarded-For`. Turn on behind a proxy (Cloud Run)                                                                                                                      |
| `TRUSTED_PROXY_HOPS`                                         | `1`                                                                                      | Which `X-Forwarded-For` entry is the client, counted from the right. `1` on Cloud Run, `2` behind an external load balancer. Entries further left are sent by the client and can be faked          |

Use `NEEDL_SOURCE=live` with `NEEDL_ANALYZER=real` to fetch Shopee data and
analyze it with Gemini. `NEEDL_SOURCE=stub` with `NEEDL_ANALYZER=stub` uses
sample listings and checks from `app/analysis/stub_data.json`. Real mode needs
`GEMINI_API_KEY` and a configured Shopee provider (`APIFY_TOKEN` or RapidAPI
credentials); `real+stub` is rejected at startup. `live+stub` fetches live
listings but returns the sample analysis. Only `stub+stub` uses cached results;
existing Firestore records have no source or analyzer provenance.

`.env` is git-ignored. Never commit real keys.

## 4. Run

Start the emulator in one terminal (data lives in memory and is gone when it stops):

```bash
scripts/firestore_emulator.sh
```

Then the API, with `GOOGLE_CLOUD_PROJECT=needl-local` and
`FIRESTORE_EMULATOR_HOST=127.0.0.1:8085` in `.env`:

```bash
python -m uvicorn main:app --port 8000 --reload
```

Run a **single process** (no `--workers`): the job queue lives in memory.
A job left pending or processing when the server stops is taken over once it has
not been updated for `JOB_STALE_AFTER_S` (4 minutes by default), checked every
`JOB_SWEEP_INTERVAL_S`. It gets at most `JOB_MAX_ATTEMPTS` tries, then fails with
`interrupted`. Interactive docs: http://localhost:8000/docs

```bash
# link mode
curl -i -X POST http://localhost:8000/api/v1/verification-jobs \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://shopee.co.id/Dermalune-10-Niacinamide-Alpha-Arbutin-Brightening-Serum-30ml-Original-i.773019284.25810442671","locale":"id-ID"}'

# screenshot mode
curl -i -X POST http://localhost:8000/api/v1/verification-jobs \
  -F 'product=@./product.png' -F 'store=@./store.png' -F 'locale=id-ID'

# poll (use the id from the response)
curl http://localhost:8000/api/v1/verification-jobs/<job_id>

# saved verification history (optional filters: risk_level, from, to, cursor)
curl 'http://localhost:8000/api/v1/verifications?limit=20&risk_level=safe'

# one saved verification (use an id from history or a completed job)
curl http://localhost:8000/api/v1/verifications/<verification_id>

# service health
curl http://localhost:8000/health
```

`POST /api/v1/verification-jobs` accepts `Idempotency-Key` (1–128 printable
ASCII characters). Reusing a key with the same request returns the original job;
using it with a different request returns `409 idempotency_conflict`.
Screenshot multipart requests require `product` and `store`; optional fields are
`description`, `low_reviews`, `newest_reviews`, up to five `extra_screenshots`,
optional matching `extra_names`, and `locale` (`en-US` or `id-ID`). Images must
be JPEG, PNG, or WebP; each file is limited to 10 MiB, total upload to 30 MiB,
and up to 10 images are accepted. JSON request bodies are limited to 16 KiB.

History supports `limit` (1–100, default 20), `risk_level`, `from`, `to`, and the
opaque `cursor` returned in `meta.next_cursor`. Dates are ISO 8601; `from` is
inclusive and `to` is exclusive. Valid risk levels: `safe`, `suspicious`, `fraud`.
Responses include `X-Request-ID`. Interactive OpenAPI docs: http://localhost:8000/docs

In stub mode, links with shop id `218334561` get the "safe" sample and
everything else gets the "risky" sample, like the needl-ui mock.

Seed Firestore history with alternating safe/risky demo records. The script
uses `.env` Firestore settings and can run while the API is running:

```bash
python -m scripts.seed_dummy --count 10
python -m scripts.seed_dummy --count 25
```

## 5. Shopee links in live mode

Shopee sends browsers without a login to a "Masuk Diperlukan" page, so links
are read through third-party data providers instead of our own scraper. They
are tried in `SHOPEE_PROVIDERS` order; when one fails, the next one is used.

| Provider          | What it calls                                                                                                                                                                   | Cost (Oct 2026)                                                                      |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Apify (main)      | Actor `zen-studio/shopee-product-detail-scraper` for the product and `zen-studio/shopee-product-reviews-scraper` for reviews, both at once through `run-sync-get-dataset-items` | about $4.99 per 1,000 products ($7.99 after 21 Oct 2026) and $3.99 per 1,000 reviews |
| RapidAPI (backup) | The Shopee API you subscribe to, configured as URL templates                                                                                                                    | depends on the API's plan                                                            |

Reviews are optional: when the reviews run fails, the job still completes
with product data. The reviews actor fails often (about 60% of public runs in
the 30 days before 16 Oct 2026), so expect some jobs without reviews.

RapidAPI Shopee APIs differ in paths and parameters. Copy the endpoint from
the API's Code Snippets tab and put placeholders where the ids go:

```bash
RAPIDAPI_KEY=...
RAPIDAPI_SHOPEE_DETAIL_URL=https://<host>.p.rapidapi.com/<path>?shop_id={shop_id}&item_id={item_id}
RAPIDAPI_SHOPEE_REVIEWS_URL=https://<host>.p.rapidapi.com/<path>?shop_id={shop_id}&item_id={item_id}&limit={limit}
```

Placeholders: `{shop_id}`, `{item_id}`, `{url}` (URL-encoded product link),
`{limit}`. The `x-rapidapi-host` header is taken from the URL. Responses are
mapped by `app/sources/providers/normalize.py`, which reads Shopee's own JSON
shape (`data.item`, prices times 100000) and camelCase records.

Check a provider with a real product before using it:

```bash
python -m scripts.shopee_fetch 'https://shopee.co.id/...-i.<shop_id>.<item_id>'
python -m scripts.shopee_fetch --save '<url>'   # also writes raw JSON to tests/fixtures/shopee/
```

Each run costs one product and up to `SHOPEE_MAX_REVIEWS` reviews per provider.
Short links (`shp.ee`) are followed only through Shopee hosts.

## 6. Tests

```bash
scripts/test.sh            # starts the emulator if needed, then runs pytest
scripts/test.sh -k repo -x # extra arguments go to pytest
```

Tests run in stub mode, with no network, against the emulator project
`needl-test`, which is cleared before every test. They never reach a real
Firestore project: without a reachable `FIRESTORE_EMULATOR_HOST` every test that
needs storage fails with a message saying so. `python -m pytest` also works when
an emulator is already running and `FIRESTORE_EMULATOR_HOST` is set.

## 7. Project structure

```
needl-service/
├── main.py                    # create_app(): lifespan, middleware, routers
├── Dockerfile                 # Cloud Run image (no Chromium)
├── app/
│   ├── config.py              # settings from .env
│   ├── api/                   # routes, error envelope + request id, rate limit
│   ├── contract/              # Pydantic request/response models
│   ├── db/                    # Firestore repository and JobRecord
│   ├── jobs/                  # in-memory queue + sweeper, job runner, progress numbers
│   ├── sources/               # stub, Shopee providers (Apify, RapidAPI), screenshots (Gemini Vision)
│   ├── analysis/              # Gemini and stub analyzers, scoring, result builder
│   ├── llm/gemini.py          # Gemini client
├── deploy/                    # setup.sh, deploy.sh, smoke.sh, env.example.yaml
├── scripts/                   # emulator, seed, test, and Shopee fetch commands
└── tests/
```

## 8. How a job runs

1. `POST` validates the input, stores the job as `pending` in Firestore and puts it on the in-memory queue (`202`).
2. A worker claims the job in a Firestore transaction: `processing`, progress 5, one more attempt. A job another live worker is processing is skipped, so it never runs twice. Stub mode can reuse a fresh result; real mode always fetches and analyzes again.
3. The source returns one normalized listing: stub data, provider data (Apify, then RapidAPI), or Gemini Vision output. The job gets its `preview` (progress 30).
4. The analyzer uses sample checks in stub mode or asks Gemini to assess supplied listing/review evidence in real mode.
5. The result is validated against the contract, saved, and the job becomes `completed` (100).
6. A sweeper requeues pending or processing jobs that have not been updated for `JOB_STALE_AFTER_S`. A screenshot job whose files are not on this instance fails with `interrupted`.

Risk score = `min(100, 20·critical + 8·major + 3·minor)`; `trust_score = 100 - score`.
`risk_level` is `safe` below 33, `suspicious` from 33, `fraud` from 67.

## 9. Data model (Firestore)

| Collection          | Document ID                         | Contents                                                                      |
| ------------------- | ----------------------------------- | ----------------------------------------------------------------------------- |
| `verification_jobs` | job UUID                            | status, progress, input, preview, error, attempts, `created_at`, `updated_at` |
| `verifications`     | verification UUID                   | summary fields plus `result_json`, the full result as a JSON string           |
| `verification_urls` | sha256 of the canonical product URL | ID and `checked_at` of the newest result for that product (cache lookup)      |
| `idempotency_keys`  | sha256 of the key                   | request hash, job ID, `created_at`, `expires_at` (TTL policy)                 |

- `result_json` is exempt from indexing (`deploy/setup.sh`), since it is large and
  never queried. A document can be at most 1 MiB.
- History filters and pagination currently scan saved results in the service to
  avoid composite indexes; use indexed Firestore queries if the collection grows.
- Expired idempotency keys are ignored by the code right away; the TTL policy
  deletes them later (usually within a day). Jobs and results have no TTL, because
  a replayed key expects its job to exist.

## 10. Deploy to Cloud Run

The service runs as **one** Cloud Run instance (`--max-instances 1`) with CPU
always allocated (`--no-cpu-throttling`), because the job queue, the rate limiter
and the screenshot uploads live in that instance's memory, and workers run
outside requests. It reaches Firestore with its service account through
Application Default Credentials; there is no key file. Secrets come from Secret
Manager. Default region: `asia-southeast2` (Jakarta), Firestore in the same location.

### Prerequisites

- A GCP project with billing, the gcloud CLI, and `gcloud auth login`.
- Deploying account: project Owner, or `roles/run.sourceDeveloper`,
  `roles/serviceusage.serviceUsageConsumer`, and `roles/iam.serviceAccountUser`
  on the runtime service account. `--allow-unauthenticated` also needs permission
  to set the service's IAM policy (`roles/run.admin`), and an organization policy
  that restricts `allUsers` blocks it.

### Steps

```bash
# 1. APIs, Firestore (Native, asia-southeast2), TTL policy, index exemption,
#    runtime service account with roles/datastore.user, roles/run.builder for
#    Cloud Build, secrets (asks for values, input hidden). Safe to run again.
PROJECT_ID=my-project deploy/setup.sh

# 2. Non-secret settings
cp deploy/env.example.yaml deploy/env.yaml
#    set GOOGLE_CLOUD_PROJECT and CORS_ORIGINS (the UI origin)

# 3. Build with Cloud Build and deploy
PROJECT_ID=my-project deploy/deploy.sh

# 4. Check: /health, CORS, rate limit against a spoofed X-Forwarded-For
PROJECT_ID=my-project deploy/smoke.sh
PROJECT_ID=my-project deploy/smoke.sh --with-job --link '<real Shopee link>'   # costs Apify credit
```

`deploy.sh` uploads the source (without `.env`, see `.gcloudignore`), builds the
`Dockerfile` with Cloud Build, stores the image in Artifact Registry
(`cloud-run-source-deploy`), and deploys with: service account
`needl-service@PROJECT.iam.gserviceaccount.com`, 1 CPU, 1 GiB, concurrency 80,
timeout 300 s, min 0 / max 1 instances, `deploy/env.yaml`, and each secret that
has a version.

### GitHub Actions CI/CD

`.github/workflows/ci-cd.yml` runs Firestore-emulator tests on pull requests to
`main`; pushes to `main` deploy after tests pass. Configure Workload Identity
Federation once in GCP. Replace `PROJECT_ID` and `ORG/REPO` below:

```bash
PROJECT_ID=my-project
ORG_REPO=my-org/my-repo
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
CI_SA="needl-github-deploy@$PROJECT_ID.iam.gserviceaccount.com"

gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  --project "$PROJECT_ID"
gcloud iam service-accounts create needl-github-deploy --project "$PROJECT_ID"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$CI_SA" --role=roles/run.admin
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$CI_SA" --role=roles/run.sourceDeveloper
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$CI_SA" --role=roles/serviceusage.serviceUsageConsumer
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$CI_SA" --role=roles/secretmanager.viewer
gcloud iam service-accounts add-iam-policy-binding \
  "needl-service@$PROJECT_ID.iam.gserviceaccount.com" --project "$PROJECT_ID" \
  --member="serviceAccount:$CI_SA" --role=roles/iam.serviceAccountUser

gcloud iam workload-identity-pools create github --project "$PROJECT_ID" \
  --location=global --display-name=GitHub-Actions
gcloud iam workload-identity-pools providers create-oidc github \
  --project "$PROJECT_ID" --location=global --workload-identity-pool=github \
  --display-name=GitHub --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='$ORG_REPO'"
gcloud iam service-accounts add-iam-policy-binding "$CI_SA" --project "$PROJECT_ID" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$ORG_REPO"
```

In GitHub repository **Settings → Secrets and variables → Actions**, add:

| Kind | Name | Value |
|---|---|---|
| Variable | `GCP_PROJECT_ID` | GCP project ID |
| Variable | `CORS_ORIGINS` | UI origin, for example `https://app.example.com` |
| Secret | `GCP_WIF_PROVIDER` | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github` |
| Secret | `GCP_SERVICE_ACCOUNT` | `needl-github-deploy@PROJECT_ID.iam.gserviceaccount.com` |

Set branch protection on `main` to require the `test` check. Runtime API keys
remain in GCP Secret Manager; the deploy script attaches enabled secret versions.

### needl-ui

Build the UI with `VITE_API_URL` set to the Cloud Run URL (`deploy.sh` prints it),
and put the UI's origin in `CORS_ORIGINS` in `deploy/env.yaml`, then redeploy.

### Operations

| Task                    | Command                                                                                                                                              |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Logs                    | `gcloud run services logs read needl-service --region asia-southeast2 --limit 100`                                                                   |
| Find one failed request | search the logs for the `request_id` (browser console in development, or the `X-Request-ID` response header)                                         |
| Revisions               | `gcloud run revisions list --service needl-service --region asia-southeast2`                                                                         |
| Roll back               | `gcloud run services update-traffic needl-service --to-revisions REVISION=100 --region asia-southeast2`                                              |
| Rotate a secret         | `printf '%s' "$NEW" \| gcloud secrets versions add GEMINI_API_KEY --data-file=-`, then `deploy/deploy.sh`: `:latest` is read when an instance starts |
| TTL policy status       | `gcloud firestore fields ttls list --collection-group=idempotency_keys`                                                                              |

### Costs

- Instance-based billing: the instance is billed for as long as it runs. With
  `--min-instances 0` it stops when idle (the first request after that waits for a
  cold start); `--min-instances 1` avoids cold starts but runs all the time.
- Firestore has a daily free tier; each job is a handful of reads and writes.
- Apify and Gemini are billed per use. Set `APIFY_MAX_CHARGE_USD`, and a budget
  alert: `gcloud billing budgets create --billing-account=ACCOUNT_ID --display-name=needl --budget-amount=20USD --threshold-rule=percent=0.5 --threshold-rule=percent=0.9`.

### Limitations

- Gemini only assesses fields returned by configured Shopee providers. Missing descriptions or reviews limit the checks; suggested stores are empty until a verified comparison source is integrated.
- The API does not require authentication. Anyone who
  finds the URL can start jobs that cost Apify and Gemini credit. Mitigations: the
  per-IP rate limit, `APIFY_MAX_CHARGE_USD`, one instance, and a budget alert.
- One instance only. Scaling out needs Cloud Storage for uploads and a shared queue
  (Cloud Tasks); that is separate work.

## 11. Troubleshooting

| Problem                                             | Fix                                                                                                        |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Tests fail with "Firestore emulator is not running" | Run `scripts/test.sh`, or start `scripts/firestore_emulator.sh` and set `FIRESTORE_EMULATOR_HOST`          |
| Emulator does not start: Java errors                | Install JDK 21 or newer (`brew install openjdk@21`) and put it on `PATH`                                   |
| `Firestore credentials not found` at startup        | Locally set `FIRESTORE_EMULATOR_HOST`; against a real project run `gcloud auth application-default login`  |
| `403 PERMISSION_DENIED` from Firestore on Cloud Run | The runtime service account needs `roles/datastore.user` (`deploy/setup.sh`, step 6)                       |
| Link jobs fail with `source_unavailable`            | No provider configured or all failed: check `APIFY_TOKEN` / `RAPIDAPI_*`, Apify credit, and the server log |

| Screenshot jobs fail with `service_busy` | Gemini is not configured (`GEMINI_API_KEY` / the secret) or its API failed; see the log |
| Screenshot jobs fail with `extraction_failed` | Gemini answered but found no product data; the screenshots are unclear |
| Uploads fail with a network error, no JSON | The request was over Cloud Run's 32 MiB limit; keep `MAX_UPLOAD_TOTAL_MB` at 30 or less |
| Browser calls blocked by CORS | Add the UI origin to `CORS_ORIGINS` (`deploy/env.yaml` on Cloud Run) and redeploy |
| Idempotency keys are not deleted | The TTL policy may still be `CREATING` (10 minutes or more); expired keys are ignored meanwhile |
| Port already in use | Change `--port 8000` |

### What the UI message means

needl-ui never shows technical details. In development the browser console logs
each failed request with its status, code, message and `request_id`
(`[needl] request failed`).

| UI text (key)                                              | Cause                                                                                                 | Check                                                              |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| "Can't connect to needl…" (`errNetwork`)                   | No response the browser could read: server down, wrong `VITE_API_URL`, CORS, or a request over 32 MiB | Server log, `CORS_ORIGINS`, `VITE_API_URL`                         |
| "needl is busy right now…" (`errUnavailable`)              | `502`/`503`/`504` from the platform, for example during a cold start or deploy                        | Cloud Run logs and revisions                                       |
| "Something went wrong…" (`errServer`)                      | `500 internal_error`, `400`, `409`, a failed job with `internal_error`, or an unknown code            | Search the log for the `request_id`                                |
| "Too many checks…" (`errRateLimited`)                      | `429`                                                                                                 | `RATE_CREATE_PER_MIN` / `RATE_POLL_PER_MIN`, `TRUSTED_PROXY_HOPS`  |
| "This check is no longer available…" (`errJobNotFound`)    | `404 job_not_found`                                                                                   | The job ID; old history entries point at jobs that no longer exist |
| "Screenshots can't be read right now…" (`errServiceBusy`)  | Job failed with `service_busy`                                                                        | `GEMINI_API_KEY`, Gemini quota, the log                            |
| "All screenshots together must be 30 MB…" (`errTotalSize`) | `413 payload_too_large`                                                                               | `MAX_UPLOAD_TOTAL_MB`                                              |
| "One image is too large…" (`errImageTooLarge`)             | `413 image_too_large` (over 40 megapixels)                                                            | The image                                                          |
| "The screenshots couldn't be sent…" (`errShotsInvalid`)    | `422` on a screenshot request                                                                         | The request fields; the log                                        |
| "Shopee data can't be fetched…" (`errSourceUnavailable`)   | Job failed with `source_unavailable`                                                                  | Provider credentials and credit                                    |
