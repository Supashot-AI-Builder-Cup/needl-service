"""Endpoints from before the v1 API. Kept until the verification-jobs flow replaces them."""

import asyncio
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..llm import gemini
from .scraper import fetch_page_content, scrape_ecommerce_data

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 6

router = APIRouter(tags=["legacy"])


class ScrapeRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    headless: bool = True
    scroll: bool = True
    timeout_ms: int = Field(default=30000, ge=3000, le=120000)


class AnalyzeRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    prompt: Optional[str] = Field(default=None, max_length=4000)
    headless: bool = True
    scroll: bool = True
    timeout_ms: int = Field(default=30000, ge=3000, le=120000)


@router.post("/scrape")
async def scrape(payload: ScrapeRequest):
    try:
        data = await scrape_ecommerce_data(
            payload.url,
            headless=payload.headless,
            scroll=payload.scroll,
            timeout_ms=payload.timeout_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="scrape timed out")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"scrape failed: {exc}")
    return {"status": "success", "data": data}


@router.post("/analyze")
async def analyze(payload: AnalyzeRequest):
    try:
        content = await fetch_page_content(
            payload.url,
            headless=payload.headless,
            scroll=payload.scroll,
            timeout_ms=payload.timeout_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="scrape timed out")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"scrape failed: {exc}")

    try:
        result = await gemini.analyze_page(payload.url, content, prompt=payload.prompt)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"gemini failed: {exc}")

    return {
        "status": "success",
        "source": {"url": content["url"], "source_site": content["source_site"]},
        "data": result,
    }


@router.post("/analyze-image")
async def analyze_image(
    files: list[UploadFile] = File(...),
    prompt: Optional[str] = Form(default=None),
):
    if not files:
        raise HTTPException(status_code=400, detail="no image provided")
    if len(files) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"too many images (max {MAX_IMAGES})")
    images = []
    for upload in files:
        if upload.content_type and not upload.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"unsupported file type: {upload.content_type}")
        data = await upload.read()
        if not data:
            continue
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="image too large (max 8MB each)")
        images.append((data, upload.content_type or "image/jpeg"))
    if not images:
        raise HTTPException(status_code=400, detail="empty image")

    try:
        result = await gemini.analyze_images(images, prompt=prompt)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"gemini failed: {exc}")
    return {"status": "success", "data": result}
