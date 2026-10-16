# needl-service

FastAPI backend for needl. It runs verification jobs for a Shopee listing, from
a product link or from screenshots, and returns the result the needl-ui result
page renders. The API follows `API_Contract.md` (v1).

Implemented now:

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/verification-jobs` | Start a job from a link (JSON) or screenshots (multipart) |
| `GET /api/v1/verification-jobs/{job_id}` | Poll progress, preview and the final result |
| `GET /api/v1/verifications` | List saved scans with filters and cursor pagination |
| `GET /api/v1/verifications/{verification_id}` | Read one saved result |
| `GET /health` | Service check |

The real analyzer (`NEEDL_ANALYZER=real`) is not implemented yet. In stub mode,
completed jobs save sample verification results in SQLite; the history and detail
endpoints read those same records.

---

## 1. Requirements

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11 or newer | the contract models use `StrEnum`; tested on 3.14 |

## 2. Installation

```bash
cd needl-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt     # app + test tools
cp .env.example .env
```

## 3. Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `NEEDL_SOURCE` | `stub` | `stub`: sample listing, no network. `live`: Shopee data providers for links, Gemini Vision for screenshots |
| `NEEDL_ANALYZER` | `stub` | `stub`: sample checks copied from the needl-ui mock. `real` is not implemented yet |
| `STUB_STEP_MS` | `600` | Delay per step in stub mode, so polling looks like a real run |
| `GEMINI_API_KEY` | - | Needed for screenshot jobs in live mode |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model name |
| `SHOPEE_PROVIDERS` | `apify,rapidapi` | Providers to try, in order. One without credentials is skipped |
| `SHOPEE_MAX_REVIEWS` | `100` | Reviews fetched per product |
| `APIFY_TOKEN` | - | Apify API token (Settings, API & Integrations in the Apify console) |
| `APIFY_DETAIL_ACTOR` / `APIFY_REVIEWS_ACTOR` | `zen-studio~shopee-product-detail-scraper` / `zen-studio~shopee-product-reviews-scraper` | Actors used for product data and reviews |
| `APIFY_TIMEOUT_S` | `120` | Longest wait for one actor run |
| `APIFY_MAX_CHARGE_USD` | - | Optional spending cap per actor run |
| `RAPIDAPI_KEY` | - | RapidAPI key |
| `RAPIDAPI_SHOPEE_DETAIL_URL` / `RAPIDAPI_SHOPEE_REVIEWS_URL` | - | Endpoint templates of the RapidAPI Shopee API, see section 5 |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated origins allowed to call the API |
| `DB_PATH` | `data/needl.db` | SQLite database |
| `UPLOAD_DIR` | `data/uploads` | Screenshots, deleted when their job finishes |
| `WORKER_COUNT` | `2` | Jobs processed at the same time |
| `JOB_TIMEOUT_S` | `180` | A job fails with `timeout` after this |
| `CACHE_TTL_S` | `21600` | A product checked within this time is answered from the saved result |
| `RATE_CREATE_PER_MIN` / `RATE_POLL_PER_MIN` | `10` / `60` | Per-IP limits for creating and polling jobs |

`.env` is git-ignored. Never commit real keys.

## 4. Run

```bash
python -m uvicorn main:app --port 8000 --reload
```

Run a **single process** (no `--workers`): the job queue lives in memory. Jobs
left pending or processing when the server stops are picked up again on the
next start (at most `JOB_MAX_ATTEMPTS`, default 2, then they fail with
`interrupted`). Interactive docs: http://localhost:8000/docs

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
```

In stub mode, links with shop id `218334561` get the "safe" sample and
everything else gets the "risky" sample, like the needl-ui mock.

Seed saved history with 10 alternating safe/risky demo records (or choose a
count/database path):

```bash
python -m scripts.seed_dummy --count 10
python -m scripts.seed_dummy --count 25 --db data/demo.db
```

## 5. Shopee links in live mode

Shopee sends browsers without a login to a "Masuk Diperlukan" page, so links
are read through third-party data providers instead of our own scraper. They
are tried in `SHOPEE_PROVIDERS` order; when one fails, the next one is used.

| Provider | What it calls | Cost (Oct 2026) |
|---|---|---|
| Apify (main) | Actor `zen-studio/shopee-product-detail-scraper` for the product and `zen-studio/shopee-product-reviews-scraper` for reviews, both at once through `run-sync-get-dataset-items` | about $4.99 per 1,000 products ($7.99 after 21 Oct 2026) and $3.99 per 1,000 reviews |
| RapidAPI (backup) | The Shopee API you subscribe to, configured as URL templates | depends on the API's plan |

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
python -m pytest
```

Tests run in stub mode with a temporary database and no network.

## 7. Project structure

```
needl-service/
├── main.py                    # create_app(): lifespan, middleware, routers
├── app/
│   ├── config.py              # settings from .env
│   ├── api/                   # routes, error envelope + request id, rate limit
│   ├── contract/              # Pydantic models from API_Contract.md
│   ├── db/                    # SQLite schema and repository
│   ├── jobs/                  # in-memory queue, job runner, progress numbers
│   ├── sources/               # stub, Shopee providers (Apify, RapidAPI), screenshots (Gemini Vision)
│   ├── analysis/              # stub analyzer, scoring, result builder
│   ├── llm/gemini.py          # Gemini client
├── scripts/                   # shopee_fetch
└── tests/
```

## 8. How a job runs

1. `POST` validates the input, stores the job as `pending` in SQLite and puts it on the queue (`202`).
2. A worker marks it `processing` (progress 5). For links it first looks for a saved result of the same product (`cached: true`).
3. The source returns one normalized listing: stub data, provider data (Apify, then RapidAPI), or Gemini Vision output. The job gets its `preview` (progress 30).
4. The analyzer runs its 8 steps (progress 38 to 94, matching the 8 lines of the UI checking log).
5. The result is validated against the contract, saved, and the job becomes `completed` (100).

Risk score = `min(100, 20·critical + 8·major + 3·minor)`; `trust_score = 100 - score`.
`risk_level` is `safe` below 33, `suspicious` from 33, `fraud` from 67.

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| Link jobs fail with `source_unavailable` | No provider configured or all failed: check `APIFY_TOKEN` / `RAPIDAPI_*`, Apify credit, and the server log |
| Screenshot jobs fail with `extraction_failed` | Set `GEMINI_API_KEY` in `.env` (live mode) |
| Browser calls blocked by CORS | Add the UI origin to `CORS_ORIGINS` |
| Port already in use | Change `--port 8000` |
