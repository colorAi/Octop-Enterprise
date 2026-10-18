"""The bridge exposes a fixed method/path surface, never arbitrary upstream URLs."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    permission: str
    summary: str


ROUTES = (
    Route("GET", "/directory-settings", "contacts", "Read company directory name"),
    Route("GET", "/organization/directory-settings", "departments", "Read directory settings"),
    Route("PUT", "/organization/directory-settings", "departments", "Set company directory name"),
    Route(
        "GET",
        "/organization/users",
        "departments",
        "List communication identities for department management",
    ),
    Route(
        "GET",
        "/organization/groups/{id}",
        "departments",
        "Read department-group administration metadata",
    ),
    Route("GET", "/conversations", "messaging", "List conversations"),
    Route("POST", "/conversations/direct", "messaging", "Start a company direct conversation"),
    Route("GET", "/conversations/{id}/messages", "messaging", "Read visible message history"),
    Route(
        "GET",
        "/conversations/{id}/messages/{message_id}",
        "messaging",
        "Preview a visible original message",
    ),
    Route(
        "DELETE",
        "/conversations/{id}",
        "messaging",
        "Delete own conversation and clear own history",
    ),
    Route("POST", "/conversations/{id}/messages", "messaging", "Send a text or image message"),
    Route("POST", "/conversations/{id}/read", "messaging", "Advance read sequence"),
    Route("PATCH", "/conversations/{id}/state", "messaging", "Update pinned or muted state"),
    Route("POST", "/conversations/{id}/hide", "messaging", "Hide a conversation"),
    Route("POST", "/conversations/{id}/clear", "messaging", "Clear visible history"),
    Route("POST", "/messages/{id}/recall", "messaging", "Recall own message"),
    Route("POST", "/messages/{id}/forward", "messaging", "Forward a visible message"),
    Route("GET", "/groups", "messaging", "List joined groups, including hidden conversations"),
    Route("POST", "/groups", "groups_create", "Create a project group with explicit members"),
    Route("PATCH", "/groups/{id}", "messaging", "Update group settings as owner or administrator"),
    Route("POST", "/groups/{id}/members", "messaging", "Invite explicit group members"),
    Route("DELETE", "/groups/{id}/members", "messaging", "Remove explicit group members"),
    Route("PATCH", "/groups/{id}/members/{user_id}", "messaging", "Set group member role or mute"),
    Route(
        "POST",
        "/groups/{id}/leave",
        "messaging",
        "Leave a project group after transferring ownership",
    ),
    Route("PUT", "/groups/{id}/owner", "messaging", "Transfer ownership to an active member"),
    Route(
        "POST", "/groups/{id}/archive", "messaging", "Archive a project group, preserving history"
    ),
    Route(
        "GET",
        "/organization/groups",
        "departments",
        "List group administration metadata without history",
    ),
    Route(
        "POST",
        "/organization/company-group",
        "departments",
        "Create or return the unique company group",
    ),
    Route(
        "POST",
        "/organization/groups/{id}/members",
        "departments",
        "Explicitly grant group membership",
    ),
    Route(
        "DELETE",
        "/organization/groups/{id}/members",
        "departments",
        "Revoke explicit group membership",
    ),
    Route(
        "POST",
        "/organization/groups/{id}/archive",
        "departments",
        "Archive a managed project group",
    ),
    Route("GET", "/groups/{id}", "messaging", "Read group settings as a member"),
    Route("GET", "/groups/{id}/members", "messaging", "List active group members"),
    Route("PATCH", "/groups/{id}/my-membership", "messaging", "Update own group nickname"),
    Route("GET", "/sync", "messaging", "Read incremental account events"),
    Route("POST", "/sync/snapshot", "messaging", "Create an atomic account snapshot"),
    Route("GET", "/sync/snapshots/{id}", "messaging", "Read a snapshot chunk"),
    Route("GET", "/media/uploads/{id}", "messaging", "Read own image processing status"),
    Route("POST", "/media/uploads", "messaging", "Begin a private image upload"),
    Route("PUT", "/media/uploads/{id}/content", "messaging", "Stream image upload bytes"),
    Route("POST", "/media/uploads/{id}/complete", "messaging", "Complete image upload"),
    Route("DELETE", "/media/uploads/{id}", "messaging", "Abort image upload"),
    Route("GET", "/media/{id}", "messaging", "Download a visible image variant"),
    Route("GET", "/contacts", "contacts", "Search active company colleagues"),
    Route("GET", "/presence", "contacts", "List online company colleagues"),
    Route("GET", "/departments", "contacts", "Read enabled company departments and counts"),
    Route("GET", "/organization/departments", "departments", "List organization departments"),
    Route("GET", "/organization/job-titles", "departments", "List reusable company job titles"),
    Route("POST", "/organization/job-titles", "departments", "Create or reuse a company job title"),
    Route(
        "PUT", "/organization/users/{id}/job-title", "departments", "Assign an employee job title"
    ),
    Route("POST", "/organization/departments", "departments", "Create a department"),
    Route(
        "PATCH",
        "/organization/departments/{id}",
        "departments",
        "Update department hierarchy or state",
    ),
    Route("PUT", "/organization/users/{id}/department", "departments", "Assign primary department"),
    Route(
        "POST",
        "/organization/departments/{id}/group",
        "departments",
        "Create or return department group",
    ),
    Route(
        "PATCH",
        "/organization/groups/{id}/members/{user_id}",
        "departments",
        "Update department-group member role or mute",
    ),
    Route(
        "PUT",
        "/organization/groups/{id}/owner",
        "departments",
        "Assign a current department-group owner",
    ),
    Route("PATCH", "/organization/groups/{id}", "departments", "Update department group settings"),
)


def match_route(method: str, path: str) -> Route | None:
    for route in ROUTES:
        pattern = (
            re.escape(route.path)
            .replace(r"\{id\}", r"[A-Za-z0-9:_-]{1,160}")
            .replace(r"\{user_id\}", r"[A-Za-z0-9:_-]{1,160}")
            .replace(r"\{message_id\}", r"[A-Za-z0-9:_-]{1,160}")
        )
        if route.method == method and re.fullmatch(pattern, path):
            return route
    return None
