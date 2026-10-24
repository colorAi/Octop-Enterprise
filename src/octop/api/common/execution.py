"""Map shared execution conflicts into bounded, localized HTTP errors."""

from fastapi import Request
from fastapi.responses import JSONResponse

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.locale import resolve_request_locale


async def execution_error_response(request: Request, exc: Exception) -> JSONResponse:
    reason = str(exc)
    status = 409
    if reason in {
        "execution_queue_full",
        "execution_capacity_exceeded",
        "model_capacity_exceeded",
        "token_quota_exceeded",
    }:
        status = 429
    elif reason == "execution_payload_too_large":
        status = 413
    elif reason in {
        "executor_fenced",
        "executor_stop_unconfirmed",
        "runtime_host_stop_unconfirmed",
        "runtime_owner_mismatch",
        "sandbox_memory_exhausted",
        "sandbox_budget_uninitialized",
    }:
        status = 503
    code = (
        ErrorCode.SANDBOX_MEMORY_EXHAUSTED
        if reason == "sandbox_memory_exhausted"
        else ErrorCode.RUNTIME_UNAVAILABLE
        if status == 503
        else ErrorCode.FORBIDDEN
    )
    error = OctopError(code, reason, status=status, details={"reason": reason})
    return JSONResponse(
        status_code=status,
        content=error.to_envelope(locale=resolve_request_locale(request)),
        headers={"Retry-After": "5" if reason == "sandbox_memory_exhausted" else "1"}
        if status in {429, 503}
        else None,
    )
