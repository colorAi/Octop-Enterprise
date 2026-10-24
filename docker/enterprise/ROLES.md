# 分角色部署、备份与恢复

`compose.roles.yml` 提供同一镜像的 `init`、`web` 和 `worker` 入口，使用 PostgreSQL schema 30。产品版本仍为 1.0.1b2。此入口需要在目标环境完成联调后再用于现有实例；本地容器链路验证不等于原生客户端、完整业务负载或容量验收。

拓扑为代理、2–3 个 web、1–3 个运行时 worker、1 个 Go 通信服务和 PostgreSQL。`4g` 启用 2 web + 1 worker，`8g` 启用 2 web + 2 worker，`16g` 启用 3 web + 3 worker。web 本地处理权限和执行状态读取，同事私聊、群聊及实时通知的 `/api/im/` 请求由 web 鉴权后直接转发到 Go 服务，不经过专家 worker。其他 HTTP、设备和专家 WebSocket 请求按数据库保存的用户归属转发；不依赖粘性会话，不自动重试 POST。

web 挂载通信身份签名密钥，继续使用原有权限、会话撤销和一次性实时连接票据校验；全局身份后台同步仍只有一个协调者。同事消息不占专家任务或模型槽。两套服务仍共享宿主和数据库，进程分离不能消除整机资源耗尽造成的影响。

worker 按排序后的节点列表和员工用户 ID 分片，同一用户的 Agent、工作区、定时任务、IM 渠道和设备归同一 worker。管理员与全局管理入口归协调节点 `runtime-worker`；全局维护、身份后台投递与源发布仅在该节点运行。其他 worker 轮询共享配置并更新本地已加载实例。所有 worker 共用任务队列、可选的模型并发限制与用量额度。归属节点不可用时请求明确失败，不交给其他节点重做。禁止用 `--scale worker=2` 复制相同节点身份；扩缩容使用下面的停机切换流程。

## 初始化与启动

沿用 [企业部署指南](README.md) 的主机、源码镜像、目录和密钥准备。`compose.roles.yml` 是独立入口，不与 `compose.yml` 合并。已有实例保留全部数据和密钥，不能再次运行 `prepare.py`、`octop init` 或 `init --force`。

1. 保存原镜像和可恢复的两库、app/media/secrets、工作区卷及环境文件备份，在临时实例验证恢复。
2. 停止旧入口接收新任务，排空并核对活动任务，停止旧单体、通信和其工作区容器。旧单体与新 worker 不能同时连接同一执行域；旧版代码不会识别新的执行模式标记。
3. 为本次启动生成独立标识：`python3 -c 'import uuid; print(uuid.uuid4().hex)'`。将结果保存为现有私有环境文件中的 `OCTOP_WORKER_BOOT`，不修改其他密钥。worker 被停止后必须先核实旧代次，再使用新的标识重建。
4. 在源码根目录执行以下命令。示例选用 `4g`；更换档位时，每条命令使用同一档位文件。三档限额均不承诺容量，环境文件需要引用同次构建的基础、应用和 Go 镜像。

```sh
sudo docker compose --env-file /etc/octop.env --env-file docker/enterprise/profiles/4g.env -f docker/enterprise/compose.roles.yml config -q
sudo docker compose --env-file /etc/octop.env --env-file docker/enterprise/profiles/4g.env -f docker/enterprise/compose.roles.yml build
sudo docker compose --env-file /etc/octop.env --env-file docker/enterprise/profiles/4g.env -f docker/enterprise/compose.roles.yml up -d --wait postgres
sudo docker compose --env-file /etc/octop.env --env-file docker/enterprise/profiles/4g.env -f docker/enterprise/compose.roles.yml run --rm --no-deps communication --migrate
sudo docker compose --env-file /etc/octop.env --env-file docker/enterprise/profiles/4g.env -f docker/enterprise/compose.roles.yml up -d --wait
```

全新实例需要在上面的 `up -d --wait` 之前，按原指南交互创建一次管理员：将原 `octop init` 命令的 Compose 服务名改为 `initialize` 即可。Compose 启动依赖中的初始化角色执行主库及共享检查点迁移、保留或首次生成 JWT、安装插件依赖及保存拓扑和全局限额，成功退出后才启动 worker/web；不需要再手动运行一遍初始化角色。初始化锁避免并发建表/索引冲突。web/worker 校验初始化标记；每个旧 worker 都必须有停止证明，否则初始化拒绝执行。

