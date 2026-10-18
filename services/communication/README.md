# Octop Enterprise 通信服务

基于 PostgreSQL 的独立 Go 通信服务，提供组织、员工身份、私聊、群聊及消息同步。该模块源自项目权利人开发的 Miaox 通信代码，采用 [MIT License](LICENSE)。

员工账号与认证由 Octop 控制面管理，通信服务负责消息、附件及同步。[SOURCE.json](SOURCE.json) 记录首次提取时的源提交与文件摘要，来源与许可说明见 [NOTICE](../../NOTICE.md)。

企业部署入口见[部署与恢复](../../docker/enterprise/README.md)。以下是服务配置与开发参考。

Required runtime variables:

- `OCTOP_IM_DATABASE_URL`: communication-only database and application role.
- `OCTOP_IM_IDENTITY_KEY_FILE`: shared Octop bridge credential key, at least 32 bytes.
- `OCTOP_IM_CURSOR_KEY_FILE`: independent stable cursor key, at least 32 bytes.
- `OCTOP_IM_MEDIA_ROOT`: private image volume; default `/data/media`.
- `OCTOP_IM_LISTEN`: default `0.0.0.0:8090` on the private service network.
- `OCTOP_IM_GROUP_MAX_MEMBERS`: default 500; this is a limit, not a load-test claim.

Run `go run ./cmd/im-server --migrate` once against the explicit empty communication
database, then `go run ./cmd/im-server`. Startup checks the exact migration level
and required tables; it never automatically rebuilds data. `GET /healthz` checks
PG connectivity. Docker builds support Linux ARM64 through TARGETOS/TARGETARCH.

The current communication schema is **15**. Upgrading an existing database requires
the explicit `--migrate` command before starting the updated service. Migration 14
adds claim tokens to both job queues without rewriting messages or existing work.
Stop older communication processes before migrating/restarting; old workers do not
enforce the new claim tokens. This is independent of the application version.

Recurring `sync.purge` and `media.cleanup` jobs are registered with an hourly
interval. The supervisor atomically releases the current claim and schedules the
next run; retry exhaustion waits for the next cycle. Startup restores terminal
maintenance rows left by older versions while preserving pending work and live
leases. One-shot jobs remain idempotent by work key.

Both queues renew their 60-second leases during execution. Renewal and result
writes require the current, unexpired claim token; lease loss cancels the handler.
Handlers still need to tolerate re-execution after a crash before acknowledgement.
Completed media processing recognizes its durable ready state on replay.

The bridge is enabled only by `OCTOP_IM_URL` on Octop. It requires a PostgreSQL
control plane and a private identity key file. Without that variable existing
Octop startup does not depend on Go. Every start re-enqueues current identities
and tombstones for idempotent reconciliation. Identity revisions are captured by
SQLite/PostgreSQL users-table triggers in the same transaction, including SSO,
bulk role assignment and deletion. Failed deliveries keep their revision and
backoff state. Acknowledged tombstones cannot be resurrected by newer upserts.

Migration 15 adds the account portrait URL to communication identities. The bridge
backfills existing portraits on startup reconciliation and publishes a revision
when a user or administrator uploads, removes, or replaces a portrait with a preset.
Contacts, snapshots, and group rosters return `avatar_url` and `avatar_icon`; images
remain on the control plane and require authenticated `/api/users/{id}/avatar` access.

The service acquires an organization transaction lock before conversation,
pair and sync locks, making membership changes and message sends serializable
against one another. Measure throughput with representative traffic before
setting deployment capacity.

Public surface and browser synchronization are documented in
[api/contract.md](api/contract.md). Deployment, backup and recovery are documented in
[the enterprise guide](../../docker/enterprise/README.md).

Targeted real-PostgreSQL checks:

```sh
OCTOP_IM_TEST_DATABASE_URL=postgresql://TEST_OWNER@127.0.0.1:TEST_PORT/postgres \
OCTOP_IM_TEST_OWNER=TEST_OWNER \
go test ./internal/itest ./internal/auth ./internal/mediasvc ./internal/store ./internal/syncsrv
```

The test helper creates and drops only `octop_im_test_*` databases. Do not point
its admin URL at a production instance. No external gateway, APNs, Redis or Kafka
is needed by these tests or the service.
