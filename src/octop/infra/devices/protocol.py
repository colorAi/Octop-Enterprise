"""Bounded device frames; v1 diagnostics remain compatible with v2 file tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROTOCOL_VERSION = 2
MAX_FRAME_BYTES = 1024 * 1024
HEARTBEAT_SECONDS = 15
IDLE_TIMEOUT_SECONDS = 45
PROBE_TOOL = "device_status"
FILE_TOOLS = frozenset(
    {"local_list_directory", "local_read_file", "local_write_file", "local_operation_status"}
)


class Frame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DirectoryGrant(Frame):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    name: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)
    writable: bool
    allow_company: bool

    @model_validator(mode="before")
    @classmethod
    def legacy_consent(cls, data: Any) -> Any:
        # Keep protocol-v2 clients compatible; conflicting consent must never grant access.
        if isinstance(data, dict) and "allow_h3c" in data:
            data = dict(data)
            legacy = data.pop("allow_h3c")
            if "allow_company" in data and data["allow_company"] != legacy:
                raise ValueError("conflicting directory consent")
            data.setdefault("allow_company", legacy)
        return data


class DeviceSelection(Frame):
    mode: Literal["local", "auto"]
    device_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    grant_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    grant_version: int = Field(ge=1)


class Hello(Frame):
    type: Literal["pair", "hello"]
    credential: str = Field(min_length=32, max_length=128)
    protocol_version: Literal[1, 2]
    name: str = Field(min_length=1, max_length=80, pattern=r"^[^\x00-\x1f\x7f]+$")
    platform: Literal["windows", "darwin"]
    capabilities: list[Literal["device_status", "local_files"]] = Field(max_length=2)
    grants: list[DirectoryGrant] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def compatible(self) -> Hello:
        if self.protocol_version == 1 and (self.grants or "local_files" in self.capabilities):
            raise ValueError("v1 has no file capabilities")
        if len({g.id for g in self.grants}) != len(self.grants):
            raise ValueError("duplicate grants")
        return self


class ProbeResult(Frame):
    platform: Literal["windows", "darwin"]
    protocol_version: Literal[1, 2]


class FileResult(Frame):
    kind: Literal["local_file", "local_directory", "local_text", "local_operation", "local_error"]
    status: Literal["completed", "failed", "uncertain", "not_found"]
    path: str = Field(default="", max_length=512)
    text: str = Field(default="", max_length=128 * 1024)
    entries: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    truncated: bool = False
    sha256: str = Field(default="", max_length=64)
    bytes: int = Field(default=0, ge=0)
    error: str = Field(default="", max_length=256)
    operation_id: str = Field(default="", max_length=64)


class DeviceMessage(Frame):
    type: Literal["heartbeat", "result", "cancelled", "revoke", "grants"]
    call_id: str | None = Field(default=None, min_length=1, max_length=64)
    result: ProbeResult | FileResult | None = None
    grants: list[DirectoryGrant] = Field(default_factory=list, max_length=16)
