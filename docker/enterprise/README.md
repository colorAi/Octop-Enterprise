# Octop Enterprise 企业部署与恢复

[English](README_EN.md)

本指南适用于 Linux Docker Engine + Compose v2。基础单机模式使用 `docker/docker-compose.yml`。企业模式包含 Octop 控制面、Go 通信服务、两个 PostgreSQL 数据库和员工隔离工作区。客户端在员工电脑执行项目工具，服务器保留账号、模型授权、记忆、历史和恢复回执。

本指南对应 Octop Enterprise **1.0.1b2**，依赖 Harness **1.0.3**，主数据库 schema **30**，设备协议 **2**，公司运行时协议 **1**。服务端镜像与客户端工具环境需使用匹配的源码和协议版本构建。

当前工作区的分角色入口及 schema 29 操作见[分角色部署、备份与恢复](ROLES.md)。下文 `compose.yml` 命令用于既有单体拓扑，不直接套用于分角色实例。

## 部署前先了解

采用**面向企业 IT 的源码部署**：Compose 管理应用、通信和 PostgreSQL 服务，脚本生成私有配置；管理员准备 Docker、构建镜像、完成初始化、配置访问地址和分发客户端。安装、备份与恢复按本指南执行；离线环境需另行准备镜像与依赖。

先在企业内网完成一名员工的登录、模型调用、本机任务、消息与附件验证，再扩大使用。硬件与人数配额需通过实际业务负载测试确定，并为镜像构建、数据库、媒体存储和并发请求预留资源。

### 服务器准备

