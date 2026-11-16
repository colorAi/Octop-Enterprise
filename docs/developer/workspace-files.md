# Workspace content access

Agent workspace content is read and written through the harness `BackendWorkspace`. HTTP handlers use `require_running_workspace()` and `workspace_api_path()`; gateway and media code use `harness_workspace_for_agent()`.

Pass workspace-relative content paths such as `SOUL.md` or `skills/example/SKILL.md`. `.` identifies the current workspace directory; `/` has a distinct backend-root meaning. Do not switch between local filesystem access and backend access based on storage type.

Database files, checkpoints, authentication material and harness-managed session files remain system data. A workspace content adapter does not move these into a remote content backend.

Docker isolation and recovery are operator policies documented in [employee isolation](../user/employee-isolation.md). Module boundaries and the relevant helpers are listed in [AGENTS.md](../../AGENTS.md).
