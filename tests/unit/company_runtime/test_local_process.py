"""Real child processes must not consume the stdio worker's private input."""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from octop.infra.company_runtime.local import ProjectBackend, windows_python_command


def test_windows_image_inspection_keeps_python_code_and_paths_intact():
    executable = r"C:\Users\HooToo\.octop\company-runtime\tools\hash\runtime\python.exe"
    code = "from PIL import Image; im=Image.open(r'D:\\work\\octop\\generated\\cat_test.png'); print(im.size, im.format)"
    assert windows_python_command(f'"{executable}" -c "{code}"') == [
        executable,
        "-X",
        "utf8",
        "-u",
        "-c",
        code,
    ]
    spaced = r"C:\Program Files\工作目录\python.exe"
    assert windows_python_command(f'"{spaced}" -c "print(\'中文 📝\')"')[-1] == "print('中文 📝')"


@pytest.mark.parametrize(
    "command",
    [
        '"C:\\Python\\python.exe" -c "print(1)" && echo done',
        '"C:\\Python\\python.exe" -c "print(1)" > output.txt',
        '"C:\\Windows\\cmd.exe" -c "echo done"',
        '"python.exe" -c "print(1)"',
    ],
)
def test_shell_commands_keep_their_shell_semantics(command):
    assert windows_python_command(command) is None


@pytest.mark.asyncio
async def test_tool_stdin_is_eof_while_worker_protocol_pipe_stays_open(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    script = """import asyncio,json,sys
from pathlib import Path
from octop.infra.company_runtime.local import ProjectBackend
async def run():
    backend=ProjectBackend(Path(sys.argv[1]),Path(sys.argv[2]),lambda event:None,tool_path=str(Path(sys.executable).parent))
    result=await backend.aexecute('"'+sys.executable+'" -c "import sys; print(len(sys.stdin.read()))"',timeout=1)
    print(json.dumps({'exit':result.exit_code,'output':result.output}),flush=True)
asyncio.run(run())
"""
    worker = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(project),
        str(tmp_path / "state"),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=os.environ.copy(),
    )
    try:
        # communicate() would close stdin and hide the inherited-input defect.
        await asyncio.wait_for(worker.wait(), timeout=10)
        output = await worker.stdout.read()
        errors = await worker.stderr.read()
        assert worker.returncode == 0, errors.decode()
        result = json.loads(output)
        assert result["exit"] == 0
        assert result["output"].strip() == "stdout:\n0\n\nstderr:".strip()
    finally:
        worker.stdin.close()
        if worker.returncode is None:
            worker.kill()
            await worker.wait()


@pytest.mark.asyncio
async def test_image_inspection_returns_size_and_format_from_real_python(tmp_path):
    project = tmp_path / "项目 space"
    project.mkdir()
    image = project / "cat_test.png"
    Image.new("RGB", (37, 19), "blue").save(image)
    backend = ProjectBackend(
        project, tmp_path / "state", lambda event: None, tool_path=str(Path(sys.executable).parent)
    )
    result = await backend.aexecute(
        f'"{sys.executable}" -c "from PIL import Image; im=Image.open(r\'{image}\'); print(im.size, im.format)"',
        timeout=10,
    )
    assert result.exit_code == 0
    assert "(37, 19) PNG" in result.output


@pytest.mark.asyncio
async def test_long_command_budget_and_output_before_completion(tmp_path):
    events = []
    backend = ProjectBackend(
        tmp_path, tmp_path / "state", events.append, tool_path=str(Path(sys.executable).parent)
    )
    assert backend._default_timeout == 900
    task = asyncio.create_task(
        backend.aexecute(
            f'"{sys.executable}" -u -c "import time; print(123,flush=True); time.sleep(0.4); print(456)"'
        )
    )
    try:
        for _ in range(100):
            if any(event.get("event") == "process_output" for event in events):
                break
            await asyncio.sleep(0.01)
        assert not task.done()
        assert any("123" in event.get("content", "") for event in events)
        result = await asyncio.wait_for(task, 5)
        assert result.exit_code == 0 and "456" in result.output
        assert events[-1]["status"] == "completed"
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await backend.shutdown()


@pytest.mark.asyncio
async def test_timeout_returns_even_when_an_orphan_retains_output_pipe(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "killpg", lambda *_: None, raising=False)

    class RetainedPipe:
        async def read(self, count):
            await asyncio.Event().wait()

    process = type(
        "Process",
        (),
        {
            "pid": 12345,
            "returncode": 0,
            "stdout": RetainedPipe(),
            "stderr": RetainedPipe(),
            "wait": AsyncMock(),
        },
    )()
    monkeypatch.setattr(asyncio, "create_subprocess_shell", AsyncMock(return_value=process))
    backend = ProjectBackend(tmp_path, tmp_path / "state", lambda event: None, tool_path="")
    monkeypatch.setattr(backend, "stop_process", AsyncMock())
    result = await asyncio.wait_for(backend.aexecute("test", timeout=0.01), timeout=3)
    assert result.exit_code == 124
    assert "timed out" in result.output
    assert not backend.processes


@pytest.mark.asyncio
async def test_cancel_during_log_setup_stops_the_already_started_process(tmp_path, monkeypatch):
    import octop.infra.company_runtime.local as local

    original = local.offload
    entered = asyncio.Event()

    async def pause_setup(fn, *args, **kwargs):
        if getattr(fn, "__name__", "") == "mkdir":
            entered.set()
            await asyncio.Event().wait()
        return await original(fn, *args, **kwargs)

    monkeypatch.setattr(local, "offload", pause_setup)
    backend = ProjectBackend(
        tmp_path, tmp_path / "state", lambda event: None, tool_path=str(Path(sys.executable).parent)
    )
    task = asyncio.create_task(
        backend.aexecute(f'"{sys.executable}" -c "import time; time.sleep(30)"')
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    spawned = list(backend.processes)
    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
        assert not backend.processes
        assert all(process.returncode is not None for process in spawned)
    finally:
        for process in spawned:
            if process.returncode is None:
                await backend.stop_process(process)
