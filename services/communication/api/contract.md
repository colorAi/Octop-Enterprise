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
| `GET /presence` | contacts | `{online_user_ids:[...]}`; active non-admin colleagues with at least one live client connection |
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
| `POST /realtime-ticket` | contacts or messaging | Bearer JWT; one-use ticket, at most 30 seconds |
| `WS /realtime?ticket=...` | contacts or messaging | exact same-origin browser connection; no JWT in URL |
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
`sync.available`, `presence.changed`, `ping`, `pong`, `resync` only.
`presence.changed` carries no identities; contacts-enabled clients refresh
`GET /presence` on this hint, reconnect, visibility changes and every 30 seconds.
Presence is transient connection state, not a durable account event or an activity
status. A user stays online until their final client disconnects; unresponsive
connections are checked by a 45-second heartbeat with a 10-second response timeout.
If a client cannot refresh presence it shows an unknown state.
Logout revokes the stable JWT
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

Schema 13 supplies the human-group foundation. Group expert bindings and execution are
implemented by the additive protocol below.

## Group expert protocol v1 (schema 19)

当前实现要求能力协议 2、schema 21；保留 v1 路由和签名用途，旧 Worker 不可领取新群任务。

该协议用于公司群、部门群、项目群的单轮专家调用，实施规则见
[`company-group-experts-plan.md`](../../../docs/design/company-group-experts-plan.md)，
执行归属、模型快照和 Python 接口见
[`execution-contract.md`](../../../docs/design/execution-contract.md)。
本节路由已接入。`group_settings.experts_enabled` 默认 `false`；Python 验证固定
Worker 拓扑后签发至多 15 秒的运行能力证明，Go 只在证明有效时受理启用和新调用。

### 消息和历史兼容

- 发送请求增加可选 `mention_expert_id`，值为本群专家绑定 ID，与人员
  `mention_user_ids` 分开；没有专家提及时不向规范发送摘要增加空字段，
  既有 `client_message_id` 重试继续命中原回执。发送回执包含 `expert_call`，
  重试返回原调用身份和登记状态。回执及历史列表的 `expert_call.expert_name` 仅显示本群绑定的名称。
- `messages.mention_expert_id` 保留原始绑定 ID，允许无效或过期 ID 随合法人员
  消息保存，另记拒绝原因。`expert_binding_id` 仅供内部结果回帖使用，外部用户
  不能指定发送者类型或伪装为专家。
- `sender_type` 为数据库生成列：系统消息为 `system`，含合法
  `expert_binding_id` 的文本答复为 `group_expert`，其余为 `member`。
  新旧系统消息写入入口不必补传新字段；历史消息、成员可见区间和清空标记不变。
- 专家答复的 `sender_id=expert_binding_id`，`client_message_id=call_id`。
  已移除绑定及其展示资料留存；重新邀请创建新绑定，旧消息不能转指新绑定。

### 调用和补偿记录

`group_expert_calls.call_state` 取值为 `pending_admission`、`rejected`、`queued`、
`dispatched`、`awaiting_delivery`、`completed`、`failed`、`cancel_requested`、
`cancelled`、`unknown`。Python 的执行 `completed` 不等于 Go 已回帖。
Go 的所有终态不可被迟到登记、控制或结果复活。

群锁保护最多 4 个预留位置及邀请的 3 位专家上限；数据库额外以唯一索引保护
每群一个 `dispatched/awaiting_delivery/cancel_requested` 位置。同源同群只有
一个有效绑定。绑定记录 `scope_revision`，登记身份另传 `binding_scope_revision`。排队按原消息序号推进，尚未确认的队首不能被后续调用越过。
未派发取消可释放群位置，已派发取消必须等待明确停止；`unknown` 释放群位置，
Python 仍保留未核清执行、模型与工具审计及会话占用。群专家 unknown 对全局运行/模型准入占位最多 30 秒，未确认消耗暂按预留上限计入调用人额度，其新群调用最多暂停 30 秒，已经受理的调用继续排队等待；该窗口不构成停止证明。

调用依赖的授权域同时保存在 `authorization_versions` 和可索引的关系表中。
内部事务复用既有组织互斥锁，然后按授权域键、群 ID、调用、输入消息的顺序
加锁，最后按用户 ID 排序写入同步事件。授权撤销与回帖依提交顺序裁定先后，
事务内不等待网络。只读状态、上下文和回执不申请群会话排他锁，最终回帖仍按上述锁顺序重新核验。

