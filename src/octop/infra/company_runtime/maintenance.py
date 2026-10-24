"""Read-only capacity visibility. Recovery receipts and pinned skills are retained."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from octop.infra.db.repos.company_runtime import CompanyRuntimeRepo


def storage_summary(db: Any, root: Path) -> dict[str, Any]:
    summary = CompanyRuntimeRepo(db).storage_summary()
    cache = root / "company-runtime-skills"
    count = size = temporary_count = temporary_size = 0
    if cache.is_dir():
        with os.scandir(cache) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    try:
                        length = entry.stat(follow_symlinks=False).st_size
                        if re.fullmatch(r"[0-9a-f]{64}", entry.name):
                            size += length
                            count += 1
                        elif entry.name.startswith(".pending-"):
                            temporary_size += length
                            temporary_count += 1
                    except FileNotFoundError:
                        continue
    summary["skill_cache"] = {"files": count, "bytes": size}
    summary["temporary_cache"] = {"files": temporary_count, "bytes": temporary_size}
    summary["automatic_deletion_enabled"] = False
    return summary
