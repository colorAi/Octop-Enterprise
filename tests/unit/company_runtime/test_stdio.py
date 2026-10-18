"""Company JSONL remains UTF-8 even with Windows GBK redirected pipes."""

import io
import json

from octop import company_runtime


def test_unicode_stdio_with_gbk_pipes(monkeypatch):
    requests = (
        "\n".join(
            json.dumps(value, ensure_ascii=False)
            for value in [
                {"id": "1", "method": "status"},
                {"id": "2", "method": "initialize", "params": {"name": "中文 📝"}},
            ]
        )
        + "\n"
    )
    output = io.BytesIO()
    monkeypatch.setattr(
        company_runtime.sys,
        "stdin",
        io.TextIOWrapper(io.BytesIO(requests.encode()), encoding="gbk"),
    )
    stdout = io.TextIOWrapper(output, encoding="gbk")
    monkeypatch.setattr(company_runtime.sys, "stdout", stdout)
    monkeypatch.setattr(
        company_runtime.sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="gbk")
    )

    class Runtime:
        def __init__(self, emit):
            pass

        def status(self):
            return {"message": "中文任务 📝"}

        async def initialize(self, params):
            raise ValueError(params["name"])

        async def close(self):
            pass

    monkeypatch.setattr(company_runtime, "EnterpriseRuntime", Runtime)
    company_runtime.main()
    rows = [json.loads(line) for line in output.getvalue().decode("utf-8").splitlines()]
    assert rows[0]["result"]["message"] == "中文任务 📝"
    assert rows[1]["error"] == "中文 📝"
