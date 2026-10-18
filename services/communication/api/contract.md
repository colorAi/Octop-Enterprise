# Octop communication API (first backend version)

The browser uses the existing Octop Bearer JWT and `/api/im`. The internal Go
origin and `/internal/identities` are unavailable to browsers. Runtime permissions
come from current Octop account revisions; headers such as `X-User-Id` are ignored.

All ordinary endpoints preserve `{request_id,data}` and
`{request_id,error:{code,message,retryable}}`. HTTP status is preserved (including
201 on newly created messages/uploads/snapshots). Media downloads return binary
bytes with `Cache-Control: no-store`. `/api/im/realtime-ticket` returns its own
`{ticket,expires_at}` object. Generated OpenAPI includes request schemas and summaries.

| Method/path after `/api/im` | Permission | Request or query |
|---|---|---|
| `GET /contacts` | contacts | optional `q`; active company colleagues, including unassigned people; public name/avatar/job title/department/message capability only |
| `GET /departments` | contacts | enabled company departments, including empty departments; `parent_id` and direct `member_count` |
| `GET /directory-settings` | contacts | shared directory `name`; empty uses the localized Contacts label |
| `GET/PUT /organization/directory-settings` | departments | read or set `{name}`; at most 80 characters, audited and synchronized |
| `GET /conversations` | messaging | `limit`, opaque `cursor`, `include_hidden=1` |
| `POST /conversations/direct` | messaging | `{peer_user_id}` from contacts |
| `GET /conversations/{id}/messages` | messaging | `limit`, decimal-string `before_seq` |
| `POST /conversations/{id}/messages` | messaging | `{client_message_id,text?,asset_ids?,reply_to_message_id?,mention_user_ids?,mention_all?}` |
| `POST /conversations/{id}/read` | messaging | `{last_read_seq:"123"}` |
| `PATCH /conversations/{id}/state` | messaging | `{pinned?,muted?}` |
| `POST /conversations/{id}/hide` | messaging | no body |
| `POST /conversations/{id}/clear` | messaging | `{through_seq?}`; empty object clears through current sequence |
| `POST /messages/{id}/recall` | messaging | own message, within configured recall window |
| `POST /messages/{id}/forward` | messaging | `{client_message_id,to_conversation_ids:[...]}`; source and destinations rechecked in transactions |
| `GET /groups` | messaging | current joined active groups, independent of hidden state; optional `kind=company/department/project` |
| `POST /groups` | groups_create + messaging | `{request_id,name,user_ids:[]}`; creates a project group, includes creator |
| `PATCH /groups/{id}` | messaging + group owner/admin | `{request_id,name?,announcement?,mute_all?,members_can_invite?}`; invite policy only for projects |
| `POST/DELETE /groups/{id}/members` | messaging + group authority | `{request_id,user_ids:[]}`; explicit grants only; automatic members cannot be removed |
| `PATCH /groups/{id}/members/{user_id}` | messaging + group authority | `{request_id,role?,mute_until?}`; only owner grants roles or manages admins |
| `PUT /groups/{id}/owner` | messaging + owner | `{request_id,user_id}`; current active member |
| `POST /groups/{id}/leave` | messaging | `{request_id}`; project member; owner must transfer first |
| `POST /groups/{id}/archive` | messaging + owner | `{request_id}`; projects only; history retained, sending disabled |
| `GET /organization/groups` | departments | all managed groups, with `is_member`; never implies history access |
| `POST /organization/company-group` | departments | `{request_id?,name?}`; create or return the single company group |
| `POST/DELETE /organization/groups/{id}/members` | departments | `{request_id,user_ids:[]}`; explicit join/invite/remove without bypassing automatic membership |
| `POST /organization/groups/{id}/archive` | departments | `{request_id}`; projects only |
| `GET /groups/{id}` | messaging | current member settings; `group_kind`, `my_role`, `is_member`, `status` |
| `GET /groups/{id}/members` | messaging | current members can read names/avatars/nicknames; `role`, `membership_source=automatic/invited`, `granted_by`, `mute_until`, `joined_at` are returned only to the group owner/admin or a caller with `departments` permission |
| `PATCH /groups/{id}/my-membership` | messaging | `{my_nickname}`; current membership required |
| `GET /sync` | messaging | `cursor`, `limit`; cursor bound to account |
| `POST /sync/snapshot` | messaging | no body; returns `snapshot_id,until_seq,cursor,chunk_count` |
| `GET /sync/snapshots/{id}` | messaging | `chunk=0` ... `chunk_count-1` |
| `POST /media/uploads` | messaging | `{purpose:"chat_image"|"chat_file",mime,size_bytes,sha256,filename?}` |
| `GET /media/uploads/{id}` | messaging | uploader-only processing state: created/writing/uploaded/processing/ready/failed/aborted |
| `PUT /media/uploads/{id}/content` | messaging | raw attachment bytes; images up to 10 MiB, files up to 50 MiB |
| `POST /media/uploads/{id}/complete` | messaging | no body; enqueues attachment processing |
| `DELETE /media/uploads/{id}` | messaging | abort upload |
| `GET /media/{id}` | messaging | `kind=original` or `thumbnail`; membership, interval, recall and clear checks apply |
| `POST /realtime-ticket` | messaging | Bearer JWT; one-use ticket, at most 30 seconds |
| `WS /realtime?ticket=...` | messaging | exact same-origin browser connection; no JWT in URL |
| `GET /organization/users` | departments | mirrored people for assignment; no login or secret fields |
| `GET /organization/groups/{id}` | departments | administration settings and roles; no history/media |
| `GET/POST /organization/departments` | departments | create: `{name,parent_id?,sort_order?,enabled?}` |
| `PATCH /organization/departments/{id}` | departments | partial department fields; cycles rejected |
| `PUT /organization/users/{id}/department` | departments | `{department_id}` or null; mirrored communication user id |
| `POST /organization/departments/{id}/group` | departments | create or return unique department group; no body |
| `PATCH /organization/groups/{id}` | departments | `{name?,announcement?,mute_all?}` |
| `PATCH /organization/groups/{id}/members/{user_id}` | departments | `{role?:"admin"/"member",mute_until?:RFC3339/null}` |
| `PUT /organization/groups/{id}/owner` | departments | `{user_id}`; current active member only |

