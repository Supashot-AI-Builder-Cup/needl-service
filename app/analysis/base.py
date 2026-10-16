from dataclasses import dataclass, field
from typing import Protocol

from ..contract.common import LocalText
from ..contract.result import Check, SuggestedStore
from ..sources.base import ProgressFn, RawListing


@dataclass
class AnalysisOutput:
    checks: list[Check]
    stores: list[SuggestedStore] = field(default_factory=list)
    questions: list[LocalText] = field(default_factory=list)


class AnalysisError(Exception):
    pass


class Analyzer(Protocol):
    async def run(self, listing: RawListing, report: ProgressFn) -> AnalysisOutput: ...
