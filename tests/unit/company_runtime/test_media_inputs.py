import base64
import hashlib

import pytest

from octop.infra.company_runtime.local import image_reference_inputs, local_attachment_metadata

from .test_runtime import acquire, install_agent


def test_reference_inputs_preserve_original_bytes_and_confine_paths(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    original = project / "原图.png"
    data = b"\x89PNG\r\n\x1a\noriginal"
    original.write_bytes(data)
    result = image_reference_inputs(project, [str(original)])
    assert base64.b64decode(result[0].split(",", 1)[1]) == data
    outside = tmp_path / "outside.png"
    outside.write_bytes(data)
    with pytest.raises(ValueError, match="inside this project"):
        image_reference_inputs(project, [str(outside)])
    assert original.read_bytes() == data


def test_local_attachment_metadata_checks_hash_and_preserves_receipt(tmp_path):
    original = tmp_path / "原图.png"
    data = b"\x89PNG\r\n\x1a\noriginal"
    original.write_bytes(data)
    file = {"path": str(original), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    row = local_attachment_metadata(tmp_path, [file])[0]
    assert row["workspace_path"] == str(original.resolve())
    assert row["sha256"] == file["sha256"]
    assert row["kind"] == "image" and row["execution_location"] == "desktop"
    with pytest.raises(ValueError, match="checksum"):
        local_attachment_metadata(tmp_path, [{**file, "sha256": "f" * 64}])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference", ["company-private.png", "https://arbitrary.test/image.png", "file:///private.png"]
)
async def test_native_image_api_does_not_resolve_company_files_or_urls(company, reference):
    server, client, _, _ = company
    install_agent(server)
    from types import SimpleNamespace

    agent = server.app_runtime.agent_registry.get_agent("expert")
    agent.config.media_generation = SimpleNamespace(has_api_key=lambda: True, image_enabled=True)
    await acquire(client)
    response = await client.http.post(
        "tasks/task1/images",
        json={"operation_id": "edit1", "prompt": "edit", "reference_images": [reference]},
    )
    assert response.status_code == 422
    assert server.services.company_runtime_repo.operation("task1", "edit1") is None
    await client.close()