Private conversations require two active messaging-enabled company users; they
may belong to different departments or have no assigned department. Transfers
retain private conversation access and sending. Department-group transfers close
old epochs before appending system events, and re-entry opens a new interval.
Owner departure/disable/deletion selects another active member; empty groups have
no eligible senders. Organization membership changes, group intervals, audit
records and synchronization events commit together. Organization group management
never bypasses membership checks on message or media reads.

Message history includes `client_message_id`, reply ids and mention fields. System messages expose `system_kind` (including `department.members.changed`) for localized rendering.

Use a stable `client_message_id` for retries. Sequence/watermark/version fields
in the communication protocol are decimal strings; do not convert them to JS
Number. `message.new` contains ids and sequence, so fetch authorized history to
obtain content. `contacts.changed` signals replacement of the authorized contact
list. `group.members.changed`, `group.update`, `conversation.state.changed`,
`conversation.hidden`, `conversation.cleared` and `message.recall` signal changes
whose current data is read via the corresponding endpoint. Do not use old Miaox
friend or `miaox_code` examples.

Fetch and apply every snapshot chunk atomically, retain its returned cursor,
connect WS, then actively pull sync once. Pull all incremental pages in sequence
on `sync.available`, reconnect and page visibility changes. Handle
`sync_cursor_expired` with a fresh snapshot. Account events and conversation
history have distinct pagination. `sync_wakeup` is persisted in the transaction
with events; reconnect clients must still pull incrementals. Realtime carries
`sync.available`, `ping`, `pong`, `resync` only. Logout revokes the stable JWT
session id for authenticated HTTP requests and new WebSocket connections, including
communication; renewals retain that id and revoked sessions cannot renew. Existing JWTs without a
session id use their fingerprint. Active proxy sockets recheck the source account
and session every second. Ticket query values are removed from the ASGI logging
scope before acceptance or rejection.

Retention deletes synchronization events and advances the corresponding watermarks
atomically in bounded batches. Sync reads use a single database snapshot for the
watermark and event page. A cursor acknowledging the greatest purged event remains
valid; one that still needs a purged event receives `sync_cursor_expired`, including
when no events remain. Gaps left by older interrupted purges also require a fresh
snapshot. This continuity rule applies to the per-user event stream, not to
conversation history filtered by membership epochs and clear markers.

Images and files follow init -> PUT -> complete -> uploader status poll -> send ready
asset ids. Files use `chat_file`, preserve their original bytes and return filename,
MIME and size in message attachments. A message containing any file has type `file`;
otherwise image attachments retain type `image`. Files are downloaded as attachments
with `application/octet-stream`, a sanitized filename and `nosniff`. Upload responses already contain the public `/api/im/.../content` URL.
The uploader can read an unattached asset; once attached, the uploader is also
subject to message visibility/recall/clear. Attachments forwarded into another legally
visible conversation remain accessible through that valid message reference.
`avatar_icon` and `avatar_url` mirror the login account in contacts, sync profiles,
and group rosters. `job_title_id` and `job_title` come from company assignments on
those same records. Portrait files stay on Octop and use authenticated
`/api/users/{id}/avatar?v=...` requests; an empty URL uses the account preset.
Account and administrator portrait changes publish durable identity revisions;
startup reconciliation backfills existing images. Reading a colleague's portrait
requires `contacts`, `messaging`, or `users`; changing it retains the existing
account/user-management permissions.

Snapshots for messaging-only users include profiles of existing private peers and
group members whose history intervals overlap their own. They do not include
unrelated directory entries. Profile and position changes notify messaging clients
to refresh these scoped records. Accounts, passwords, SSO, AI and native push do
not have second product endpoints here.

## Group upgrade (schema 13)

`groups_create` defaults off for ordinary employees and is granted through the existing
role/permission editor. Job titles never confer this capability. Administrators receive
it from the canonical permission catalog, including same-version identity backfill on upgrade.
Company groups include all active messaging-enabled accounts; department groups include
direct department members plus explicit collaborator grants. Project groups use explicit
grants and survive department transfers. Disable, deletion or loss of messaging permission
revokes explicit grants and closes epochs; reactivation restores only automatic membership.
Archived projects retain authorized history but cannot receive messages or new members.

All new mutation endpoints require a stable `request_id` (1–128 characters), reused unchanged
for retries. Receipt digests bind actor, method, path and payload. A conflicting reuse returns
409. The unique company relation additionally makes creation idempotent across administrators.
Existing organization settings/role/owner routes accept omitted request IDs for compatibility.
Member changes, system messages, organization audit, permission version and sync/outbox events
commit in the existing organization transaction. New members start at the join system event;
leaving members' intervals end before the removal event. No prior messages are copied to the
company group. The additive migration preserves department mappings, messages, epochs and
read/mute/hidden/clear state. Run the service's explicit migration command before starting the
updated binary; application version numbers are independent from this schema migration.

This upgrade implements human groups only. Company bot identities, bindings and execution
remain a subsequent implementation phase.
