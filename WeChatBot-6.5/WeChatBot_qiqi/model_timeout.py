"""Shared model request timeout policy."""

import math


MIN_MODEL_TIMEOUT_SECONDS = 5.0
MAX_MODEL_TIMEOUT_SECONDS = 600.0
GROK_MIN_TIMEOUT_SECONDS = 180.0


def _coerce_finite_seconds(value, fallback):
    if isinstance(value, bool):
        value = fallback
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = float(fallback)
    if not math.isfinite(seconds):
        seconds = float(fallback)
    return seconds


def resolve_model_timeout_seconds(model, configured, default=60):
    """Resolve a bounded timeout, allowing Grok models more response time."""
    fallback = _coerce_finite_seconds(default, 60)
    seconds = _coerce_finite_seconds(configured, fallback)
    seconds = max(MIN_MODEL_TIMEOUT_SECONDS, min(MAX_MODEL_TIMEOUT_SECONDS, seconds))
    if "grok" in str(model or "").casefold():
        seconds = max(GROK_MIN_TIMEOUT_SECONDS, seconds)
    return seconds
