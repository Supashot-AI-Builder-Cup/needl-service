# Backend - E-commerce Scraper API

FastAPI + Playwright service that scrapes e-commerce product pages and returns
structured data (product name, merchant, price, rating, reviews, images).
Optionally sends the scraped content to Gemini (`/analyze`) with an optional
custom `prompt` for AI-based extraction.

---

## 1. Requirements (engine / software)

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10 - 3.13 | tested on 3.13.5 |
| pip | >= 23 | comes with Python |
| Node.js | not required | Playwright Python ships its own driver |
| Chromium browser | installed via Playwright | ~180 MB, downloaded by `playwright install chromium` |
| OS | macOS / Linux / Windows | tested on macOS (arm64) |

Python packages (installed from `requirements.txt`):

| Package | Version | Purpose |
|---|---|---|
| fastapi | >=0.115,<1.0 | HTTP API |
| uvicorn[standard] | >=0.30 | ASGI server |
| playwright | >=1.44 | async browser automation |
| playwright-stealth | >=2.0.3 | anti bot-detection evasions |
| pydantic | >=2.7 | request validation |
| google-genai | >=1.0.0 | Gemini model client |
| python-dotenv | >=1.0 | load `.env` config |
| python-multipart | >=0.0.9 | multipart image upload parsing |

> Windows/Linux may need extra system libs for Chromium. If Chromium fails to
> start, run `playwright install-deps chromium` (Linux) and retry.

---

## 2. Installation

```bash
cd backend

# 1. create virtual environment
python3 -m venv .venv

# 2. activate it
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# 3. install python dependencies
pip install -r requirements.txt

# 4. download the Chromium engine used by Playwright
python -m playwright install chromium

# 5. create your env file and put the Gemini API key in it
cp .env.example .env
# then edit .env and set GEMINI_API_KEY=...
```

### Environment variables (`.env`)

| Variable | Required | Default | Description |
|---|---|---|---|
| GEMINI_API_KEY | for `/analyze` | - | Google AI Studio API key |
| GEMINI_MODEL | no | gemini-2.5-flash | Gemini model name |

`.env` is git-ignored. Never commit real keys.

---

## 3. Run the server