`group_expert_outbox` 保存稳定操作 ID、类型、目标、请求摘要、不可变载荷和
持久回执。它与业务变更同事务写入，再用 `store.EnqueueJob` 同事务安排短投递。
主 Worker 拉取待办并确认，短任务负责本地核对；后台任务的成功或重试耗尽不等于
业务确认，周期扫描不排除终态调用的待办。
正文不写入登记、派发或取消任务载荷，按 `call_id` 即时读取受限上下文。

### Go 内部接口

仅接受认证的 Python 内部服务请求，不能纳入外部代理通配路径。管理操作携带
真实操作者身份和 `request_id`，使用 `group.expert` 操作回执范围；普通群管理
与企业管理员代办分别校验，不从外部请求接受管理员身份声明。

| 路由 | 契约 |
| --- | --- |
| `GET /internal/group-experts/v1/capabilities` | 当前能力协议 2 和 schema 21。 |
| `POST /internal/group-experts/v1/runtime` | Python 的全部 Worker 校验结论 `protocol=0|2`，按签名签发时间设置证明到期。 |
| `POST /internal/group-experts/v1/groups/{id}/inspect` | 操作者权限、绑定展示资料、群状态及范围元数据；不返回历史。 |
| `PUT /internal/group-experts/v1/groups/{id}/enabled` | 企业管理员代办，校验有效运行证明；关闭后仍保留历史与补偿。 |
| `PUT /internal/group-experts/v1/groups/{id}/scope` | `operation_id`、`revision`、来源 ID/记录代次、展示资料及授权修订；保留修订高水位，旧同步不覆盖新值，同修订的执行范围不同则冲突，名称头像独立更新。 |
| `PUT /internal/group-experts/v1/authorizations/{domain_key}` | `operation_id`、`revision`；更新镜像并持久登记失效待办后返回回执。`bindings:<group_id>` 域另携带 `revocations=[{source_agent_id,source_row_id,before_revision}]`，旧通知也应用独立水位并在同一事务使旧绑定失效、释放名额和生成系统提示。 |
| `POST /internal/group-experts/v1/groups/{id}/bindings` | `request_id`、操作者、管理路径、来源身份、预期范围修订；群锁内复核权限、范围、去重及 3 位专家上限。 |
| `DELETE /internal/group-experts/v1/groups/{id}/bindings/{binding_id}` | 指定绑定代次，失效标记、名额释放、系统提示和取消待办同事务提交；迟到移除不得影响新绑定。 |
| `GET /internal/group-experts/v1/calls/{call_id}` | 调用状态及固定登记身份，不包含问题或结果正文。 |
| `GET /internal/group-experts/v1/calls/{call_id}/context` | 内部鉴权后按调用记录决定发起人、绑定及输入消息；只读当前问题和至多一条直接引用，执行/回帖阶段忽略提交后的清空标记，但复核成员区间和撤回。 |
| `POST /internal/group-experts/v1/calls/{call_id}/admission` | 持久登记回执、执行 ID 和请求摘要；幂等更新受理结果并在群锁内安排派发。 |
| `POST /internal/group-experts/v1/calls/{call_id}/delivery` | 持久结果的 `run_id` 与 `result_digest`；将已派发调用推进到 `awaiting_delivery`，不传答案、不释放位置，重复确认不复活终态。 |
| `POST /internal/group-experts/v1/calls/{call_id}/result` | `result_wire` 原始规范结果、`result_digest` 和可选 `unpublished`；身份及授权取持久调用。永久不能发表时只发送元数据和原摘要并保留原因。合法结果信封中的无效文本/失效附件保存 failed/unpublished 回执，不用永久 4xx 占住群队列；附件尚在处理时可重试。群消息、终态回执、同步事件、队列推进同事务提交。 |
| `GET /internal/group-experts/v1/calls/{call_id}/receipt` | 只读原持久回执，已确认回帖不因后来撤权而改变；不重新取答案正文。 |

