# 2026-11-06 Issue 修复与官方更新评估

本轮已落地两个 issue 局部修复，并按企业版现有实现适配合入四项官方修复。未升级项目或依赖版本，未提交、推送或部署。

## 对照范围

- 企业版：本地 `28c3c07`，项目版本 `1.0.1b3`，`octop-harness==1.0.3`。
- 官方最新发行：[v1.0.3b4](https://github.com/TencentCloud/Octop/releases/tag/v1.0.3b4)，GitHub API 记录发布时间为北京时间 2026-11-06 10:33:24。
- 以旧上游基线 `eb280112` 为入口扫描 302 条差异提交标题，重点审阅近期 12 个相关提交的文件和补丁，并与当前企业版实现对照。这不是对全部上游变更的兼容性验收。
- 两个项目的发行版本独立，不能用版本号大小判断企业版是否缺少某项修复。
- 保留工作区已有项目导航、输入框和文档修改；未修改旁边的 `octop/` 工作区。

## 已修复的 issue

### #3：模型请求中的内部角色名

来源：[Issue #3](https://github.com/colorAi/Octop-Enterprise/issues/3)。

修改 `src/octop/infra/agents/middleware/history_roles.py`：仅对发送给模型的 AI 消息副本，清除 `agent_` 前缀的 `name`，并清除旧消息 `additional_kwargs.name` 中相同的内部名称。正常名称、用户和工具消息、工具调用、推理字段、多模态内容、消息 ID 均保留；不改写持久化消息。

没有用正则删除正文中的角色前缀：正文可能是引用或代码，无法可靠区分。这次修复阻止内部名称继续进入模型请求，不承诺清除既有正文污染，也未将工具中断、空工具名称或 `finish_reason=length` 归因于此问题。

### #4：Docker Desktop 的宿主路径别名

来源：[Issue #4](https://github.com/colorAi/Octop-Enterprise/issues/4)。

修改 `src/octop/infra/backend/isolation.py`：先按原逻辑精确比较挂载；只有比较不一致且 Docker daemon 的 `OperatingSystem` 明确为 `Docker Desktop` 时，才允许配置中绝对源路径对应的 `/host_mnt` 前缀别名。

目标路径、挂载模式、挂载数量、命名卷、配置指纹、运行用户、内存、网络和权限校验保持严格。原生 Linux daemon 不接受该别名；不依赖控制面宿主系统判断，兼容控制面本身位于 Linux 容器内的场景。

验证使用模拟 Docker inspect 和真实校验方法，未启动真实 Docker Desktop 容器。

### #5：不修改

[Issue #5](https://github.com/colorAi/Octop-Enterprise/issues/5) 明确属于提交者自行移植的 `public-opinion-monitor` 技能。当前仓库没有该技能，也没有其原始采集证据，不据此修改官方技能或模型供应商配置。

## 已适配合入的官方修复

| 官方修复 | 企业版核实结果 | 本地处理 |
| --- | --- | --- |
| [#1454：远程 embedding 顺序](https://github.com/TencentCloud/Octop/pull/1454) | 原实现按响应数组顺序追加向量；响应索引为 `[1, 0]` 时，可能把向量关联到错误文档片段。 | 每批按 `index` 重排，拒绝重复、缺失、越界、布尔索引及混合有/无索引的响应。兼容所有条目均不带 `index` 的既有供应商，但要求数量正确并按其响应顺序处理。 |
| [#2779：问答暂停绑定正确会话](https://github.com/TencentCloud/Octop/pull/2779) | Dashboard 多个聊天共用 session key，原查找会把新聊天消息视为旧聊天待回答问题的答案。 | 校验当前 thread 与暂停记录的会话或恢复子线程一致；同时修改 processor 和企业版 execution gateway 的接收、恢复路径。未提供 thread 的 IM 消息保留原会话查找行为。 |
| [长截图尺寸限制](https://github.com/TencentCloud/Octop/commit/011b0f9949) | 原实现只检查文件字节数，32,059 字节、100×16000 的 PNG 可原样进入模型请求。 | 覆盖 workspace 引用、内联图片及历史重放；超长边缩放至不超过 8000 像素，同时保留 2 MiB 字节上限。仅修改发给模型的副本，保留原文件、checkpoint 和图片块附加字段；同步、异步调用均覆盖。 |
| [#2796：历史缓存错序](https://github.com/TencentCloud/Octop/pull/2796) | 原实现会把无共同 ID 的旧缓存尾部追加到新消息之后；不同 hook 的旧历史请求也可能覆盖新结果。 | 显式标记本地待确认消息；刷新仅保留未确认的新发送和仍在增长的回复。按会话状态跟踪请求，阻止过期响应覆盖消息、分页游标或删除后重建的会话。保留企业版服务端重建消息 ID 时的文本/时间去重，并扩展至连续多轮及部分已落库场景。 |

本次按修复点移植，没有整体合并上游分支，也未引入上游新增的 sessionStorage 历史缓存模块。历史刷新保留原有编辑分支版本保护和本地运行时消息处理。服务端重新生成 ID 时的兼容去重仍依赖原有文本/时间规则；附件独立标识和消息 ID 协议不在本次改动范围内。

## 已有、低优先级或需要独立决策的项目

| 官方修复 / 更新 | 当前判断 |
| --- | --- |
| [#2736：PostgreSQL executemany](https://github.com/TencentCloud/Octop/pull/2736) | 企业版 `_PgConnectionProxy.executemany` 已通过 cursor 执行并转换占位符，不需重复移植。 |
| [#2394：AUTO 模型失效后刷新](https://github.com/TencentCloud/Octop/pull/2394) | 企业版已有“全局默认缺失或不可用时，供应商修改触发 AUTO 重载”的实现及回归测试。上游另检查已构建 Harness 中的默认引用；暂不替换本地策略，可在遇到具体剩余场景时补充。 |
| [#2844：团队示例头像保存](https://github.com/TencentCloud/Octop/pull/2844) | 前端提交 `icon_url`，但 `TeamPatchBody` 未声明该字段，模型校验会将其丢弃。是低优先级小问题；当前界面使用固定团队头像，主要影响旧团队编辑时补齐头像，可在团队界面维护时同步。 |
| [#2809：共享连接器仅所有者可管理](https://github.com/TencentCloud/Octop/pull/2809) | 企业版明确保留了管理员管理其他用户连接器的分支。上游改成连管理员也不可管理，属于权限策略变化；不作为无行为影响的修复直接移植。应先确定企业管理员的凭据与管理边界。 |
| [#2813：专家工作区限制根目录](https://github.com/TencentCloud/Octop/pull/2813) | 企业版隔离模式会剥离用户 backend，并使用固定私有工作区和命名卷。上游针对宿主本地后端的目录重写不能覆盖这条策略；仅在维护非隔离本地后端时评估。 |
| [#2848：共享自定义 MCP 名称](https://github.com/TencentCloud/Octop/pull/2848) | 上游围绕访问者使用所有者专家 Harness 的名称映射；企业版共享专家先实例化成访问者自己的私有运行时，不能直接照搬。该提交还混有连接器分类和用量 UI，后续必须拆分评估。 |
| [#2830：预置模型同步 / HY4](https://github.com/TencentCloud/Octop/pull/2830) | 上游启动时会自动追加并启用新预置模型，且伴随 Harness 依赖更新。与企业版管理员控制模型目录和授权的策略有关，暂不自动同步。 |
| [#2716：Windows SQLite 降低 fsync 频率](https://github.com/TencentCloud/Octop/pull/2716) | 上游将 Windows 控制面 SQLite 从 FULL 调整为 NORMAL，涉及系统崩溃时的持久性取舍；企业部署重点是 PostgreSQL，不作为本轮必要修复。 |

官方 1.0.3b4 的云主机控制台、默认精简镜像、市场迁移及 Harness 1.0.8 不直接并入。企业版当前显式校验 Harness 必须为 1.0.3，并依赖其私有容器/工作区接口；依赖升级需要独立兼容验证，不能只修改版本约束。

## 验证

### Issue 修复

- 新回归在修改前出现 6 个预期失败：5 个内部名称场景、1 个 Docker Desktop 路径别名场景。
- 修改的两个模块及容器生命周期关联测试：**45 passed, 1 deselected**。
- 4 个修改的 Python 文件通过 Ruff 检查和格式检查；两个生产文件通过定向 mypy。
- 被排除的是已有 `test_v29_repair_is_idempotent_and_preserves_live_reservations`：断言数据库版本为 30，但当前迁移版本为 31。加载 HEAD 原版 `isolation.py` 后单独重跑仍失败，确认与本轮改动无关；未修改此测试。
- 未执行全量测试，未调用真实模型、启动容器或连接生产数据库。

验证命令：

```sh
uv run --no-sync ruff check src/octop/infra/agents/middleware/history_roles.py src/octop/infra/backend/isolation.py tests/unit/agents/test_history_roles.py tests/unit/backend/test_isolation.py
uv run --no-sync ruff format --check src/octop/infra/agents/middleware/history_roles.py src/octop/infra/backend/isolation.py tests/unit/agents/test_history_roles.py tests/unit/backend/test_isolation.py
uv run --no-sync mypy --follow-imports=silent src/octop/infra/agents/middleware/history_roles.py src/octop/infra/backend/isolation.py
uv run --no-sync pytest tests/unit/agents/test_history_roles.py tests/unit/backend/test_isolation.py tests/unit/backend/test_sandbox_budget.py -q -k 'not test_v29_repair_is_idempotent_and_preserves_live_reservations'
```

### 官方修复移植

- 六个后端相关测试文件：**117 passed**，包含向量批次重排、问答会话隔离及实际 gateway 路由、图片缩放和 checkpoint 不变性。
- 后端 12 个修改文件通过 Ruff 检查、格式检查；6 个生产文件通过定向 mypy。
- 七个前端相关测试文件：**88 passed**，覆盖历史合并、异步历史请求、分页、编辑分支、本地运行时、团队流、清空会话及待处理会话别名。额外复现并修复了连续多轮使用服务端消息 ID 后刷新产生重复消息的两个场景。
- 前端修改文件通过 ESLint 与 Prettier 检查；`make build-frontend` 成功，包含 TypeScript 编译和 Vite 生产构建，已重新生成前端静态资源。
- 未升级 Harness，也未调用真实模型或连接生产服务；未执行全量测试。

验证命令：

```sh
uv run --no-sync pytest tests/unit/knowledge/test_embed.py tests/unit/gateway/test_hitl_ask_user.py tests/unit/test_execution_gateway.py tests/unit/agents/test_workspace_image_middleware.py tests/unit/gateway/test_attachment_hints.py tests/unit/gateway/test_dashboard_ws.py -q
uv run --no-sync mypy --follow-imports=silent src/octop/infra/knowledge/embed.py src/octop/infra/gateway/hitl/coordinator.py src/octop/infra/gateway/process/processor.py src/octop/infra/execution/gateway.py src/octop/infra/agents/middleware/workspace_image.py src/octop/infra/gateway/media/attachment_hints.py
cd dashboard
npx vitest run src/pages/Chat/utils/mergeHistoryTail.test.ts src/pages/Chat/hooks/useChat.history.test.ts src/pages/Chat/hooks/chatStore.history.test.ts src/pages/Chat/hooks/chatStore.localRuntime.test.ts src/pages/Chat/hooks/chatStore.teamStream.test.ts src/pages/Chat/hooks/chatStore.clearMessages.test.ts src/pages/Chat/hooks/chatStore.pendingAlias.test.ts
npx tsc -b
# 在仓库根目录执行：
make build-frontend
```
