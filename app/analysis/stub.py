"""Sample checks from the needl-ui mock (NEEDL_ANALYZER=stub)."""

import asyncio
import json
from pathlib import Path

from pydantic import TypeAdapter

from ..contract.common import LocalText
from ..contract.result import Check, SuggestedStore
from ..jobs.progress import ANALYSIS_STEPS
from ..sources.base import ProgressFn, RawListing
from .base import AnalysisOutput

# Exported from needl-ui/src/data (checks, stores, questions), with a `code` per check.
DATA = json.loads(Path(__file__).with_name("stub_data.json").read_text())

_checks = TypeAdapter(list[Check])
_stores = TypeAdapter(list[SuggestedStore])
_questions = TypeAdapter(list[LocalText])


class StubAnalyzer:
    def __init__(self, step_ms: int):
        self.step_s = step_ms / 1000

    async def run(self, listing: RawListing, report: ProgressFn) -> AnalysisOutput:
        for _, progress in ANALYSIS_STEPS:
            await asyncio.sleep(self.step_s)
            await report(progress)
        sample = DATA[listing.stub_scenario or "risky"]
        return AnalysisOutput(
            checks=_checks.validate_python(sample["checks"]),
            stores=_stores.validate_python(sample["stores"]),
            questions=_questions.validate_python(sample["questions"]),
        )
