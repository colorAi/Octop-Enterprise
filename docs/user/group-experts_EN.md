# Group experts

Company, department and project groups can invite up to three experts and ask one expert per message through the `@` menu.

## Deployment requirements

This version requires PostgreSQL and the Web/Worker deployment in `docker/enterprise/compose.roles.yml`. Base mode and the enterprise monolith in `docker/enterprise/compose.yml` do not support group experts. Ordinary colleague messages and groups remain available in the enterprise monolith.

Build the services from the same release source. The application schema is 36, the communication schema is 21, and every Worker in the configured topology must be ready and advertise group expert protocol 2. Readiness also requires a current capability heartbeat.

For a new installation, prepare configuration, keys and images with the [enterprise guide](../../docker/enterprise/README_EN.md), then follow the complete [role initialization procedure](../../docker/enterprise/ROLES.md#初始化与启动), using one resource profile consistently. Before converting an existing monolith, back up both databases, files, configuration and workspaces; stop the old application and its workspace containers. Preserve data and keys and do not rerun `prepare.py` or `octop init`. Existing role-based Workers require verified stop evidence before a new boot generation is created.

## Enable and invite

1. An enterprise administrator configures expert sources, employee expert access and model grants in company administration.
2. Open group management in the company structure, choose a group and configure its permitted expert sources. Department groups also use their department's resource scope.
3. Enable experts for that group and invite a source expert. Every group starts with this feature disabled.
4. Group owners and administrators can invite from the permitted scope through group details.

An `AUTO` source requires a valid company default model. Members still need permission to use the source expert and its model; managing a group does not grant model access.

## Questions and results

Select an expert in the `@` menu and enter a question. The expert receives the current question and at most one directly quoted text message, without full group history, personal workspaces or personal memory. For follow-ups, quote the previous answer and mention the expert again.

Search, image and video tools use the source expert's enabled tools and company services. Generated assets accompany the group reply. Model usage counts toward the asking member's quota. If admission fails, an otherwise valid ordinary message remains in the group and shows the reason.

## Troubleshooting

- **Group experts are not ready:** confirm the role-based deployment, all Worker versions and states, both schema migrations and communication service availability. `group expert heartbeat will retry` in the primary Worker log indicates heartbeat failure. Synchronize clocks across Python, Go and PostgreSQL hosts.
- **Experts are disabled:** an enterprise administrator must enable the feature for this group.
- **No sources to invite:** check the source administrator, enabled experts, group scope, department scope and authorization synchronization.
- **Cannot call an invited expert:** check membership, speaking permissions, expert and model grants, and quota.
- **Only the browser has the feature:** install a matching desktop client; configuration notifications do not download a new UI.

After health checks pass, verify an actual invitation, question and reply. See the [role deployment guide](../../docker/enterprise/ROLES.md) for operational commands and resource limits.