代理默认仅绑定宿主 127.0.0.1:8088。生产 HTTPS 仍在外层受信任代理终止；worker 内部固定使用 HTTP，不继承旧单体的 TLS 监听配置。web 没有 Docker socket，数据目录只读；worker 的 Docker socket 仅供受信任控制面使用。

```sh
curl -fsS http://127.0.0.1:8088/api/health/ready
sudo docker compose --env-file /etc/octop.env --env-file docker/enterprise/profiles/4g.env -f docker/enterprise/compose.roles.yml ps
```

web 就绪检查同时核对自身启动代次、数据库租约和运行时主节点。worker 额外检查数据库、通信与沙箱。健康检查不能替代一次真实隔离工具调用。

## 候选资源限制

| 档位 | web / worker 数量 | 服务器任务 / 默认模型并发 | 全局任务排队上限 | web 数据库池（每个） | worker 池（每个） | Go 池 | 默认线程池（web / worker） |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 4g | 2 / 1 | 2 / 不限制 | 128 | 2 | 4 | 8 | 4 / 8 |
| 8g | 2 / 2 | 4 / 不限制 | 256 | 3 | 6 | 12 | 4 / 12 |
| 16g | 3 / 3 | 8 / 不限制 | 512 | 4 | 8 | 16 | 4 / 16 |

模型请求默认没有 Octop 全局并发上限，API 与本地部署的模型一致，不随内存档位限制为 2/4/8 路。实际可用并发取决于供应商或本地推理服务的配置与容量。需要主动限制时，在部署环境文件中设置 `OCTOP_MODEL_CONCURRENCY=100` 等正整数；`0` 或不设置表示不限制。该值由 init 保存至数据库，按上面的维护流程停止旧 worker、记录停止证明并重新初始化后生效。它仅限制同时进行的请求数量，不代替上游的每分钟请求数或 token 速率限制，也不放宽账号 token 配额。

服务器任务仍按上述档位排队，每用户最多 8 个排队/审批等待任务；客户端执行工具的任务不占服务器工具任务槽。桌面模型请求只有在明确启用模型并发上限时才使用等待队列，等待最多 90 秒，待处理上限沿用全局/每用户队列配置。正在调用上游的请求不计入待处理队列。Web 转发连接池不设活动连接数上限，仅保留最多 16 条空闲连接；模型长连接不会被固定的 64 条转发连接额度阻挡。已运行的模型请求进行状态校验和用量结算时等待短事务处理，不因数据库队列瞬间满额而丢失回执。

每个角色另有一条 LISTEN 连接；初始化另用迁移锁和检查点连接。已加载 Agent 的记忆/检查点有自己的连接池，也需要计入 PostgreSQL 的 80 连接上限，并为备份和维护预留余量。队列满显式拒绝；未知模型请求保留未结算额度，启用模型限制时也保留对应槽位。日志写容器标准输出，由 Docker 限制轮转大小。

角色容器及每个沙箱分别有内存/PID 限制，同一执行域的沙箱还共用下述总预算。整机仍需计入操作系统、worker 内已加载的专家、数据库缓存、媒体及构建占用。尚未验证 50 / 100 / 200 人目标，也未验证 20 分钟、2 小时或 24 小时稳定性。不能仅调大槽数或复制容器就承诺人数。

## 沙箱总内存准入

| 档位 | 每个沙箱上限 `OCTOP_SANDBOX_MEMORY` | 共享总预算 `OCTOP_SANDBOX_TOTAL_MEMORY` |
| --- | --- | --- |
| 4g | 256 MiB | 512 MiB |
| 8g | 384 MiB | 1536 MiB |
| 16g | 512 MiB | 4096 MiB |

两个限额由 init 从档位文件写入数据库，worker 从数据库读取，增加 worker 数量不会增加预算。值为正整数字节数或带 `k`/`m`/`g` 后缀的二进制大小，每个沙箱至少 6 MiB，总预算不能小于单个沙箱。更改限额与拓扑切换一样，必须先停止所有旧 worker 及其沙箱并记录停止证明，再运行 init。升级前尚未设置预算的 schema 29 数据库，也必须重新初始化后才能启动新 worker/web；迁移补齐表，不修改产品版本。

每次创建或恢复容器前，短事务原子预留完整内存上限，不按瞬时 RSS 超卖。卷初始化容器最多使用 128 MiB，与主沙箱共用预留额度，确认初始化退出后才启动主沙箱；两者都限制内存且禁用额外 swap。预算涵盖同一数据库执行域、同一 Docker 主机上的这些沙箱，不包含其他安装实例或宿主其他服务。

