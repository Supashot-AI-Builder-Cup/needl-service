"""Risk score and verdict, computed in code from check severities."""

from typing import Iterable, Literal

from ..contract.common import RiskLevel
from ..contract.result import Check

WEIGHTS = {"critical": 20, "major": 8, "minor": 3, "pass": 0}
# Zones match the gauge in the UI: 0-32 safe, 33-66 careful, 67-100 high.
SUSPICIOUS_FROM = 33
FRAUD_FROM = 67


def risk_score(checks: Iterable[Check]) -> int:
    return min(100, sum(WEIGHTS[c.severity.value] for c in checks))


def risk_level(score: int) -> RiskLevel:
    if score >= FRAUD_FROM:
        return RiskLevel.fraud
    if score >= SUSPICIOUS_FROM:
        return RiskLevel.suspicious
    return RiskLevel.safe


def ui_verdict(level: RiskLevel) -> Literal["high", "safe"]:
    return "safe" if level == RiskLevel.safe else "high"
