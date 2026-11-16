# Enterprise process architecture

The [role deployment](../../docker/enterprise/ROLES.md) runs an edge proxy, Web processes, a fixed topology of runtime Workers, one Go communication service and PostgreSQL. Resource profiles select the Worker count and limits; they do not establish a tested user capacity.

Web processes authenticate requests and serve lightweight metadata. Colleague messages, groups and notifications under `/api/im/` go directly from the authenticated Web process to Go. Expert execution and device requests route to the user's configured Worker; a failed owner is not replaced by replaying the request on another Worker.

Worker identity consists of its node and boot generation. Recreating a Worker requires verified evidence that the previous generation and its workspaces stopped. Shared tasks, events, commands and usage reservations are durable; external model and tool results can remain unknown and must be reconciled without automatic replay.

[Group experts](../user/group-experts.md) require this distributed execution mode, application schema 36, communication schema 21 and protocol 2 on every configured Worker. The primary Worker renews an expiring capability proof in Go. An unavailable or incompatible node disables new group expert admission.

The compatibility enterprise monolith supports ordinary messaging but does not assemble the group expert Worker executor. It must not run concurrently with distributed Workers against the same execution domain.

For initialization, resource profiles, backups, verified stops and recovery commands, use the [operator guide](../../docker/enterprise/ROLES.md). The persistence boundary is defined in the [execution contract](execution-contract.md).