预算不足返回 HTTP 503、`SANDBOX_MEMORY_EXHAUSTED` 和 `Retry-After: 5`，同时触发各 worker 提前回收空闲沙箱。普通专家对话先进入持久队列，领取后才创建运行时；显式选择技能或 MCP 的请求仍在准备时验证所选内容。领取任务后、模型和工具尚未执行时，启动因预算不足而被拒绝会触发回收并间隔 1 秒重试，最多等待 10 秒；仍不足则保存明确失败回执并释放会话占用。模型或工具一旦开始，不按此机制重发。执行中、排队、审批等待、取消中、unknown 或持有活动引用的运行时均受保护。

确认主沙箱及初始化容器停止后才释放额度。Docker 启停超时、worker 失联和租约过期均保留预留；初始化或创建发生不确定错误时也如此。恢复流程会检查预留名称、登记的容器和标签发现的容器；已登记容器缺失或引擎错误仍不作为停止证明。只对确认旧 worker 已停止、且从未登记的待创建容器名称，允许用引擎返回不存在确认未使用的预留。停止的初始化容器和主沙箱保留供核验，工作区卷保留数据。

## 空闲运行时回收

`OCTOP_RUNTIME_IDLE_SECONDS` 默认 300 秒，可设置 1–86400 秒，扫描间隔最多 5 秒。无活动调用、排队、审批等待、取消中或结果未知任务时，worker 卸载闲置 Agent、关闭记忆运行时及沙箱句柄，并确认沙箱容器停止。保留用户归属、数据库启用状态、对话记录与工作区卷；下一次访问按原归属重新加载。回收不等于用户停用，也不删除内容。

分角色启动时，管理员源专家也按需加载。发布定义和技能使用串行、只读的工作区视图，读取完成即停止，不启动模型、记忆或完整专家运行时；该临时容器也计入共享沙箱预算。已发布的员工资源读取不要求源专家常驻。

HTTP 流及后台写入、交互终端、源发布、主动关怀和 IM 附件传输期间暂停对应回收。聊天与设备长连接本身不阻止回收，实际任务由持久占用保护；IM 附件每次重新获取工作区。停止失败保留句柄供后续重试，不将其报告为已回收。共享预算不足时按最近使用时间优先回收空闲实例，不必等到正常闲置阈值；活动保护仍然有效。总预算和回收不能代替整机容量验收。

## 任务补读与故障核对

- 支持共享执行的流会增加 `execution_id`、`event_seq`、`event_index`。网页重连以这三个字段订阅；同一事件批次的剩余帧也能补读。旧客户端仍可接收原帧；浏览器整页刷新后，历史照常读取，不能自动重发旧消息恢复任务。
- 当前用户可访问 `GET /api/executions/{id}`、`GET /api/executions/{id}/events?after=0&limit=100`，以及 `POST /api/executions/{id}/cancel`，请求体为 `{"request_id":"唯一取消编号"}`。取消返回成功表示接受命令，不表示外部写入已回滚。
- 原有 steer 继续使用运行时内的队列；运行时故障时不自动重放尚未执行的 steer。它不属于本版持久命令接口。
- 租约超时或进程异常后，任务进入 `unknown`，会话及未结算额度保留。设备迟到回执按操作号和连接代次核对；不把超时当作工具失败并自动重做。

维护命令使用运行环境中的 PostgreSQL 和私有配置。以下 `RUN_ID`、`INCARNATION` 必须来自实际查询结果，不能自行填写“已停止”。

```sh
octop execution nodes
octop execution list --state unknown --limit 100
octop execution inspect RUN_ID
octop execution drain --node runtime-worker --incarnation INCARNATION
```

在容器部署中，通过 `docker compose ... exec -T worker octop execution ...` 调用上述命令。worker 停止后，改用 `docker compose ... run --rm --no-deps worker octop execution ...` 创建短暂维护进程；它不启动运行时。其他嵌入式/离线管理 CLI 会拒绝修改已切换的执行域，配置管理继续使用在线管理入口。

恢复步骤：

