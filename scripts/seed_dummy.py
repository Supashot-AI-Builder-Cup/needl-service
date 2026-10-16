"""Insert stub verification records into the configured SQLite database."""

import argparse
import asyncio
from datetime import timedelta
from pathlib import Path

from app.analysis.build import build_result
from app.analysis.stub import StubAnalyzer
from app.config import load_settings
from app.db.repository import Repository, utc_now
from app.sources.base import RawListing
from app.sources.stub import DEMO_LINKS, SAMPLES


async def seed(db_path: Path, count: int) -> None:
    repo = await Repository.open(db_path)
    try:
        analyzer = StubAnalyzer(0)
        async def report(_: int) -> None:
            pass

        for index in range(count):
            scenario = "safe" if index % 2 else "risky"
            link = DEMO_LINKS[1 if scenario == "safe" else 0]
            listing = RawListing(
                source_kind="url",
                requested_url=link,
                canonical_url=link,
                fetched_at=utc_now(),
                stub_scenario=scenario,
                **SAMPLES[scenario],
            )
            result = build_result(listing, await analyzer.run(listing, report))
            result.checked_at = utc_now() - timedelta(seconds=index)
            await repo.insert_verification(result)
    finally:
        await repo.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10, help="records to insert (default: 10)")
    parser.add_argument("--db", type=Path, default=load_settings().db_path, help="SQLite file path")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be at least 1")
    asyncio.run(seed(args.db, args.count))
    print(f"Inserted {args.count} dummy verifications into {args.db}")


if __name__ == "__main__":
    main()
