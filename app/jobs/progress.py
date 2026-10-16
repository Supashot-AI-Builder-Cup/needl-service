"""Progress numbers. The UI maps them to the 8 steps of its checking log."""

from datetime import datetime

PENDING = 0
STARTED = 5
PREVIEW = 30
SAVING = 99
DONE = 100

# (step, progress when the step is done), in the order of the UI log.
ANALYSIS_STEPS = [
    ("store_name", 38),
    ("risky_terms", 46),
    ("claims", 54),
    ("review_patterns", 62),
    ("buyer_photos", 70),
    ("complaints", 78),
    ("safer_stores", 86),
    ("questions", 94),
]

FAST_POLL_S = 2
SLOW_POLL_S = 5
FAST_POLL_WINDOW_S = 30


def poll_after_seconds(created_at: datetime, now: datetime) -> int:
    return FAST_POLL_S if (now - created_at).total_seconds() < FAST_POLL_WINDOW_S else SLOW_POLL_S
