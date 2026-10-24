"""Context shared with leaf persistence code without importing orchestration."""

from contextvars import ContextVar

execution_run_id: ContextVar[str | None] = ContextVar("octop_execution_run_id", default=None)