| 项目 | 要求 |
| --- | --- |
| 系统与权限 | Linux Docker 主机；管理员具有 sudo 和 Docker 操作权限 |
| 容器工具 | [Docker Engine](https://docs.docker.com/engine/install/) 与 [Docker Compose 插件](https://docs.docker.com/compose/install/linux/)；本指南命令使用支持 `up --wait` 的版本 |
| 宿主工具 | Python 3.11+（配置与备份脚本）、curl；Node、uv 和 Go 在镜像构建阶段使用，无需先在宿主安装 |
| 构建网络 | 能获取 Dockerfile 中的基础镜像、系统包以及 npm、Python、Go 依赖；纯离线服务器需要另行准备这些构建材料 |
| 访问与模型 | 确定员工访问的内网地址；准备可用模型服务及其凭据。生产访问入口可使用企业域名和受信任的 HTTPS 证书 |
| 数据与备份 | 独立持久化目录与目录之外的备份位置；员工电脑项目另外备份 |

先确认工具可用，下列操作只读取环境：

```sh
sudo docker info
sudo docker compose version
python3 --version
```

Docker daemon 无法连接时，先处理 Docker 启动或 socket 权限。默认 socket 为 `/var/run/docker.sock`；使用其他 socket 时，在首次生成配置时传入 `--socket /absolute/path/to/docker.sock`，并确保 Docker CLI 使用同一引擎。不要把本机开发环境的 socket 或绝对目录照抄到服务器。

## 首次安装

使用本项目源码包或确定的发行提交，在仓库根目录逐步执行。采用源码包时记录 `PUBLIC_SOURCE.json` 与源码包校验值；采用 Git 时记录 commit 和本地改动。下列 `enterprise-1.0.1b2` 是本指南使用的本地镜像标签；每次部署使用可追溯到对应源码的唯一标签。

### 1. 生成新的私有配置

```sh
sudo python3 docker/enterprise/prepare.py --data /srv/octop --env /etc/octop.env
```

`prepare.py` 只接受全新数据目录和新的环境文件，自动生成三个独立数据库密码及两个通信密钥，并设置私有目录与文件权限。使用 sudo 执行时会为应用、媒体和密钥目录设置 UID/GID 1000；PostgreSQL 目录由官方镜像入口初始化。没有使用 sudo 的部署需按脚本提示自行修正所有者。

这个脚本不能用于升级或修复已有实例，已有密钥必须保留。

### 2. 设置镜像与员工访问入口

使用管理员编辑器修改 `/etc/octop.env`，保留已生成的密码与密钥，在原文件内将三个镜像值改为：

```dotenv
OCTOP_BASE_IMAGE="octop:enterprise-1.0.1b2"
OCTOP_APP_IMAGE="octop-enterprise:enterprise-1.0.1b2"
OCTOP_IM_IMAGE="octop-im:enterprise-1.0.1b2"
```

默认 `OCTOP_BIND_ADDRESS="127.0.0.1"`，8088 端口只供服务器本机或本机反向代理访问，员工电脑无法直接连接。按部署场景选择：

| 场景 | 配置与员工填写的地址 |
| --- | --- |
| 企业 HTTPS 入口 | 保持本机绑定，按 `nginx.conf.example` 配置域名和系统受信任证书；员工填写 `https://企业域名` |
| 可信内网 HTTP 联调 | 将绑定值改成服务器实际的内网网卡 IP，按企业网络策略允许员工访问；员工明确填写 `http://服务器内网IP:8088` |

客户端省略协议时使用 HTTPS，不会自动降级到 HTTP。修改绑定地址不等于自动配置反向代理、证书或防火墙。PostgreSQL 和通信服务均不映射宿主端口；员工只需要应用入口。

### 3. 构建、初始化并启动

```sh
sudo docker build -f docker/Dockerfile -t octop:enterprise-1.0.1b2 .
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml config -q
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml build
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml up -d --wait postgres
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml run --rm --no-deps communication --migrate
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml run --rm --no-deps octop octop init
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml up -d --wait
```

逐条确认成功后再继续。`config -q` 仅验证配置，不输出带密码的解析结果；[Compose 的 `--wait`](https://docs.docker.com/reference/cli/docker/compose/up/) 等待服务进入运行或健康状态。通信迁移和管理员初始化不提前启动其他应用服务。

`octop init` 在终端交互输入管理员用户名和密码，不把密码写进命令行。只在首次安装执行；升级不能再次初始化，也不能使用 `--force`。PostgreSQL 只在首次创建数据目录时执行 `init-databases.sh`，分别创建 `octop_app/octop` 和 `im_app/octop_im`，禁止应用角色访问对方数据库。

首次构建需要下载和编译依赖，之后可复用构建缓存；容器启动成功还不等于员工已能工作，继续完成下一步。

基础应用镜像预装锁定版本的本地向量模型运行时（`local-embedding`），企业镜像构建时会以 UID 1000 检查这些依赖能否导入。模型权重仍在管理员选择下载后获取，保存在持久化应用目录的 `embedding_models/` 下。旧镜像出现“依赖尚未就绪”或 Python 环境无写入权限时，按升级流程重新构建基础应用与企业镜像并替换应用容器；仅更新源码文件或重启旧容器不会补齐镜像依赖。

下载完成只保存模型文件；模型测试、知识库索引与检索时才加载推理会话。大模型的运行内存需要单独计入每个 Worker 的容器限额和宿主总预算，下载成功不代表当前限额足够运行。若日志出现 `OOMKilled` 后伴随 `executor_fenced`，先核对内存限额，再按 [分角色恢复流程](ROLES.md#任务补读与故障核对)恢复节点，不要直接重启旧 Worker。

### 4. 检查服务并完成首个员工流程

```sh
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml ps
curl -sS http://127.0.0.1:8088/api/health/ready
```

默认本机绑定时 readiness 应返回 `{"ok":true,"checks":{"database":true,"communication":true,"sandbox":true}}`；更改绑定地址时改用对应内网 IP。检查项位于 `checks` 对象内。`sandbox` 检查 Docker 连接和镜像存在，不能代替真正执行一次隔离任务。

1. 浏览器打开已配置的员工访问地址，使用刚创建的管理员账号登录。
2. 在左侧“管理 → 模型”配置至少一个可用模型服务；在“管理 → 用户”创建测试员工并授予所需权限，准备可供员工使用的专家。
3. 给员工分发与服务器兼容的 Mac/Windows 企业客户端。只部署服务器不能自动生成完整的员工安装包，构建入口见[桌面说明](../../desktop/README.md)（English）。
4. 员工在桌面客户端登录后，打开左侧“设置”分组的“本地工作目录”，确认默认目录或点击“更换文件夹”选择项目。让专家读取一份样例文件并输出摘要，确认成果和历史可查看。
5. 检查同事消息、附件与实际需要的群聊，再修改测试员工权限并核对配置通知。手动发布配置通知的入口是左侧“管理 → 应用设置”的“应用更新”标签。完整企业备份与恢复还需另做演练。

分发客户端前，核对安装包的平台、签名、公证和设备验证记录。

### 常见问题

| 现象 | 优先检查 |
| --- | --- |
| 服务器本机能访问，员工电脑连不上 | 是否仍绑定 127.0.0.1、内网地址与端口是否正确、反向代理及企业网络策略 |
| HTTP 内网地址出现证书或连接错误 | 客户端是否省略了 `http://`；HTTPS 证书是否由客户端系统信任 |
| `/api/health/ready` 返回 503 | 查看响应中为 false 的检查；主库连接、通信迁移、Docker socket 与 sandbox 镜像 |
| 应用启动报目录或密钥无权限 | 数据目录及密钥是否属于 UID/GID 1000，Docker socket 组号是否与配置一致 |
| 首次构建卡住或失败 | 基础镜像和依赖下载、构建主机内存与磁盘，按失败的具体构建阶段处理 |
| 员工看不到授权菜单 | 账号权限、配置刷新、此前是否手动隐藏该菜单；确认客户端包含相应功能代码 |
| 网页已更新，员工客户端仍是旧界面 | 客户端资源随安装包内置；配置通知不下载新界面，需要匹配的新安装包 |

查看最近服务日志：

```sh
sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml logs --tail 100 octop communication postgres
```

### 部署边界

密钥文件不得在重建时重新生成；改变通信身份或游标签名密钥会破坏已有身份及同步状态。企业容器的服务端隔离 worker 使用同次构建的镜像，UID/GID 1000、只读根、无网络、drop ALL capabilities、no-new-privileges，并挂载各自命名卷。服务器的 Docker socket 仅供受信任管理员维护的控制面使用，不得挂入员工 worker。

本目录提供单机 Compose 运维入口，不包含高可用集群、自动扩缩容或一键灾备切换方案。公网访问需配置 HTTPS 和访问策略，并测试历史消息、图片加载及实际并发性能。

## 配置

下表同时列出 Compose 输入和容器内的进程配置。当前 `compose.yml` 会固定设备通道、本机运行时、数据库连接和 Docker 隔离等进程配置；把同名变量加到 `/etc/octop.env` 不会自动覆盖这些固定值。环境文件直接参与替换的参数包括数据目录、镜像、绑定地址与端口、Docker socket/GID、HTTP 媒体 origin 和时区；其他变更需要同步调整 Compose 或使用经过核对的 override。

| 配置 | 含义 |
| --- | --- |
| `OCTOP_DATA_ROOT` | 可由环境文件覆盖；目标 Docker 主机绝对目录，含 app/media/secrets/postgres |
| `OCTOP_DATABASE_URL` | Compose 构造，不能用同名环境变量覆盖；Octop 主库连接，Compose 从独立密码构造 |
| `OCTOP_IM_DATABASE_URL` | Compose 构造，不能用同名环境变量覆盖；通信专用主库，不能使用 Octop 角色 |
| `OCTOP_IM_URL` | Compose 固定；Octop 到通信服务的固定内网 origin；不设则禁用通信 |
| `OCTOP_IM_IDENTITY_KEY_FILE` / `OCTOP_IM_CURSOR_KEY_FILE` | Compose 固定路径；稳定私有密钥文件；前者两服务共享，后者仅通信使用 |
| `OCTOP_DEVICE_CHANNEL_ENABLED` | Compose 固定为 true；启用原生设备通道 |
| `OCTOP_COMPANY_RUNTIME_ENABLED` | Compose 固定为 true；启用企业本地执行，依赖设备通道 |
| `OCTOP_AGENT_ISOLATION=docker` | Compose 固定；强制员工服务器工作区隔离 |
| `OCTOP_SANDBOX_IMAGE` | 由 OCTOP_APP_IMAGE 推导，不能用同名环境变量覆盖；同次构建的企业镜像；发布后固定唯一标签/digest |
| `OCTOP_SANDBOX_HOST_DATA_DIR` | 由 OCTOP_DATA_ROOT 推导，不能用同名环境变量覆盖；宿主 app 目录，用于只读技能包挂载映射 |
| `OCTOP_DOCKER_SOCKET` / `OCTOP_DOCKER_GID` | 可由环境文件覆盖；Docker Engine socket 路径与实际组号 |
| `OCTOP_MEDIA_HTTP_ORIGINS` | 可由环境文件覆盖；管理员批准的 HTTP 生图 origin，逗号分隔，默认空；精确匹配域名和端口，无通配符。例如显式配置 `http://image-service.example.internal:3001` |
| `OCTOP_DEFAULT_TIMEZONE` | 可由环境文件覆盖；默认 Asia/Shanghai，用户日期和定时任务沿用服务器设置 |

迁移已有部署时，先核对原有环境变量、数据库和密钥与上表的对应关系；保留原数据与密钥，按下节执行升级，不运行 `prepare.py`。

## 升级与回退

企业镜像包含工作台使用的 Chromium 和系统库，浏览器安装目录为 `/opt/octop-browser`。构建会以 UID 1000 在断网条件下实际启动浏览器并截图，同时加载知识库向量依赖；服务就绪检查不能替代这项验证。可对候选镜像再次执行：

```sh
docker run --rm --network none --read-only --cap-drop ALL --tmpfs /tmp --tmpfs /data:uid=1000,gid=1000 \
  --entrypoint python octop-enterprise:enterprise-1.0.1b2 /usr/local/libexec/octop/verify_runtime.py
```

已有本地应用镜像和验证通过的运行环境时，可用 `Dockerfile.runtime-reuse` 离线组装。`APP_IMAGE` 必须包含完整当前应用、前端和 Python 环境；`RUNTIME_IMAGE` 必须包含可用的浏览器和系统库，两者系统基础层及 Python ABI 必须一致。不要仅凭应用健康检查选择运行环境。使用 `--pull=false --network=none`，并明确传入两个本地镜像：

```sh
docker build --pull=false --network=none -f docker/enterprise/Dockerfile.runtime-reuse \
  --build-arg APP_IMAGE=octop:current-application --build-arg RUNTIME_IMAGE=octop-enterprise:verified-runtime \
  -t octop-enterprise:next-candidate .
```

1. 保存旧 commit、镜像 ID、配置和四架构客户端发行清单，检查两库版本。主库 `SELECT version FROM _schema_version`；通信启动检查自己的迁移表，升级命令始终使用新通信镜像的 `--migrate`。
2. 停止接收新任务，等待本地任务上报结束或标记结果未知。未知结果不能自动重做。
3. 按下一节取得同一停止写入窗口的完整备份；保留员工电脑原项目。
4. 在新的空目录/临时数据库先恢复该备份并验证；从新提交重建应用、通信和 payload。
5. 停止应用/通信，用新通信镜像执行 `--migrate`，再启动两服务；Octop 启动执行主库增量迁移。检查 readiness、登录、附件、技能下载、设备撤销和任务恢复。
6. 仅应用回退：切回旧镜像前核实其是否支持当前 schema；禁用公司运行时不会删除公司表。数据库回退必须恢复与旧代码匹配的两库及文件快照，不能把旧应用直接连接到不兼容 schema，也不能覆盖升级后新增资料。将新增文件单独归档并人工对账。

## 完整备份与恢复

产品内“系统备份”包含主库和选中的宿主目录，公司技能缓存随主库一起归档。默认启用 `include_config`，会包含存在的 `config.json` 和应用数据目录下的 `env` 文件，其中可能有明文凭据；关闭此选项则不包含这两个文件。不包含 Docker 隔离卷、通信库/媒体、部署环境文件 `/etc/octop.env`、`env` 引用的外部密钥文件内容和员工本机项目。manifest 记录外部组件，隔离模式下不会把宿主空目录标为已备份的 workspace。旧备份保持可读。

运维备份入口：

```sh
sudo docker volume ls --filter label=octop.agent.id
sudo python3 docker/enterprise/backup.py --env /etc/octop.env --output /srv/backups/octop-20261013 \
  --volume octop_workspace_REPLACE_WITH_ACTUAL_24_HEX
```

每个属于本实例的工作区卷都必须逐一提供 `--volume`（可重复参数），不能只填一个示例。通过 `docker volume inspect` 和 `docker inspect` 核实卷的 `octop.agent.id` 标签及 worker 挂载关系。确实没有任何隔离工作区才使用 `--no-sandbox-volumes`。命令拒绝旧输出目录、无标签卷、驱动自定义选项卷和数据目录内部的输出路径；不会删除任何数据。

备份会暂时停止原先运行的应用、通信和指定卷关联的 worker，分别 `pg_dump -Fc` 两库，归档 app/media/secrets、部署环境和各卷，结束时恢复原先运行的组件。`manifest.json` 的 `complete` 必须为 true，并核对文件 SHA-256；`complete=false` 表示备份未完成，不能作为完整恢复的备份。部署环境和备份中包含密钥，离机保存时加密并保持仅管理员可读。外部数据库、外部工作区及员工电脑项目需按其系统另外备份。

恢复只在另一台干净主机或明确准备好的空实例中执行：

1. 从选定发行提交构建对应镜像；校验 manifest 每个文件的 SHA-256。不要使用 `complete=false` 的归档。
2. 还原 `deployment.env` 为私有环境文件；修改宿主路径、socket 和 GID，保留所有密码和签名密钥。安全解压 `files.tar.gz` 到新的数据根，保留 uid/gid 1000，**不要运行 prepare.py 重新生成密钥**。
3. 启动空 PostgreSQL，使初始化脚本创建两库及角色。停止应用与通信。分别运行：

   ```sh
   sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml exec -T postgres \
     pg_restore -U octop_bootstrap -d octop --single-transaction --exit-on-error --clean --if-exists < /srv/backups/SNAPSHOT/octop.dump
   sudo docker compose --env-file /etc/octop.env -f docker/enterprise/compose.yml exec -T postgres \
     pg_restore -U octop_bootstrap -d octop_im --single-transaction --exit-on-error --clean --if-exists < /srv/backups/SNAPSHOT/octop_im.dump
   ```

4. 按 manifest 创建**同名**命名卷，恢复原有 `octop.agent.id` 标签。使用同版企业镜像、root 用户、无网络，将对应 tar 解到卷根；保留文件 uid/gid。不要启动 worker 直到所有卷恢复完成。
5. 启动应用/通信，检查 readiness 和两库迁移状态，再验证真实附件、历史消息、技能下载、隔离文件和设备签名认证。记录恢复耗时、数据丢失窗口与验证结果，确认满足企业的恢复要求。

## 运维与容量

- `/api/health` 保留兼容的存活探针；`/api/health/ready` 检查主库查询、已启用通信 `/healthz`、Docker 可达和 sandbox 镜像存在，异常返回 503。它不能替代真正执行隔离工具的验收。
- 管理员访问 `/api/health/storage` 可查看任务/事件/检查点/写入/操作回执行数、序列化内容字符数（不是数据库物理字节数）、任务状态、技能缓存文件与字节、设备数量、身份投递积压条数与最大尝试次数。接口只读，不返回消息、路径或凭据。扫描较大的数据库可能耗时，不应作为每秒探针。
- 保留策略：任务、检查点、操作回执和技能哈希由历史恢复引用；当前不自动过期或按时间删除。仍在运行、取消请求中或结果未知的任务尤其禁止清理。删除员工/专家/线程沿用关联清理。需要容量回收时先确认引用、完成备份和恢复验收，再设计专用管理流程，禁止直接按 mtime 删除技能缓存。
- 根据业务保留周期与员工规模制定清理策略；先观察数据库增长、PG 锁等待、消息/文件/AI 并发延迟和内存占用，再通过负载测试确定配额。
- readiness 503：先查主库、通信迁移和 Docker socket 权限；技能读取失败检查 host data 路径与包目录权限。桌面进程退出显示安全异常类型与退出码，原始 stderr 不写日志，避免泄露提示词/路径/凭据。

## 定向验收与四架构交付

```sh
make check-enterprise ARGS='--section backend'
make check-enterprise ARGS='--section frontend'
make check-enterprise ARGS='--section desktop'
make check-enterprise ARGS='--section communication'
make check-enterprise ARGS='--section packaging'
```

真实数据库测试通过 `OCTOP_IM_TEST_DATABASE_URL` 和 `OCTOP_IM_TEST_OWNER` 指向**专用临时 PostgreSQL**；测试创建/删除带测试前缀的数据库。不要使用生产地址。不配置时有跳过项，不能视作真实 PG 验收。

企业 payload 先用 `desktop/portable/package.sh` 从同一源码构建当前架构，再准备同架构 Python、ffmpeg/ffprobe 和办公依赖。目标机器用包内解释器运行 `verify_company_runtime.py`。`package-company.py` 核对 Octop METADATA 版本与当前源码全部 Python 文件摘要，写入 shell/Octop/Harness/两个协议/commit/dirty/source SHA-256，拒绝旧版 payload，即使其版本号碰巧相同。生成登录资源的步骤在共享 Wails 构建中执行，Go embed 也要求 JS/CSS 同时存在。

四架构解包后可用 `make check-enterprise ARGS='--section packaging --manifest-dir /path/to/unpacked'` 检查版本矩阵。支持的构建目标是 macOS arm64/amd64 和 Windows amd64/arm64；每次发行应分别记录构建与 manifest、原生工具烟测、安装/升级/卸载、签名/公证、登录/断线/撤销的结果和日期。矩阵检查与包内目标烟测不等于真实安装或签名验收，具体状态以对应发行记录为准。

员工操作与功能范围见[企业客户端指南](../../docs/enterprise-client.md)。