`GET /internal/group-experts/v1/pending` 返回到期的登记/派发/取消待办；
`POST /internal/group-experts/v1/pending/{operation_id}/ack` 绑定原请求摘要确认，
`POST /internal/group-experts/v1/pending/{operation_id}/retry` 保存退避。取消回执的
`saved` 不等于 `stopped`，已派发任务须等待停止或 unknown 结论后释放群位置。待登记 5 分钟、排队/派发 1 小时、待回帖 10 分钟超时均保存取消操作；取消等待 2 分钟仍未核清时，Go 只终止群回帖资格为 unknown 并保留后续核对。

内部签名使用 `purpose=group-experts-v1`、服务发行者/接收者、方法、完整路径、
原始请求体 SHA-256 与至多 15 秒有效期；普通用户 JWT 不可访问。

结果回执包含 `call_id`、`result_digest`、`call_state`、`delivery_state=confirmed|unpublished`，
成功回帖时另含 `message_id`、`seq`。重复投递同摘要返回原回执；不同摘要冲突。
永久不能回帖也须持久保存 `unpublished` 及原因码，使 Python 能停止投递。
`confirmed` 表示 Go 已提交相应完成/失败交付结论，不能用于仍在取消中的调用。

### 外部接口边界

Python 增加 `/api/im/groups/{id}/experts` 的绑定列表、逐个邀请/移除和
`/api/im/groups/{id}/experts/candidates` 的本人可调用候选；企业管理员从
`/api/im/organization/groups/{id}/experts` 管理绑定，
`PUT /api/im/organization/groups/{id}/expert-scope` 管理群范围，
`PUT /api/im/organization/groups/{id}/experts-enabled` 开关功能。
`GET /api/im/organization/expert-authorization-status` 返回持久待办的 `syncing`；
授权管理写响应通过 `X-Octop-Group-Expert-Syncing` 提醒客户端立即显示同步状态。
所有管理写请求携带 `request_id`。外部可见状态在
`/api/im/groups/{id}/expert-calls/{call_id}` 读取，先校验原消息对当前读者可见，
不返回快照、执行事件或尚未发表的答案。查询绑定与管理权限不授予历史读取权。
该按调用查询接口保留用于诊断和兼容；前端不再保留无人调用的封装或按调用轮询。
`group.expert.call` 只对可见原提问的当前成员生成。增量读取时会复核消息可见性，
已清空或超出成员可见区间的旧事件以 `sync.redacted` 返回，只保留同步序号，`payload` 为空对象。
客户端收到 `group.expert.call` 后按 `message_id` 刷新已缓存提问的调用状态，保留原有引用和提及。
单条消息返回 404 时移除过期缓存并继续同步，临时请求失败时保留游标供重试。
列表 `ready` 读取 Go 内现有能力证明是否有效，只有 Worker 心跳续写能力证明。
Python 重投已受理登记时返回原执行 ID 和空登记拒绝原因，执行期原因另随结果交付。

### Group-generated attachments (schema 20)

`POST /internal/group-experts/v1/calls/{call_id}/artifacts` accepts `run_id`,
`operation_id`, SHA-256 `file_id`, `filename`, `mime` and base64 `data` from the
signed execution service only. The call must be dispatched with matching
identity and live authorization. The tuple `(call_id, operation_id, file_id)`
is idempotent and cannot be rebound to different content. Images use existing
validation/normalization and thumbnails; videos are private downloadable files.
`GET .../artifacts/{asset_id}` returns processing status for that call only.

`result.attachments` may contain at most four ready asset IDs produced by this
call. The reply and its attachment references commit together. The asset owner
is the expert binding, never the caller; unpublished assets do not grant any
member owner access. Existing message visibility, recall and clear rules govern
downloads after publication. Cross-call IDs, wrong execution IDs, changed hashes
and forged ordinary user credentials are rejected.

### Reliability correction (schema 21)

`0021_group_expert_reliability.sql` stops legacy per-call recurring jobs without deleting their business obligations. New per-call jobs use `group.expert.reconcile.call`; only the canonical `group.expert.reconcile` job recurs. Reconciliation scans active calls by cursor with one transaction per call, and unchanged calls are not rewritten. `group_expert_calls.max_text_chars` fixes each new call's output budget. Authority triggers filter active calls before iterating. An unreconciled call or lost delivery cannot indefinitely reserve the group's dispatch slot; shared execution facts remain in Python.

Individual reconciliation failures are logged and retried on subsequent periodic passes without backing off the entire watchdog. Single-call jobs still return their own errors to the scheduler. Selection, acknowledgment and cancellation failures affecting the overall pass still propagate.
