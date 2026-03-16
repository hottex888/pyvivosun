"""Utility helpers for value scaling and validation."""

from .const import (
    MAX_FAN_LEVEL,
    MAX_LIGHT_LEVEL,
    MIN_FAN_LEVEL,
    MIN_LIGHT_LEVEL,
    SCALE_DIVISOR,
    SENTINEL_VALUE,
)


def scale_value(raw: int | float, divisor: int = SCALE_DIVISOR) -> float:
    """Scale a raw API value by the divisor (default 100)."""
    return raw / divisor


def is_sentinel(val: int | float, sentinel: int = SENTINEL_VALUE) -> bool:
    """Check if a value is the sentinel indicating unavailable data."""
    return val == sentinel


def clamp_light_level(level: int) -> int:
    """Clamp a light level to the valid range [25, 100]."""
    return max(MIN_LIGHT_LEVEL, min(MAX_LIGHT_LEVEL, level))


def clamp_fan_level(level: int) -> int:
    """Clamp a fan level to the valid range [0, 10]."""
    return max(MIN_FAN_LEVEL, min(MAX_FAN_LEVEL, level))
