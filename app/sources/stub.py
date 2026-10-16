"""Sample listings without any network call (NEEDL_SOURCE=stub).

Mirrors the needl-ui mock: links containing the safe demo shop id get the
"safe" sample, everything else (and every screenshot job) gets "risky".
"""

import asyncio

from ..db.models import JobRecord, utc_now
from .base import ProgressFn, RawListing
from .shopee_url import parse_shopee_link

SAFE_SHOP_ID = "218334561"
DEMO_LINKS = (
    "https://shopee.co.id/Dermalune-10-Niacinamide-Alpha-Arbutin-Brightening-Serum-30ml-Original-i.773019284.25810442671",
    "https://shopee.co.id/Dermalune-Niacinamide-Brightening-Serum-30ml-Official-i.218334561.24019837712",
)

SAMPLES = {
    "risky": dict(
        title="Dermalune 10% Niacinamide + Alpha Arbutin Brightening Serum 30ml ORIGINAL",
        price=39000,
        price_was=129000,
        rating=4.9,
        rating_count=1200,
        sold_count=2400,
        shop_name="DermaIune 0fficial Shop",
        is_mall=False,
        shop_location="Kota Medan",
    ),
    "safe": dict(
        title="Dermalune Niacinamide Brightening Serum 30ml",
        price=129000,
        price_was=None,
        rating=4.9,
        rating_count=9800,
        sold_count=18000,
        shop_name="Dermalune Official Shop",
        is_mall=True,
        shop_location="Jakarta Barat",
    ),
}


class StubSource:
    def __init__(self, step_ms: int):
        self.step_s = step_ms / 1000

    async def fetch(self, job: JobRecord, report: ProgressFn) -> RawListing:
        for progress in (10, 20):
            await report(progress)
            await asyncio.sleep(self.step_s)

        if job.input_kind == "screenshots":
            sample = SAMPLES["risky"]
            return RawListing(
                source_kind="screenshots",
                screenshot_count=len(job.input["slots"]) + len(job.input["extras"]),
                fetched_at=utc_now(),
                stub_scenario="risky",
                **sample,
            )

        link = parse_shopee_link(job.input_url)
        scenario = "safe" if link.shop_id == SAFE_SHOP_ID else "risky"
        is_demo = any(job.input_url.startswith(demo) for demo in DEMO_LINKS)
        return RawListing(
            source_kind="url",
            requested_url=job.input_url,
            canonical_url=link.canonical_url,
            shop_id=link.shop_id,
            item_id=link.item_id,
            display_name=None if is_demo else link.slug_name,
            fetched_at=utc_now(),
            stub_scenario=scenario,
            **SAMPLES[scenario],
        )
