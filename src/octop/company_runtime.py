"""Company-only process entry: JSONL IPC; deliberately starts no HTTP server."""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from typing import Any

from octop.infra.company_runtime.runtime import EnterpriseRuntime


def main() -> None:
    # Windows redirected pipes may otherwise use the legacy GBK code page.
    # JSONL and diagnostics must accept the same Unicode as model/file content.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    output = sys.stdout
    # Third-party diagnostics must not corrupt the IPC stream or expose credentials.
    sys.stdout = sys.stderr

    def emit(value: Any) -> None:
        output.write(
            json.dumps(
                value,
                ensure_ascii=False,
                default=lambda obj: obj.model_dump() if hasattr(obj, "model_dump") else str(obj),
            )
            + "\n"
        )
        output.flush()

    async def run() -> None:
        runtime = EnterpriseRuntime(emit)
        loop = asyncio.get_running_loop()
        current = asyncio.current_task()
        if sys.platform != "win32" and current:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, current.cancel)
        try:
            while True:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                request: dict[str, Any] = {}
                try:
                    if len(line) > 2 * 1024 * 1024:
                        raise ValueError("ipc_request_too_large")
                    request = json.loads(line)
                    method, params = request["method"], request.get("params", {})
                    if method == "initialize":
                        result = await runtime.initialize(params)
                    elif method == "status":
                        result = runtime.status()
                    elif method == "start":
                        result = await runtime.start(params)
                    elif method == "reconcile":
                        result = await runtime.reconcile(params)
                    elif method == "cancel":
                        result = await runtime.cancel(params)
                    elif method == "steer":
                        result = runtime.steering.send(params)
                    elif method == "steer_remove":
                        result = runtime.steering.remove(params)
                    elif method == "hitl_resume":
                        result = runtime.steering.approvals.resolve(params)
                    else:
                        raise ValueError("unknown_runtime_method")
                    emit({"id": request.get("id"), "result": result})
                except Exception as exc:
                    emit({"id": request.get("id"), "error": str(exc)})
        finally:
            await runtime.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