```bash
# from the backend folder, with .venv activated
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open the interactive docs: http://localhost:8000/docs

---

## 4. API reference

### `GET /health`

Returns `{"status": "ok", "gemini_configured": true|false}`.

### `POST /scrape` (raw extraction, no AI)

Request body:

```json
{
  "url": "https://scrapeme.live/shop/Bulbasaur/",
  "headless": true,
  "scroll": true,
  "timeout_ms": 30000
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| url | string | required | product page URL (http/https only) |
| headless | bool | true | run browser headless |
| scroll | bool | true | auto-scroll to trigger lazy load |
| timeout_ms | int | 30000 | navigation timeout (3000-120000) |

Success response:

```json
{
  "status": "success",
  "data": {
    "url": "https://scrapeme.live/shop/Bulbasaur/",
    "requested_url": "https://scrapeme.live/shop/Bulbasaur/",
    "source_site": "scrapeme.live",
    "product_name": "Bulbasaur",
    "merchant_name": "ScrapeMe",
    "sales_count": null,
    "price": "63.00",
    "price_value": 63.0,
    "currency": "GBP",
    "rating": null,
    "review_count": null,
    "reviews": [
      { "author": "Andi", "rating": 5.0, "date": "2026-01-02", "text": "Aroma mantap." }
    ],
    "images": ["https://scrapeme.live/wp-content/uploads/2018/08/001.png"],
    "scraped_at": "2026-10-13T02:47:00Z",
    "http_status": 200
  }
}
```

Error responses:

| Code | Meaning |
|---|---|
| 400 | invalid or disallowed URL (bad scheme / SSRF host) |
| 502 | page load or scrape failed |
| 504 | scrape exceeded total timeout |

### `POST /analyze` (scrape + Gemini)

Scrapes the page, then asks Gemini to read it and produce a **product report**.
The default output is a JSON object with the product fields plus `overview`,
`key_benefits`, `price_range_indonesia`, and a Markdown `report`. Use the optional
`prompt` field to add a custom instruction (it is appended to the default
instruction, so the JSON keys stay the same).

Request body:

```json
{
  "url": "https://www.ebay.com/itm/123456",
  "prompt": "Fokus ke review dan kisaran harga di Indonesia.",
  "headless": true,
  "scroll": true,
  "timeout_ms": 30000
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| url | string | required | product page URL (http/https only) |
| prompt | string | null | optional additional instruction for Gemini |
| headless | bool | true | run browser headless |
| scroll | bool | true | auto-scroll to trigger lazy load |
| timeout_ms | int | 30000 | navigation timeout (3000-120000) |

Success response:

```json
{
  "status": "success",
  "source": { "url": "https://www.ebay.com/itm/123456", "source_site": "ebay.com" },
  "data": {
    "product_name": "La Roche Posay Cicaplast Baume B5+ - 40ml",
    "price": "IDR 322,561",
    "price_value": 322561,
    "currency": "IDR",
    "merchant_name": "eBay",
    "rating": 4.8,
    "review_count": "62,3k+",
    "overview": "Rich, multi-purpose soothing balm that repairs and protects the skin barrier...",
    "key_benefits": [
      { "title": "Skin Repair", "description": "Accelerates the skin's natural recovery process..." },
      { "title": "Key Ingredients", "description": "5% Panthenol (Vitamin B5) and Madecassoside..." },
      { "title": "Versatility", "description": "Spot treatment for dry patches, minor burns, chapped lips..." },
      { "title": "Who it is for", "description": "Hypoallergenic, fragrance-free, suitable for all skin types." }
    ],
    "price_range_indonesia": "Rp 240.000 - Rp 340.000 depending on seller and promotions.",
    "reviews": [],
    "images": ["https://i.ebayimg.com/..."],
    "report": "### La Roche Posay Cicaplast Baume B5+ - 40ml\nIDR 322,561\n\n..." 
  }
}
```

Error responses:

| Code | Meaning |
|---|---|
| 400 | invalid or disallowed URL |
| 502 | scrape failed, or Gemini call failed |
| 503 | `GEMINI_API_KEY` not set / google-genai missing |
| 504 | scrape exceeded total timeout |

### `POST /analyze-image` (image + Gemini vision)

Upload one or more images (`multipart/form-data`) of a product page and/or its
reviews. Gemini Vision reads them and returns a product summary plus a review
analysis grouped by star rating, including complaints (keluhan) and suggestions
(saran). Optional `prompt` adds a custom instruction.

```bash
curl -X POST http://localhost:8000/analyze-image \
  -F "files=@/tmp/product.png;type=image/png" \
  -F "prompt=Fokus ke review, beri saran dan keluhan dalam Bahasa Indonesia."
```

Form fields:

| Field | Type | Required | Description |
|---|---|---|---|
| files | file[] | yes | 1-6 images (max 8 MB each, `image/*`) |
| prompt | string | no | optional additional instruction |

Success response:

```json
{
  "status": "success",
  "data": {
    "product_name": "Tokopedia",
    "product_summary": "Marketplace jual beli barang daring di Indonesia.",
    "overall_rating": 1.5,
    "review_count": "167",
    "sentiment": "negative",
    "rating_breakdown": [
      {
        "stars": 1,
        "count": null,
        "summary": "Keluhan soal refund dan pengiriman.",
        "pros": [],
        "complaints": ["Barang tidak diterima", "Refund lambat"]
      }
    ],
    "reviews": [
      { "author": null, "rating": 1, "text": "...", "sentiment": "negative", "complaints": ["..."], "suggestions": ["..."] }
    ],
    "top_complaints": ["Layanan pelanggan tidak membantu", "Refund lambat"],
    "suggestions": ["Percepat proses refund", "Perbaiki layanan pelanggan"],
    "report": "### Ringkasan Produk\n...\n\n### Ulasan Berdasarkan Rating\n..."
  }
}
```

Error responses:

| Code | Meaning |
|---|---|
| 400 | no image / unsupported type / too large / more than 6 images |
| 502 | Gemini call failed |
| 503 | `GEMINI_API_KEY` not set / google-genai missing |

---

## 5. Testing the API

### With curl

```bash
# health check
curl http://localhost:8000/health

# scrape a product
curl -X POST http://localhost:8000/scrape \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://scrapeme.live/shop/Bulbasaur/"}'

# pretty print
curl -s -X POST http://localhost:8000/scrape \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"}' \
  | python -m json.tool

# AI analyze (needs GEMINI_API_KEY in .env)
curl -X POST http://localhost:8000/analyze \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://scrapeme.live/shop/Bulbasaur/"}'

# AI analyze with a custom prompt
curl -X POST http://localhost:8000/analyze \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://scrapeme.live/shop/Bulbasaur/","prompt":"Ambil semua review dan rating saja"}'

# AI analyze from an image (product/review screenshot)
curl -X POST http://localhost:8000/analyze-image \
  -F "files=@/tmp/product.png;type=image/png" \
  -F "prompt=Ringkas summary dan review, beri saran dan keluhan"
```

### With the built-in docs UI

1. Open http://localhost:8000/docs
2. Expand `POST /scrape`, `POST /analyze`, or `POST /analyze-image` -> `Try it out`
3. Paste a product URL (or choose an image) -> `Execute`

### Direct scraper test (without the server)

```bash
python -c "import asyncio, json, scraper; \
print(json.dumps(asyncio.run(scraper.scrape_ecommerce_data('https://scrapeme.live/shop/Bulbasaur/')), indent=2))"
```

---

## 6. Project structure

```
backend/
├── main.py            # FastAPI app: /scrape, /analyze, /analyze-image, /health, CORS
├── scraper.py         # Playwright scraper + extraction/merge logic
├── gemini.py          # Gemini client: text (URL) + vision (image) analysis
├── requirements.txt   # python dependencies
├── .env.example       # env template (copy to .env)
├── README.md          # this file
└── .venv/             # local virtual environment (not committed)
```

---

## 7. How it works

`/scrape` extraction is layered, most reliable source first:

1. JSON-LD (`schema.org/Product`): name, brand/seller, offers, rating, reviews, images.
2. OpenGraph / product meta tags: title, image, price, currency, site name.
3. DOM heuristics: generic and known `data-testid`/class selectors.

`/analyze` uses the same Playwright fetch to get the page text/meta/images, then
sends that content to Gemini together with your optional `prompt`. Gemini returns
structured JSON. This is more robust on sites that block direct DOM scraping.

The browser blocks non-essential resources (CSS, fonts, media, beacons), applies
stealth evasions, uses an Indonesian locale/timezone, and auto-scrolls to trigger
lazy-loaded reviews and images. Missing fields return `null` instead of failing.

---

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `Executable doesn't exist ... chromium` | run `python -m playwright install chromium` |
| Chromium crashes on Linux | run `playwright install-deps chromium`, add `--no-sandbox` (already set) |
| `502 scrape failed` | site blocked the request or URL is wrong; try `headless:false` or raise `timeout_ms` |
| `503 GEMINI_API_KEY is not set` | create `.env` from `.env.example` and set the key |
| `502 gemini failed: API key not valid` | wrong/expired Gemini API key |
| `400 invalid or disallowed url` | only public http/https URLs are allowed (SSRF guard) |
| Port already in use | change `--port 8000` to another port |

---

## 9. Not included (per project plan)

- Database persistence (e.g. Firebase)
