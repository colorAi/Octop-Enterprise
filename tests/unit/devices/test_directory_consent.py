"""Canonical and legacy directory consent must enforce the same boundary."""

import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from octop.infra.devices.protocol import DeviceSelection, DirectoryGrant, Hello
from octop.infra.devices.tasks import DeviceTask


@pytest.mark.parametrize("consent", [True, False])
@pytest.mark.parametrize("field", ["allow_company", "allow_h3c"])
def test_directory_consent_accepts_current_and_legacy_frames(field, consent):
    grant = {"id": "a" * 32, "name": "Work", "version": 1, "writable": True, field: consent}
    hello = Hello.model_validate(
        {
            "type": "hello",
            "credential": "x" * 32,
            "protocol_version": 2,
            "name": "Employee device",
            "platform": "darwin",
            "capabilities": ["local_files"],
            "grants": [grant],
        }
    )
    assert hello.grants[0].allow_company is consent
    assert hello.grants[0].model_dump()["allow_company"] is consent
    task = DeviceTask(
        "token",
        7,
        "agent",
        "thread",
        "session",
        DeviceSelection(mode="local", device_id="b" * 32, grant_id="a" * 32, grant_version=1),
        int(time.time()) + 60,
    )
    connection = SimpleNamespace(row={"owner_user_id": 7}, grants=hello.grants)
    assert task.valid_grant(connection) is consent


@pytest.mark.parametrize("current,legacy", [(True, False), (False, True)])
def test_conflicting_consent_is_rejected(current, legacy):
    with pytest.raises(ValidationError, match="conflicting directory consent"):
        DirectoryGrant(
            id="a" * 32,
            name="Work",
            version=1,
            writable=True,
            allow_company=current,
            allow_h3c=legacy,
        )


def test_consent_stays_required_and_strict():
    grant = {"id": "a" * 32, "name": "Work", "version": 1, "writable": True}
    with pytest.raises(ValidationError):
        DirectoryGrant.model_validate(grant)
    for field in ("allow_company", "allow_h3c"):
        with pytest.raises(ValidationError):
            DirectoryGrant.model_validate({**grant, field: "true"})
