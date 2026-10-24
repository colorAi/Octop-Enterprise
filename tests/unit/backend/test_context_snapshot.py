import io
import tarfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docker.errors import NotFound

from octop.infra.backend.context_snapshot import isolated_context_workspace


@pytest.fixture
def reader(monkeypatch):
    container = Mock()
    docker = SimpleNamespace(
        volumes=SimpleNamespace(
            get=Mock(return_value=SimpleNamespace(attrs={"Labels": {"octop.agent.id": "expert"}}))
        ),
        containers=SimpleNamespace(create=Mock(return_value=container)),
        close=Mock(),
    )
    monkeypatch.setattr("docker.client.DockerClient.from_env", lambda **_: docker)
    spec = {"volume": "private-volume", "agent_id": "expert", "image": "company-image"}
    return docker, container, spec


def archive(content=b"employee context", *, link=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        entry = tarfile.TarInfo("USER.md")
        if link:
            entry.type, entry.linkname = tarfile.SYMTYPE, "/other-user/USER.md"
            tar.addfile(entry)
        else:
            entry.size = len(content)
            tar.addfile(entry, io.BytesIO(content))
    yield output.getvalue()


@pytest.mark.asyncio
async def test_existing_private_context_read_never_starts_or_executes_container(reader):
    docker, container, spec = reader

    def get(path):
        if path != "/context/USER.md":
            raise NotFound("missing")
        return archive(), {"size": 16}

    container.get_archive.side_effect = get
    workspace = isolated_context_workspace(spec)
    assert await workspace.aread_text("USER.md") == "employee context"
    assert await workspace.aread_text("MEMORY.md") is None
    options = docker.containers.create.call_args.kwargs
    assert options["volumes"] == {"private-volume": {"bind": "/context", "mode": "ro,nocopy"}}
    assert options["network_mode"] == "none" and options["read_only"]
    container.start.assert_not_called()
    container.exec_run.assert_not_called()
    container.remove.assert_called_once()
    docker.close.assert_called_once()


@pytest.mark.asyncio
async def test_new_expert_without_volume_allocates_nothing(reader):
    docker, container, spec = reader
    docker.volumes.get.side_effect = NotFound("no volume")
    workspace = isolated_context_workspace(spec)
    assert await workspace.aread_text("USER.md") is None
    docker.containers.create.assert_not_called()
    docker.close.assert_called_once()


@pytest.mark.parametrize("invalid", ["owner", "driver", "symlink", "oversized", "unavailable"])
def test_context_reader_rejects_wrong_volume_links_and_oversized_content(reader, invalid):
    docker, container, spec = reader
    if invalid == "owner":
        docker.volumes.get.return_value.attrs["Labels"]["octop.agent.id"] = "other"
    elif invalid == "driver":
        docker.volumes.get.return_value.attrs["Options"] = {"device": "/host"}
    elif invalid == "unavailable":
        container.get_archive.side_effect = RuntimeError("Docker unavailable")
    else:
        container.get_archive.return_value = (
            archive(link=invalid == "symlink"),
            {"size": 262145 if invalid == "oversized" else 16},
        )
    with pytest.raises((RuntimeError, ValueError)):
        isolated_context_workspace(spec)
    container.start.assert_not_called()
    if invalid not in {"owner", "driver"}:
        container.remove.assert_called_once()
    docker.close.assert_called_once()
