# API Reference

Every route is mounted under `/api`. JSON unless otherwise noted; SSE
endpoints emit `text/event-stream`; chat turns use a WebSocket
described in [Chat (WebSocket)](#chat-websocket).

Company communication additions:

| Method | Path | Permission | Behavior |
|--------|------|------------|----------|
| `GET` | `/im/conversations/{id}/messages/{message_id}` | messaging | Original-message preview, respecting membership and cleared history; recalled messages return a tombstone |
| `DELETE` | `/im/conversations/{id}` | messaging | Hide and clear only the caller's view; other members keep their history, and new messages reopen the conversation |
| `GET/POST` | `/im/organization/job-titles` | departments | List reusable titles, or create/reuse a trimmed title (1–40 characters) |
| `PUT` | `/im/organization/users/{id}/job-title` | departments | Assign `job_title_id`, or `null` to remove the display title; no permission changes |
| `POST` | `/agents/{id}/private-instance` | authenticated | Reuse a shared expert as the caller's private instance; returns `agent_id` |

Message pages include `reply_preview` only when the referenced message is still
visible. Directory contacts include `is_admin`, `job_title_id` and `job_title`;
administrators are excluded from the company tree and headcounts. In operator
isolation mode Agent list items include `isolated` and `requires_private_instance`.
See [employee isolation](employee-isolation.md) for storage and execution boundaries.

> **Interactive docs** (Scalar) live at `/api/docs` when
> `enable_api_docs=true` (or `OCTOP_ENABLE_API_DOCS=1`). The schema
> is at `/api/openapi.json`.

## Authentication

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer <access_token>` from `POST /api/auth/login` |

Tokens expire after `OCTOP_ACCESS_TOKEN_TTL` seconds (default 24 h).
Rotating the JWT secret (`octop admin rotate-jwt-secret`) invalidates
every outstanding token immediately. Login attempts are rate-limited
(`OCTOP_LOGIN_MAX_ATTEMPTS` / `OCTOP_LOGIN_LOCKOUT_SECONDS`); the
admin can clear the lockout with `POST /api/users/{id}/unlock-login`.

### Auth column legend

- **public** — no token required.
- **user** — any logged-in account.
- **owner** — same user that owns the resource (or admin).
- **admin** — admin role required.

### Public endpoints (no token)

`/api/docs`, `/api/openapi.json`, `/api/health`, `/api/health/ready`, `/api/setup/*`,
`/api/auth/login`, `/api/auth/captcha`, `/api/auth/oidc/status`, `/api/auth/oidc/start`,
`/api/auth/oidc/callback`, `/api/auth/oidc/exchange`, `/api/auth/oauth/status`,
`/api/auth/oauth/start`, `/api/auth/oauth/callback`, `/api/auth/oauth/exchange`,
`/api/auth/ldap/status`, `/api/auth/invite/validate`, `/api/auth/invite/redeem`,
`/api/i18n/*`, and `/api/connectors/oauth/callback`.

Company runtime endpoints under `/api/company-runtime/*` use device credentials
rather than account JWTs. Internal MCP endpoints under `/api/internal/mcp/*`
also use their own authentication. Health routes bypass JWT middleware, but
`/api/health/storage` still requires an administrator through its route dependency.
Other routes are JWT-gated by `api/middleware/jwt_auth.py`; the setup lockdown middleware
(`api/middleware/setup_lockdown.py`) additionally blocks non-setup
routes until the wizard finishes.

`GET /api/company-runtime/capabilities` advertises `checkpoint_batches` for the
desktop worker. `POST /api/company-runtime/tasks/{task_id}/checkpoint-batch`
accepts 1–64 ordered `put`/`writes` operations and commits them atomically for
the owning device and latest task. Repeating an acknowledged batch is safe;
conflicting checkpoint identities roll back the batch. Running, cancellation-requested
and unknown tasks may receive pending writes; finished tasks reject new writes.
The existing individual checkpoint endpoints remain available to older clients.
Task creation also accepts `reasoning_mode`, `reasoning_effort` and
`conversation_mode`; model and tool policy are still enforced by the server
and local engine rather than supplied as arbitrary provider parameters.

For an assigned expert, the task's `expert` object also includes
`resource_manifest_sha256`. It identifies the immutable bundle whose definition
and approved assets were verified together. It does not grant access by itself:
`GET /api/company-runtime/tasks/{task_id}/skills/{digest}` still checks the device,
active task and current resource authorization. Missing, corrupt or symlinked
assets return `409` with reason `skill_checksum_mismatch`. Older clients may
ignore the additional manifest field.

Password login may require a vendor captcha token. `GET /api/auth/captcha`
returns `{provider: "slider"}` by default (dashboard slider only; no server
check). When a strong provider is active (`turnstile`, `hcaptcha`,
`recaptcha`, `recaptcha-v3`, `tencent`), `POST /api/auth/login` must include
`captcha_token` (max 4096 characters; for `tencent` the dashboard sends the
callback pair as `ticket:randstr`, verified via GET with the client IP).
Captcha failures do not increment
login lockout. OIDC login is unchanged. A request for an unknown username
with a garbage token still triggers one outbound siteverify request (bounded
by a 10s timeout and the token length limit); a known locked user is
rejected before siteverify.

Offline recovery: delete the `captcha.settings` row via local CLI /
`settings_repo` if a settings-sourced strong provider is unreachable. If
the process refused to start because `OCTOP_CAPTCHA_PROVIDER` is strong
without both keys and no readable settings blob, edit `~/.octop/env` on
disk — the dashboard and `PUT /api/envs` are unreachable until the
process starts. `GET /api/envs` redacts `OCTOP_CAPTCHA_SECRET` as
`********`; sending that sentinel on PUT keeps the on-disk value.

If you serve the dashboard behind a CSP, allow
`challenges.cloudflare.com`; `js.hcaptcha.com`, `newassets.hcaptcha.com`,
`api.hcaptcha.com`; `www.google.com`, `www.gstatic.com`;
`turing.captcha.qcloud.com`, `captcha.qq.com`, `ssl.captcha.qq.com`. Octop
does not set these headers itself.

## Setup & auth

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/setup/status` | public | `{required, completed, has_admin}` |
| `GET`    | `/setup/presets` | public | Provider templates for the wizard |
| `POST`   | `/setup/begin` | public | Begin a wizard session (no password) |
| `POST`   | `/setup/verify-password` | public | Exchange the wizard password for a session token |
| `GET`    | `/setup/validate-token` | public | Check a wizard session token |
| `POST`   | `/setup/initial-admin` | public | body `{username, password, display_name?, email?}` → `201` |
| `POST`   | `/setup/test-provider` | public | Ping a provider draft (kind/base_url/api_key/model) |
| `POST`   | `/setup/finish` | public | Finalise setup and unlock the rest of the API |
| `GET`    | `/auth/captcha` | public | `{provider, site_key?}` for the login widget; 503 while setup is required |
| `POST`   | `/auth/login` | public | body `{username, password, captcha_token?}` (`username` may be email) → `{access_token, role, user, ...}`. When no local password matches, the credentials are retried against the configured LDAP directory; a first directory login provisions an Octop account (unless `auto_provision` is off) and derives the role from `admin_groups` |
| `GET`    | `/auth/oidc/status` | public | OIDC login availability and provider display name |
| `POST`   | `/auth/oidc/start` | public | body `{redirect_after?}` → identity-provider authorization URL |
| `GET`    | `/auth/oidc/callback` | public | Identity-provider callback; redirects to dashboard login completion |
| `POST`   | `/auth/oidc/exchange` | public | body `{code}` → same JWT response as `/auth/login` |
| `GET`    | `/auth/oidc/config` | admin | OIDC provider configuration and callback URL; client secret is omitted |
| `PUT`    | `/auth/oidc/config` | admin | Write OIDC provider configuration; `client_secret` is write-only |
| `POST`   | `/auth/oidc/config/test` | admin | Verify configured discovery metadata and JWKS endpoint |
| `GET`    | `/auth/oauth/status` | public | `{providers:[{kind, display_name, enabled}]}` |
| `POST`   | `/auth/oauth/start` | public | body `{kind, redirect_after?}` → authorization URL |
| `GET`    | `/auth/oauth/callback` | public | App OAuth callback (`code` or DingTalk `authCode`); same completion page as OIDC |
| `POST`   | `/auth/oauth/exchange` | public | Same one-time code exchange as `/auth/oidc/exchange` |
| `POST`   | `/auth/oauth/bind/start` | user | body `{kind, redirect_after?}` → bind the identity to the current user |
| `POST`   | `/auth/oauth/unbind` | user | Unlink one SSO identity by `kind` (requires a local password when it is the last login method) |
| `GET`    | `/auth/oauth/providers/{kind}` | admin | Provider config; `kind` is `oidc`, `feishu`, `dingtalk`, or `wecom` |
| `PUT`    | `/auth/oauth/providers/{kind}` | admin | Upsert provider config; `client_secret` is write-only; WeCom uses `extra.agent_id` |
| `POST`   | `/auth/oauth/providers/{kind}/test` | admin | Test provider credentials |
| `GET`    | `/auth/ldap/status` | public | `{enabled, display_name}` for the login page hint |
| `GET`    | `/auth/ldap/config` | admin | Directory settings; `bind_password` is never returned (`has_bind_password` instead) |
| `PUT`    | `/auth/ldap/config` | admin | Upsert directory settings; `bind_password` is write-only (omit to keep the stored one) |
| `POST`   | `/auth/ldap/config/test` | admin | Service bind + probe of `user_base_dn` → `{ok, detail}` |
| `POST`   | `/auth/logout` | user | `204` |
| `GET`    | `/auth/me` | user | `{id, username, role, display_name, locale, ...}` |
| `PATCH`  | `/auth/me` | user | body `{display_name?, locale?, ...}` |
| `POST`   | `/auth/change-password` | user | body `{old_password, new_password}` → `204`. Accounts without a local password (directory/SSO-provisioned) get `PASSWORD_NOT_SET` |
| `GET`    | `/health` | public | `{status: "ok", version}` |

Logout revokes the current login session, including tokens already renewed in
another tab and communication realtime tickets. Revoked tokens are rejected by
authenticated HTTP endpoints and new WebSocket handshakes; other login sessions
remain valid. Active communication sockets recheck revocation every second.

## Users (admin)

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/users` | `users` | Legacy array of accounts, including disabled accounts |
| `GET`    | `/users/page` | `users` | `{items, total, page, page_size}`; `page` ≥ 1, `page_size` 1–100 (default 25), `search` ≤ 200 characters; items include owned `agent_count` without loading experts |
| `POST`   | `/users` | admin | body `{username, password, role, display_name?, email?, permissions?, workspace_root_dir?, token_quota?}` → `201` |
| `GET`    | `/users/{id}` | admin | full user row |
| `PATCH`  | `/users/{id}` | admin | body subset of `{role, display_name, email, enabled, locale}` |
| `POST`   | `/users/{id}/reset-password` | admin | body `{new_password}` → `204` |
| `POST`   | `/users/{id}/unlock-login` | admin | `204` (clears the lockout) |
| `DELETE` | `/users/{id}` | admin | `204` |

### Employee resource management

These endpoints require an actual administrator; a delegated `users` or
`departments` menu permission does not grant resource assignment rights.

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/admin/expert-assignments/directory` | Saved employee selections, source, expert names and department pools; no workspace scan. Batch memberships are for display only; `membership_status` is `current`, `changed`, `pending` or `unavailable`. |
| `GET` | `/admin/expert-assignments/catalog` | Separate expert/skill catalog with `source` revision; discard a catalog from a different source revision. |
| `GET` | `/admin/expert-assignments/users/{id}` | Reconcile one employee against current organization authority before opening an editor; unknown moves can revoke old selections. |
| `GET` | `/admin/expert-assignments` | Compatible combined overview; new clients should use the separate reads above. |
| `GET` | `/users/{id}/models` | Assigned `model_refs`, current `revision` and available chat model `items`; no credentials. |
| `PUT` | `/users/{id}/models` | Replace assignments with `{model_refs, expected_revision}`; an empty list revokes all. Stale saves return 409. |

The user editor may also include `model_access: {model_refs, expected_revision}`
in `PATCH /users/{id}`. This field requires an actual administrator.

All these reads use `Cache-Control: no-store`. Saves and execution independently
recheck current authority. Management and resource I/O have bounded queues;
capacity exhaustion returns 503 with `details.reason=read_capacity_exceeded` and
`details.retryable=true`. See [employee resource assignment](expert-assignments.md)
for write endpoints and revision checks.

## Agents

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/agents` | user | `[{id, agent_id, name, persona_mbti, state, unread_count, ...}]`; `scope=all` and its optional `owner_user_id` filter require `users` permission. List initialization status is a lifecycle projection, not a workspace probe |
| `POST`   | `/agents` | user | body `{name, persona_mbti?, default_model?, system_prompt?, description?, icon?, template_name?, config?}` → `201` |
| `GET`    | `/agents/{id}` | owner | full agent row |
| `PATCH`  | `/agents/{id}` | owner | body subset of create body |
| `DELETE` | `/agents/{id}` | owner | `204` |
| `POST`   | `/agents/{id}/start` | owner | `204` |
| `POST`   | `/agents/{id}/stop` | owner | `204` |
| `POST`   | `/agents/{id}/reload` | owner | `204` (rebuild harness runtime) |
| `POST`   | `/agents/{id}/read` | owner | `204` (mark unread badge cleared) |
| `GET`    | `/agents/{id}/status` | owner | `{state, last_error?, memory_maintenance?, ...}` |
| `POST`   | `/agents/from-expert/{expert_id}` | user | body `{name, ...}` → `201` (creates from bundled expert template) |
| `GET`    | `/agents/{id}/tool-settings` | owner | built-in + installed plugin tools with enable / disableable / available flags |
| `PUT`    | `/agents/{id}/tool-settings` | owner | body `{disabled_builtin: string[], plugins?}` — persists denylist + plugin flags (hot-sync, no reload) |
| `PATCH`  | `/agents/{id}/tool-settings/{tool_name}` | owner | body `{enabled, source, plugin_id?}` — toggle one tool (hot-sync) |

## Skills and skill packages

Skill copy operations create snapshots; they do not keep the source and
destination synchronized after the request completes.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/skill-packages?writable_only=true` | user (`skill_packages`) | List only packages the current user may modify; admins may modify all packages |
| `POST` | `/agents/{id}/skill-packages/{package_id}/copy` | owner (`skill_packages`) | body `{skill_slugs: string[], overwrite?: boolean}`; copy selected package skills into the workspace |
| `POST` | `/agents/{id}/skills/{slug}/push-to-package` | owner (`skill_packages`) | body `{package_id, overwrite?: boolean}`; copy a workspace skill into a package created by the user (or any package for admins) |

## Chat (WebSocket)

| Path | Auth | Notes |
|------|------|-------|
| `WS /agents/{id}/chat/ws?token=<jwt>` | owner | Primary dashboard turn endpoint. Send `{"type":"user_turn", ...}` frames (optional `hitl_policy` is a thread-scoped tool-approval bypass); server replies with harness stream chunks ending in `{"type":"done"}` or `{"type":"error","message":"..."}`. `{"type":"ping"}` → `{"type":"pong"}`. `{"type":"subscribe","thread_id"}` → `{"type":"turn_status","thread_id","active"}` plus a `steer.queue_snapshot` baseline (attach to an in-flight turn without cancelling on disconnect). `{"type":"cancel","thread_id"}` stops the active turn and discards its queued steers (explicit stop; disconnect alone does **not** cancel). See [Mid-turn steering](#mid-turn-steering) for steer frames. |
| `GET /agents/{id}/chat/welcome` | agent access | `{welcome_message, quick_prompts, task_examples}`; `task_examples` is `null` when the workspace field is absent |
| `POST /agents/{id}/chat/polish` | owner | body `{text, default_model?}` → `{text}` (one-shot prompt refinement) |
| `POST /agents/{id}/chat/hitl/resume` | owner | body `{thread_id, decisions: [...], pending_id?, hitl_policy?}` → SSE chunk stream; finishes with `{"type":"done"}`. Supply the card's `pending_id` when multiple team approvals are pending so another member's request is never resumed accidentally. When the resume stops on another approval, the done frame is `{"type":"done","hitl_paused":true}` and parked steers stay queued. Optional `hitl_policy` (`ask` / `allow_all` / `allow_tools`) is a thread-scoped bypass and does not change global tool-approval settings. |

### Legacy SSE

The previous `POST /agents/{id}/chat/stream` is gone. The dashboard
streams turns over the WebSocket above; HITL resume stays on SSE
because each request is a one-shot continuation.

### Mid-turn steering

While a turn is active, a `user_turn` frame may carry `delivery`
(`auto` \| `steer` \| `follow_up`), `client_pending_id` (client UUID for
correlation) and `expected_turn_id` (the `turn.started` id, for optimistic
concurrency). The server is authoritative: busy `auto`/`steer` steers the
active turn (queued server-side, bounded by depth and byte caps); idle `auto`
starts a normal new turn; `follow_up` while busy is rejected; new steers are
rejected while a HITL card is pending. Replies arrive on the thread:

| Frame | Meaning |
|-------|---------|
| `turn.started` / `turn.resumed` / `turn.ended` | logical turn lifecycle; `turn.ended` carries `hitl_paused: true` when steers stay parked across an approval pause |
| `steer.ack` | direct reply on the sending socket (`queued` / `rejected` / `removed`, with `reason`) |
| `steer.queued` | steer accepted into the active turn queue (`pending_id`, `queue_length`, `preview`) |
| `steer.drained` | queued steers injected as a user message; the same turn continues (`turn.resumed` follows) |
| `steer.discarded` | queue dropped (`turn_cancelled` / `turn_failed` / `continuation_exhausted` / `park_expired`) |
| `steer.removed` / `steer.rejected` | `steer_remove` confirmation / structured rejection |
| `steer.queue_snapshot` | server-authoritative queue baseline (on subscribe) |

Drain happens only at safe boundaries: when tools have run, the first model
output after the completed tool batch preempts the stream (that chunk and
anything after it, except `done` / `error`, are not forwarded) and the queued
steers continue the same logical turn; after a pure-text answer steers drain
at the natural turn end. Image and file blocks on the `user_turn` are queued
with the text and replayed on drain. Steers parked across an HITL pause drain
after the approval resumes, and only when that resume does not stop on a new
approval card. A park older than 30 minutes is discarded (`park_expired`).
`{"type":"steer_remove",...}` withdraws one queued item before it drains. The client puts the draft back only after `steer.ack` with `status: "removed"` and without `already_drained`; an already-drained item stays in the continuation.

### Company desktop steering

Company tasks use the existing authenticated native IPC, without opening a server
chat WebSocket. `window.__octopRuntime.steer({thread_id, text, expected_turn_id,
client_pending_id})` inserts text into the active local task; `removeSteer({thread_id,
turn_id, client_pending_id, pending_id})` withdraws an undrained input. Both methods
are optional on protocol version 1 so older shells keep their existing queue and
receive an update hint instead of starting or cancelling another task.

Local lifecycle notifications use `{event:"steer", thread_id, frame}` with the
same `turn.*`, `steer.*`, and canonical `user` frame semantics as the WebSocket
path. Canonical user frames include `client_pending_ids`, so settling an earlier
batch preserves newer pending inputs. The final native `done` event still waits
for company history and artifact persistence before releasing the composer.

The local path accepts text only; refer to files in the selected workspace rather
than inserting uploaded attachments. Tools finish before a continuation is
injected, and the same remote checkpoint and local operation receipts are reused.
Pure-text replies finish before draining. A stopped or failed task does not run
pending corrections. Company memory remains on the server.

## Threads & history

`POST /agents/{id}/threads/{thread_id}/cancel` requires the current user's thread ownership
and no request body. Returns `{thread_id, requested}`: `true` means the cancellation entry
was called for a hub-active ordinary Dashboard turn, **not** that it has stopped; `false`
means no such active turn was detected. Observe stream termination or re-subscribe for
`turn_status`. Pending steers for that active turn are discarded. This does not cancel separate queued tasks/HITL or roll back external side effects,
does not identify an earlier turn, and must not be automatically retried.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/agents/{id}/chat/sessions` | owner | `[{id, thread_id, title, archived, last_active, unread, ...}]` |
| `POST`   | `/agents/{id}/chat/sessions` | owner | body `{session_key?}` → `{thread_id, session_key}` |
| `PATCH`  | `/agents/{id}/chat/sessions/{thread_id}` | owner | body `{title?, pinned?}` → updated row |
| `DELETE` | `/agents/{id}/chat/sessions/{thread_id}` | owner | `204` (archives the active row) |
| `GET`    | `/agents/{id}/chat/sessions/{thread_id}/history` | owner | paginated message history; `turn_active` tells a reconnecting client whether to re-`subscribe` over the chat WebSocket |

**Tool result blocks.** `tool_result` blocks in history (and `tool_result`
frames on the chat WebSocket) carry the tool's return value in `output`. When a
plugin returns a large `octop_ui` payload, the backend offloads the envelope's
`data` field: `output` then contains the slim envelope with
`data_ref: "artifact"`, and the full payload is on the block's `artifact` key
(absent otherwise). An explicit `data` key always takes precedence over
`data_ref` when both appear. Clients that render plugin UIs must resolve
`data_ref` from `artifact`; clients that only read `output` keep working.

### Trajectory ledger

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/agents/{id}/threads/{thread_id}/trajectory` | owner | paginated event summaries; use `before_seq` for older rows |
| `GET` | `/agents/{id}/threads/{thread_id}/trajectory/events/{event_id}` | owner | full event payload |
| `GET` | `/agents/{id}/threads/{thread_id}/trajectory/metrics` | owner | aggregated turn, timing, and token metrics |
| `GET` | `/agents/{id}/threads/{thread_id}/trajectory/stream` | owner | live SSE events; resume with `after_seq` or `Last-Event-ID` |
| `GET` | `/agents/{id}/threads/{thread_id}/trajectory/export` | owner | full ledger download as JSONL (default) or JSON |

## Channels

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/agents/{aid}/channels` | owner | list of channel rows |
| `POST`   | `/agents/{aid}/channels` | owner | body `{kind, name, config}` → `201` |
| `GET`    | `/agents/{aid}/channels/{cid}` | owner | channel row |
| `PATCH`  | `/agents/{aid}/channels/{cid}` | owner | body subset → updated row |
| `DELETE` | `/agents/{aid}/channels/{cid}` | owner | `204` |
| `POST`   | `/agents/{aid}/channels/{cid}/test` | owner | `{ok, error?}` (instantiate → start → stop) |
| `POST`   | `/agents/{aid}/channels/probe` | owner | `{ok, reason?, detail?}` — preflight a candidate config |
| `POST`   | `/agents/{aid}/channels/{platform}/qrcode/generate` | owner | platform-specific bot creator (wecom, weixin, feishu, yuanbao) |
| `POST`   | `/agents/{aid}/channels/{platform}/qrcode/poll` | owner | poll bot creator state |
| `POST`   | `/agents/{aid}/channels/{platform}/bot-creator/start` | owner | start a bot-creator flow |
| `POST`   | `/agents/{aid}/channels/{platform}/bot-creator/poll` | owner | poll progress |
| `POST`   | `/agents/{aid}/channels/{platform}/bot-creator/stop` | owner | stop an in-flight bot creator |

## Cron

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/settings/timezone` | user | process-level `{timezone}` from `default_timezone` |
| `GET`    | `/settings/upload` | user | `{max_upload_mb, max_upload_bytes}` from `max_upload_mb` |
| `GET`    | `/settings/hitl` | user | `{enabled, tool_guard_require_approval, show_approval_ui}` — `show_approval_ui` is true when tool HITL is on or command guard is `require_approval` |
| `GET`    | `/settings/captcha` | `captcha` | `{active, available, providers, source, v3_min_score}`; secrets omitted |
| `PUT`    | `/settings/captcha` | `captcha` | merge `{active?, providers?}`; empty secret keeps ciphertext; `null` removes a pair |
| `GET`    | `/cron/settings` | user | compat alias of `/settings/timezone` |
| `GET`    | `/agents/{aid}/cron/examples` | agent access | `{task_examples}` from workspace `.octop/manifest.json` (`{zh,en}` string arrays, display-normalized to 3 or 6); `null` if the field is absent (dashboard keeps default cards). Prefer `GET /agents/{aid}/chat/welcome` which includes the same field. |
| `GET`    | `/agents/{aid}/cron` | owner only | list cron rows; non-owners (including admin) get `[]` |
| `POST`   | `/agents/{aid}/cron` | owner only | body `{name?, trigger, prompt, session_key?, fresh_thread?, enabled?, model?, task_type?}` → `201` |
| `GET`    | `/agents/{aid}/cron/{cid}` | owner only | cron row |
| `PATCH`  | `/agents/{aid}/cron/{cid}` | owner only | body subset → updated row |
| `DELETE` | `/agents/{aid}/cron/{cid}` | owner only | `204` |
| `POST`   | `/agents/{aid}/cron/{cid}/run-now` | owner only | `204` (fire immediately, off-schedule) |

`task_type` is `"text"` (push prompt directly to the session) or
`"agent"` (run the prompt through the LLM and push the reply).
Default: `"agent"`. `trigger` accepts cron expressions
(`"0 9 * * *"`) plus the `interval:N` / `date:ISO8601` aliases
documented in `infra/cron/trigger.py`. `prompt` must be non-empty and
≤ 2000 characters. `name` is an optional display label; when omitted,
the server derives one from `prompt`.

## Providers

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/providers` | `providers` | Credential-bearing provider records; employees without model management permission receive 403 |
| `GET`    | `/admin/providers` | `providers` | Same credential boundary as `/providers` |
| `POST`   | `/admin/providers` | `providers` | body `{name, kind, base_url?, api_key?, models?, ...}` → `201` |
| `PATCH`  | `/admin/providers/{id}` | `providers` | Explicit fields only; omitting `api_key` preserves it |
| `DELETE` | `/admin/providers/{id}` | `providers` | `204`; local runtime providers are protected |
| `POST`   | `/admin/providers/{id}/test` | `providers` | Test a configured model |

Provider responses include `stream_usage`. Set it with
`PATCH /admin/providers/{id}` to control `stream_options.include_usage` without
overwriting other provider options. New custom providers default to `false`;
built-in presets default to `true`. Enable it only when the deployed provider supports streaming usage; the
cumulative-usage compatibility handling also applies to enterprise local models.

## Models

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/providers/presets` | user | provider templates from `octop-harness` |
| `GET` | `/providers/resolved` | user | Available model metadata filtered by employee assignments; administrators see all. No provider credentials. |
| `GET` | `/providers/active-model` | user | `{provider_name, model}` within the employee's assignments; empty strings when none are available. |
| `PUT` | `/providers/active-model` | `providers` | body `{provider_name, model}` |

Employees with no assignments cannot call chat models. Explicit choices and
automatic or vision fallback stay within their assigned models. Grants are
rechecked before each model call, including company desktop model proxy calls.

### Media generation models

These instance-wide endpoints require the `providers` permission (administrators bypass
permission checks). The Ark API key is write-only and encrypted at rest. Saving settings
reloads running agents so the image and video tools receive the new configuration.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/admin/media-generation` | providers | Return Volcengine Ark image/video settings; never returns the API key |
| `PUT` | `/admin/media-generation` | providers | Save enabled tools and Seedream/Seedance model IDs; an included API key is verified before saving |
| `POST` | `/admin/media-generation/test` | providers | Test credentials or a selected image/video model; model tests submit real, potentially billable requests |

## Voice

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`  | `/voice/presets` | user | voice provider presets |
| `GET`  | `/voice/providers` | `voice` | Credential-bearing voice provider records; employees without voice management permission receive 403 |
| `GET`  | `/voice/active` | user | active TTS / STT configuration |
| `PUT`  | `/voice/active` | `voice` | update active voice configuration |
| `POST` | `/voice/stt` | user | body `{audio, format?, language?}` → `{text, segments?}` |
| `POST` | `/voice/tts` | user | body `{text, voice?, format?}` → audio bytes |
| `GET`/`POST`/`PATCH`/`DELETE` | `/admin/voice/providers` | `voice` | Voice provider management; PATCH/DELETE use `/{id}` |

## MBTI & personas

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/mbti/codes` | user | `[{code: "INTJ", ...}, ...]` (16 codes + `_default`) |
| `GET`    | `/mbti/codes/{code}` | user | full profile (dimensions, behaviour, UI metadata) |
| `GET`    | `/mbti/preview/{code}` | user | rendered persona template (legacy `/api/personas/{code}`) |
| `PUT`    | `/agents/{aid}/mbti` | owner | body `{code}` → apply persona and reload |
| `GET`    | `/personas` | user | `[{code}, ...]` (compat shim) |
| `GET`    | `/personas/{code}` | user | rendered template (compat shim) |

Persona content lives in `src/octop/infra/agents/persona/mbti_profiles.py` —
see [Personas](./personas.md).

## Experts

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/experts` | user | bundled expert catalog (includes `task_examples` `{zh,en}` when present) |
| `GET`    | `/experts/{expert_id}` | user | full expert template (SOUL.md, skills, files, `task_examples`) |
| `POST`   | `/agents/from-expert/{expert_id}` | user | body `{name, locale?, quick_prompts?, ...}` → `201`; optional `quick_prompts` overwrites workspace cards after seed |

Bundled experts live in `src/octop/infra/agents/experts/library/`
(en/zh divisions); the catalog is locale-aware via
`Accept-Language` / user preference.

## Workspace, skills, subagents, memory, files

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/agents/{aid}/workspace/tree` | owner | list dir (default `.`) |
| `GET`    | `/agents/{aid}/workspace/file` | owner | read a content file |
| `PUT`    | `/agents/{aid}/workspace/file` | owner | write a content file (via `BackendWorkspace`) |
| `DELETE` | `/agents/{aid}/workspace/file` | owner | delete a content file |
| `POST`   | `/agents/{aid}/workspace/rename` | owner | rename / move |
| `POST`   | `/agents/{aid}/workspace/upload` | owner | multipart upload → backend (not `max_upload_mb`) |
| `GET`    | `/agents/{aid}/workspace/download` | owner | download a file |
| `GET`    | `/agents/{aid}/workspace/glob` | owner | glob backend paths |
| `GET`    | `/agents/{aid}/workspace/grep` | owner | grep backend files |
| `GET`    | `/agents/{aid}/workspace/...` | owner | see `api/routers/workspace.py` |
| `GET`    | `/agents/{aid}/skills` | owner | list installed skills |
| `PUT`    | `/agents/{aid}/skills/{slug}` | owner | enable / disable a skill |
| `GET`    | `/agents/{aid}/skills/hub/search` | user | Skill Hub search |
| `GET`    | `/agents/{aid}/skills/hub/rankings` | user | Skill Hub rankings |
| `POST`   | `/agents/{aid}/skills/hub/install` | owner | body `{slug, version?}` → `201` |
| `GET`    | `/subagent-catalog/divisions` | user | bundled subagent divisions |
| `GET`    | `/subagent-catalog` | user | bundled subagent catalog |
| `GET`    | `/subagent-catalog/{slug}` | user | full subagent definition |
| `GET`    | `/agents/{aid}/subagents` | owner | installed subagents for an agent |
| `POST`   | `/agents/{aid}/subagents` | owner | install a bundled subagent |
| `GET`    | `/agents/{aid}/heartbeat-config` | owner | read heartbeat YAML |
| `PUT`    | `/agents/{aid}/heartbeat-config` | owner | write heartbeat YAML |
| `GET`    | `/agents/{aid}/memory/daily` | owner | list daily memory files |
| `GET`    | `/agents/{aid}/memory/daily/{filename}` | owner | read one daily memory |
| `DELETE` | `/agents/{aid}/memory/daily/{filename}` | owner | delete one daily memory |
| `GET`/`PUT` | `/agents/{aid}/memory/extract-config` | owner/admin | Read or patch extraction triggers, `extract_light_timeout_s`, `extract_heavy_timeout_s`, and `extract_max_tokens`. Zero resets budgets/timeouts to runtime defaults. `extract_extra_body` supports only `{"chat_template_kwargs":{"enable_thinking":false}}` (or `true`); `{}` resets it. Unsupported extras return 400 rather than silently having no effect. |
| `GET`/`POST` | `/memory/...` | user | memory API (dashboard memory tab) |

## ACP (Agent Client Protocol)

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/acp` | user | current user's global runner list |
| `PUT`    | `/acp` | user | replace global runners |
| `GET`    | `/acp/{runner_name}` | user | one runner |
| `PUT`    | `/acp/{runner_name}` | user | upsert one runner |
| `DELETE` | `/acp/{runner_name}` | user | delete a custom runner (built-ins are protected) |
| `GET`    | `/agents/{aid}/acp` | owner | global runners + this agent's `tool_enabled` flag |
| `PUT`    | `/agents/{aid}/acp` | owner | update `tool_enabled` and optionally the global list |
| `PUT`    | `/agents/{aid}/acp/tool` | owner | toggle the `acp_runner` tool only |

See [ACP integration](./acp.md) for the runner object schema and the
Zed setup example.

## Storage backends

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`/`POST`/`PATCH`/`DELETE` | `/storage-backends` | user | per-user remote backend connections |
| `GET`/`POST`/`PATCH`/`DELETE` | `/admin/storage-backends` | admin | admin-managed backends |

## Host filesystem (dashboard)

Browse the host OS directory tree when configuring a local backend
`root_dir` (`local_shell` / `filesystem`). Authenticated users only;
sensitive mounts (`/proc`, `/sys`, `/dev`, `/etc`, `/root` on POSIX)
are rejected, except the process home and its subdirectories (so a
server running as root may use `/root` as the default `root_dir`).
Listing is single-level and capped; write probe runs only
for non-`/` paths.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/filesystem/dirs?path=<abs>` | user | `{path, entries: [{path, name}]}` — one directory level |
| `POST`   | `/filesystem/probe` | user | body `{path}` → `{ok, path?}` or `{ok: false, code, detail?}` (`not_directory`, `permission_denied`, `write_failed`, `not_allowed`) |
| `POST`   | `/filesystem/mkdir` | user | body `{path, base_name?}` → `{path, name}` — create child dir (`base_name` defaults to `New Folder`; collisions become `Name (2)`, …) |
| `POST`   | `/filesystem/rename` | user | body `{path, new_name}` → `{path, name}` — rename basename only |

## Connectors & OAuth

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/connectors/catalog` | user | connector catalog (Notion, Figma, …) |
| `GET`    | `/connectors/test-credentials` | user | preflight credentials |
| `GET`    | `/connector-instances` | user | list instances |
| `POST`   | `/connector-instances` | user | create instance |
| `GET`/`PATCH`/`DELETE` | `/connector-instances/{id}` | user | CRUD on an instance |
| `POST`   | `/connector-instances/{id}/test` | user | test a configured instance |
| `POST`   | `/connector-instances/{id}/refresh` | user | refresh OAuth tokens |
| `GET`    | `/connectors/auth/{kind}/info` | user | auth flow info |
| `GET`    | `/connectors/auth/{kind}/authorize-url` | user | build the authorize URL |
| `POST`   | `/connectors/auth/{kind}/exchange-code` | user | exchange auth code |
| `POST`   | `/connectors/oauth/start` | user | start OAuth (catalog or custom MCP via `target`) |
| `POST`   | `/connectors/oauth/{kind}/start` | user | legacy catalog OAuth start |
| `PUT`    | `/connectors/custom-mcp` | user | save custom MCP server map |
| `PATCH`  | `/connectors/custom-mcp/servers/{name}` | user | patch `enabled` / `default_open` on one server |
| `POST`   | `/connectors/custom-mcp/test` | user | probe a custom MCP server (inline spec or saved name) |
| `GET`    | `/connectors/oauth/callback` | public | OAuth redirect target |
| `GET`    | `/connectors/oauth/pending/{state_id}` | user | poll the OAuth result |

Custom MCP OAuth (streamable HTTP, public HTTPS URL only): Octop discovers the authorization
server from the MCP URL (401 / RFC 9728 protected-resource metadata), requires dynamic client
registration (DCR), stores encrypted tokens in the custom MCP spec, and injects `Authorization:
Bearer` when loading tools. Loopback and LAN MCP URLs may use HTTP and do not use remote OAuth
discovery.

## Internal MCP (octop-harness agents)

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `POST`/`GET`/… | `/internal/mcp/*` | public (mTLS / network-isolated) | MCP gateway used by octop-harness agents (not the dashboard) |

## Observability & security

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`/`PUT` | `/admin/observability` | admin | Langfuse configuration (host, project, env) |
| `GET`    | `/admin/security` | admin | global security policy |
| `PUT`    | `/admin/security` | admin | update global policy |
| `GET`    | `/admin/security/tool-guard/rules` | admin | active command guard rules |
| `GET`    | `/admin/security/tool-guard/rules/raw` | admin | editable YAML |
| `PUT`    | `/admin/security/tool-guard/rules/raw` | admin | save YAML |
| `POST`   | `/admin/security/tool-guard/rules/reset` | admin | reset to shipped defaults |
| `GET`    | `/admin/security/defaults` | admin | defaults + rule catalogs |

## TLS (Let's Encrypt)

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`  | `/admin/tls/status` | admin | current cert + issuance task status |
| `POST` | `/admin/tls/preflight` | admin | preflight (port 80, DNS) |
| `POST` | `/admin/tls/issue` | admin | start HTTP-01 issuance |

`GET /.well-known/acme-challenge/{token}` is the HTTP-01 challenge
endpoint (public, mounted directly in `api/app.py`).

## Browser, terminal, uploads

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `WS`/`POST`/`GET`/… | `/agents/{aid}/terminal` | owner | AI-assisted remote PTY |
| `GET` | `/agents/{aid}/terminal/context` | owner | recent terminal context for the AI helper |
| `WS`/`POST`/`GET`/… | `/browser/...` | user | octop-browser sessions, live stream, record/replay |
| `POST` | `/browser/shutdown` | user | stop the current user's Octop-managed Chrome |
| `POST` | `/agents/{aid}/upload` | user | multipart upload → `{workspace}/inbound/` |
| `POST` | `/agents/{aid}/files/access-urls` | user | refresh inbound media URLs (signed) |
| `GET`  | `/agents/{aid}/files/{path}` | owner | read an inbound file |

## Updates, ollama, i18n, plugins, slash, preferences

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/update/status` | user | Version status; enterprise distribution reports manual updates |
| `POST` | `/update/check` | `update` | Returns manual-update status; upstream online checks are disabled |
| `PATCH` | `/update/settings` | `update` | Retained stable-channel preference; does not enable the disabled upstream updater |
| `POST` | `/update/upgrade` | `update` | Disabled in this distribution; returns a forbidden error |
| `GET` | `/update/progress` | `update` | Legacy upgrade-task status by `task_id`; not an enterprise software delivery channel |
| `POST` | `/update/restart` | `update` | Restart a supported installed service / local server process; does not install software or replace the employee client |
| `GET` | `/client-config/status` | user | Current user's effective permissions, configuration revision and notification; `Cache-Control: no-store` |
| `POST` | `/client-config/publish` | admin | Publish a configuration notice; does not change settings or install software |
| `GET`/`POST`/`DELETE` | `/ollama-models/...` | `ollama_models` | Ollama model discovery + downloads |
| `GET`/`PUT` | `/ollama-models/service` | `ollama_models` | Local daemon toggle; omit `enabled` to set `models_dir` only |
| `GET` | `/i18n/tools` | user | server-owned tool display names (locale-aware) |
| `GET` | `/i18n/locales` | public | available locales + fallback chain |
| `GET` | `/i18n/locales/{locale}/{namespace}` | public | one namespace bundle (errors, tools, channel, slash) |
| `GET`/`POST` | `/preferences` | user | UI preferences (per-user key/value) |
| `GET` | `/slash/commands` | user | slash command catalog for the composer menu |
| `GET`/`POST` | `/plugins` | user | installed plugin list / install flow |
| `POST` | `/plugins/reload` | admin (`plugins`) | reload plugins from disk into process |
| `PATCH` | `/plugins/{id}` | admin (`plugins`) | enable/disable plugin (`{ "enabled": bool }`) |
| `GET` | `/plugins/{id}/ui/{path}` | user | serve prebuilt plugin UI assets (`ui/dist/…`) |
| `DELETE` | `/plugins/{id}` | admin (`plugins`) | uninstall plugin |

## Usage & admin

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/usage/summary` | user | token usage summary for the current user |
| `GET`    | `/admin/usage` | admin | global token usage summary |
| `GET`    | `/admin/overview` | admin | `{user_count, agent_count, ...}` |
| `GET`    | `/admin/audit-log` | admin | recent audit rows |
| `GET`    | `/admin/metrics` | admin | `{messages_total, stream_errors_total, cron_runs_total, cron_errors_total, agent_active, ...}` |

## Envs

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`/`PUT` | `/agents/{aid}/envs` | owner | env-var preset for an agent's tool calls |
| `GET`/`POST`/`DELETE` | `/envs/presets` | user | reusable presets |

## Error envelope

Every non-2xx JSON response uses:

```json
{ "error": { "code": "AGENT_NOT_FOUND", "message": "...", "details": { } } }
```

`code` matches the `ErrorCode` enum in `octop.infra.errors`; the
server-localized `message` is rendered by `OctopError.to_envelope`
with the locale from `Accept-Language` (falling back to `en`). The
dashboard mirrors every code under `apiErrors.*` in
`dashboard/src/locales/{en,zh}.json`.

| Code | HTTP | Meaning |
|------|------|---------|
| `AUTH_FAILED` | 401 | Bad credentials |
| `TOKEN_EXPIRED` | 401 | JWT past its TTL |
| `LOGIN_LOCKED` | 423 | Too many failed attempts — wait `login_lockout_seconds` or call `/users/{id}/unlock-login` |
| `SETUP_REQUIRED` | 409 | Initial admin not yet created (or wizard not finished) |
| `FORBIDDEN` | 403 | Authenticated but not allowed |
| `NOT_FOUND` | 404 | No such row / route |
| `USER_DISABLED` | 403 | Account flag flipped off |
| `USERNAME_TAKEN` | 409 | Conflict on `users.username` |
| `AGENT_NOT_FOUND` | 404 | Agent row missing or owned by another user |
| `AGENT_FAILED` | 500 | Runtime errored during a call |
| `AGENT_BUSY` | 409 | Operation refused while another is in flight |
| `PROVIDER_NAME_TAKEN` | 409 | Conflict on `providers.name` |
| `PROVIDER_NOT_VISIBLE` | 400 | Agent config references a provider the user can't see |
| `PROVIDER_REFERENCED` | 409 | Delete blocked because agents still reference the row |
| `PROVIDER_TEST_FAILED` | 400 | `/providers/{id}/test` failed |
| `CHANNEL_KIND_UNSUPPORTED` | 400 | `kind` not in registered builders |
| `CHANNEL_INVALID_CREDENTIALS` | 400 | Channel config rejected by the platform |
| `CHANNEL_PROBE_INCOMPLETE` | 400 | Probe couldn't reach the platform |
| `CRON_TRIGGER_INVALID` | 400 | Trigger string did not parse |
| `CRON_PROMPT_INVALID` | 400 | Empty or too-long prompt |
| `SLASH_UNKNOWN` | 400 | `/<cmd>` is not a registered handler |
| `SLASH_BAD_ARGS` | 400 | Slash handler rejected its arguments |
| `ATTACHMENT_UNSUPPORTED_TYPE` | 400 | Chat / inbound attachment rejected (media type not allowed) |
| `ATTACHMENT_TOO_LARGE` | 413 | Chat / inbound attachment exceeds the configured size limit (`max_mb` in details) |
| `WORKSPACE_PATH_INVALID` | 400 | Path outside the agent's workspace |
| `STORAGE_BACKEND_UNREACHABLE` | 502 | Remote backend connect / list failed |
| `CONNECTOR_OAUTH_FAILED` | 400 | OAuth flow could not complete |
| `INTERNAL_ERROR` | 500 | Unhandled exception (logged with traceback) |

### Company directory and messaging (dashboard)

The dashboard routes `/messages`, `/messages/:conversationId`, `/contacts`, `/contacts/:personId` and
`/admin/users?tab=departments` use `/api/im` with the normal Octop JWT. Messaging,
directory browsing and organization administration require `messaging`, `contacts`
and `departments` respectively. AI conversations continue to use their existing
agent routes. No active-agent identifier is sent for communication requests.

The single Conversation sidebar entry opens an Agent/company-directory tab switch.
The directory shows enabled departments as a hierarchy with descendant population
counts, includes empty departments and groups unassigned people. Selecting a person
shows their public profile; starting a conversation opens the shared chat area.
Recent colleague conversations are available within the company tab. Contacts with
messaging permission can chat across departments; private history and images still
require participation, and department-group epochs remain scoped to membership.
Administrators set the company label via `PUT /api/im/organization/directory-settings`.

Organization administrators can read `GET /api/im/organization/users` to assign
mirrored accounts and `GET /api/im/organization/groups/{id}` to manage department
group settings and roles. These endpoints do not grant access to messages or
images; the normal group member endpoints still enforce membership. Ordinary
contacts are read only from `/api/im/contacts` and `/api/im/departments`.

See `services/communication/api/contract.md` for envelopes, decimal-string
sequences, snapshot chunks, one-use realtime tickets and private image uploads.
The frontend keeps communication state and image object URLs in memory for the
current login session and clears them on logout or permission rejection.
