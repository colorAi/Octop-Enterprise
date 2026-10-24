"""Conservative admission presets; these are budgets, not measured capacities."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionProfile:
    running: int
    queued: int
    threads: int
    sandbox_memory: int
    sandbox_total_memory: int


def profile() -> ExecutionProfile:
    name = os.environ.get("OCTOP_RESOURCE_PROFILE", "4g")
    values = {
        "4g": ExecutionProfile(2, 128, 8, 256 * 1024**2, 512 * 1024**2),
        "8g": ExecutionProfile(4, 256, 12, 384 * 1024**2, 1536 * 1024**2),
        "16g": ExecutionProfile(8, 512, 16, 512 * 1024**2, 4096 * 1024**2),
    }
    if name not in values:
        raise ValueError("OCTOP_RESOURCE_PROFILE must be 4g, 8g or 16g")
    return values[name]


def memory_bytes(value: str) -> int:
    """Explicit binary Docker-style sizes; reject zero, fractions and unknown units."""
    match = re.fullmatch(r"([1-9][0-9]*)\s*([bkmg]?)", value.strip().lower())
    if not match:
        raise ValueError("memory must be positive bytes or an integer with k/m/g suffix")
    return int(match[1]) * {"": 1, "b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[match[2]]


def model_concurrency() -> int:
    """An operator opt-in limit, independent of host/tool resource presets."""
    value = os.environ.get("OCTOP_MODEL_CONCURRENCY", "0").strip()
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("OCTOP_MODEL_CONCURRENCY must be 0 (unlimited) or a positive integer")
    return int(value)
