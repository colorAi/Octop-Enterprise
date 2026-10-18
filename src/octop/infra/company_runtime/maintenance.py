"""Read-only capacity visibility. Recovery receipts and pinned skills are retained."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from octop.infra.db.repos.company_runtime import CompanyRuntimeRepo


def storage_summary(db: Any, root: Path) -> dict[str, Any]:
    summary = CompanyRuntimeRepo(db).storage_summary()
    cache = root / "company-runtime-skills"
    count = size = 0
    if cache.is_dir():
        with os.scandir(cache) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    try:
                        size += entry.stat(follow_symlinks=False).st_size
                        count += 1
                    except FileNotFoundError:
                        continue
    summary["skill_cache"] = {"files": count, "bytes": size}
    summary["automatic_deletion_enabled"] = False
    return summary