1. 关闭入口，停止新领取，等待可确认的活动任务结束，再停止 worker。
2. 根据 `org.octop.node`、完整 `org.octop.incarnation` 和 `octop.agent.id` 标签列出并停止该代次的**全部**沙箱。保留原容器，直到证据记录完成；沙箱不在 worker 的进程命名空间内。
3. 运行 `octop execution confirm-stop --node runtime-worker --incarnation INCARNATION`。命令只读取 Docker：核对完整容器 ID、启动时间、标签、停止状态以及登记/漏登记的沙箱；缺失容器或引擎超时都不会算作停止证明。
4. 核对未知任务的实际外部结果。证据 JSON 必须包含至少 10 个字符的 `reason`，以及 inspect 列出的每个未知模型请求的实际 token 数和每个未完成工具操作的回执。示例：`{"reason":"已与提供商账单及文件结果核对", "models":{"模型请求编号":123}, "tools":{"操作编号":{"status":"completed","sha256":"实际文件摘要"}}}`。没有相应项目时填空对象，不能随意填 0 或虚构回执。
5. 用私有证据文件运行 `octop execution reconcile RUN_ID --outcome completed --evidence /path/evidence.json`；也可使用 `failed` / `cancelled`。此操作记录审计证据、结算额度和解除会话，不执行工具。证据可在新的 worker 启动后核对，旧代次的停止记录会保留。
6. 更新 `OCTOP_WORKER_BOOT`，重建 worker 后启动 web/代理。设备重新连接后获得新代次；需要重新加载运行时和连接。旧代次无法继续写回。

不要对 worker 使用自动重启策略或直接 `docker restart`；旧执行域未确认停止时，新启动会失败。停止证明和业务结果核对是两个独立步骤。

## 扩缩容

在维护窗口中关闭入口，按恢复步骤逐一排空并停止**所有**旧 worker 及各自沙箱，记录每个节点、完整代次的停止证明。例如 8g 扩到 16g 时，先对 `runtime-worker` 与 `runtime-worker2` 完成核验，再将全部 Compose 命令切换为 `profiles/16g.env`，更新 `OCTOP_WORKER_BOOT` 并执行 `up -d --wait`。未单独配置时，worker2/worker3 也使用该启动随机值；若配置了 `OCTOP_WORKER2_BOOT`、`OCTOP_WORKER3_BOOT`，必须分别更新。缩容后移除已经核验停止且不在新档位中的旧服务容器。

init 在确认全部旧执行者停止后更新 `OCTOP_WORKER_URLS` 拓扑并重新分配用户。原工作区卷保持不变，旧任务及 unknown 占用保留，任何不确定模型/工具调用都不会自动重放。此流程支持同一 Docker 主机上的停机扩缩容，不提供在线用户迁移或跨主机卷复制。用户角色在管理员与员工之间变化可能改变分片，也应在维护窗口按同一停止核验流程切换。

## 分角色备份与回退

使用同一版本的备份脚本，并完整列出工作区卷：

```sh
sudo python3 docker/enterprise/backup.py --roles --profile 4g --env /etc/octop.env --output /srv/backups/octop-roles-SNAPSHOT --volume ACTUAL_WORKSPACE_VOLUME
```

仅当确实没有工作区卷时使用 `--no-sandbox-volumes`。脚本停止入口/角色/通信及指定卷的沙箱，核实并记录执行者停止证明，再导出两库、目录和全部指定卷。漏掉仍运行的沙箱会使停止核验失败。`manifest.json` 必须为 `complete=true`，逐文件核对 SHA-256。

**分角色备份结束或失败后都保留写入组件停止状态**，不自动重启旧 worker/沙箱。按上节核对并重建后再开放入口。备份包含环境文件及选定档位文件，须按私有数据保管。新主机恢复使用匹配的两库和卷；恢复后沿用已保存的停止证明，创建新启动代次，不能让旧实例和恢复实例同时写同一安装身份。

回退到旧代码必须恢复其支持的 schema 和匹配文件快照，并另行保留新增结果。schema 29 及分角色模式不能通过减少副本数回退到 schema 25/28。版本不因切换拓扑自动升级。

## 验收记录

按 [更新计划阶段四、五](../../docs/design/enterprise-update-plan.md) 逐项记录：真实 Go/数据库与原生客户端联调、两个接入间重连补读、worker/沙箱停止核验、模型/工具未知结果、重复请求、撤权与调岗、慢工具及通知/数据库中断。每档记录硬件、构建摘要、到达率、接受/拒绝率、排队与完成率、HTTP p50/p95/p99、事件循环延迟、整机内存峰值、数据库连接、OOM 和恢复结果。只记录实际完成的 20 分钟/2 小时/24 小时观察；其余保留未验收。
