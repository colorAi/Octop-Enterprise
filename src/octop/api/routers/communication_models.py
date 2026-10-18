"""Public communication payload schemas, also used by generated API documentation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Envelope(BaseModel):
    request_id: str
    data: dict[str, Any] = Field(description="Endpoint result; sequence fields are decimal strings")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DirectBody(Body):
    peer_user_id: str = Field(description="Stable communication user id from authorized contacts")


class DirectorySettingsBody(Body):
    name: str = Field(max_length=80, description="Company directory label; empty restores default")


class MessageBody(Body):
    client_message_id: str | None = Field(None, description="Stable retry key for this send")
    text: str | None = None
    asset_ids: list[str] = Field(default_factory=list)
    reply_to_message_id: str | None = None
    mention_user_ids: list[str] = Field(default_factory=list)
    mention_all: bool = False


class ReadBody(Body):
    last_read_seq: str = Field(pattern=r"^\d+$")


class StateBody(Body):
    pinned: bool | None = None
    muted: bool | None = None


class ClearBody(Body):
    through_seq: str | None = Field(None, pattern=r"^\d+$")


class ForwardBody(Body):
    to_conversation_ids: list[str]
    client_message_id: str | None = None


class UploadBody(Body):
    purpose: Literal["chat_image", "chat_file"]
    mime: str = Field(
        description="Image MIME for chat_image; file MIME or application/octet-stream"
    )
    filename: str | None = Field(
        None, max_length=255, description="Original file name for downloads"
    )
    size_bytes: int = Field(
        gt=0, le=50 << 20, description="Images up to 10 MiB; files up to 50 MiB"
    )
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class DepartmentBody(Body):
    name: str | None = Field(None, min_length=1, max_length=100)
    parent_id: str | None = None
    sort_order: int | None = None
    enabled: bool | None = None


class AssignmentBody(Body):
    department_id: str | None = Field(description="Null removes the primary department")


class JobTitleBody(Body):
    name: str = Field(min_length=1, max_length=40, description="Reusable company job title")


class JobTitleAssignmentBody(Body):
    job_title_id: str | None = Field(description="Null removes the employee job title")


class GroupOperationBody(Body):
    request_id: str = Field(
        min_length=1, max_length=128, description="Stable request ID reused for retries"
    )


class ProjectGroupBody(GroupOperationBody):
    name: str = Field(min_length=1, max_length=100)
    user_ids: list[str] = Field(
        default_factory=list,
        description="Invited active colleagues; creator is included automatically",
    )


class CompanyGroupBody(Body):
    request_id: str | None = Field(None, min_length=1, max_length=128)
    name: str | None = Field(None, min_length=1, max_length=100)


class GroupMembersBody(GroupOperationBody):
    user_ids: list[str] = Field(min_length=1)


class GroupBody(Body):
    request_id: str | None = Field(None, min_length=1, max_length=128)
    members_can_invite: bool | None = None
    name: str | None = Field(None, min_length=1, max_length=100)
    announcement: str | None = Field(None, max_length=500)
    mute_all: bool | None = None


class MembershipBody(Body):
    my_nickname: str = Field(max_length=16)


class GroupMemberBody(Body):
    request_id: str | None = Field(None, min_length=1, max_length=128)
    role: Literal["admin", "member"] | None = None
    mute_until: str | None = Field(None, description="Future RFC3339 timestamp, or null to unmute")


class GroupOwnerBody(Body):
    request_id: str | None = Field(None, min_length=1, max_length=128)
    user_id: str = Field(description="Current active group member")


class GroupUpdateBody(GroupBody, GroupOperationBody):
    request_id: str = Field(min_length=1, max_length=128)


class GroupMemberUpdateBody(GroupMemberBody, GroupOperationBody):
    request_id: str = Field(min_length=1, max_length=128)


class GroupOwnerUpdateBody(GroupOwnerBody, GroupOperationBody):
    request_id: str = Field(min_length=1, max_length=128)


BODY_MODELS: dict[str, type[BaseModel]] = {
    "/groups": ProjectGroupBody,
    "/groups/{id}": GroupUpdateBody,
    "/groups/{id}/members": GroupMembersBody,
    "/groups/{id}/members/{user_id}": GroupMemberUpdateBody,
    "/groups/{id}/owner": GroupOwnerUpdateBody,
    "/groups/{id}/leave": GroupOperationBody,
    "/groups/{id}/archive": GroupOperationBody,
    "/organization/company-group": CompanyGroupBody,
    "/organization/groups/{id}/members": GroupMembersBody,
    "/organization/groups/{id}/archive": GroupOperationBody,
    "/organization/job-titles": JobTitleBody,
    "/organization/users/{id}/job-title": JobTitleAssignmentBody,
    "/organization/directory-settings": DirectorySettingsBody,
    "/conversations/direct": DirectBody,
    "/conversations/{id}/messages": MessageBody,
    "/conversations/{id}/read": ReadBody,
    "/conversations/{id}/state": StateBody,
    "/conversations/{id}/clear": ClearBody,
    "/messages/{id}/forward": ForwardBody,
    "/media/uploads": UploadBody,
    "/organization/departments": DepartmentBody,
    "/organization/departments/{id}": DepartmentBody,
    "/organization/users/{id}/department": AssignmentBody,
    "/organization/groups/{id}": GroupBody,
    "/groups/{id}/my-membership": MembershipBody,
    "/organization/groups/{id}/members/{user_id}": GroupMemberBody,
    "/organization/groups/{id}/owner": GroupOwnerBody,
}
