# Octop Ent 源码发行说明

对外名称 **Octop Ent**，独立仓库为 [colorAi/Octop-Enterprise](https://github.com/colorAi/Octop-Enterprise)，当前为私库。主项目及自有通信模块采用 MIT；首次独立发行版本为 `1.0.0`，使用本分支自己的版本序列，晚于此前开发版本 `1.0.3b1`。

当前仅提供源码，不承诺已有预构建服务端镜像或 Windows/macOS 安装包。源码快照不等于已构建、签名或完成全平台验收的二进制发行。正式发行材料统一放在[本仓库 Releases](https://github.com/colorAi/Octop-Enterprise/releases)，访问受仓库权限限制；员工向管理员获取匹配的企业客户端。

## 源码范围

- 保留中文首页、英文介绍、员工指南、贡献说明、安全政策及部署和构建说明。
- 根目录及通信服务保留原始 MIT 许可和贡献者声明。第三方来源与分发范围见 [NOTICE](../NOTICE.md)。
- 在线更新 CLI/API、上游一键安装脚本及 `make publish` / `publish-test` 已停用，避免上游同名包覆盖本分支。
- 公开副本排除 Git 历史、本地工具配置、运行环境文件、浏览器会话、部署记录和构建产物。
- `office-automation` 专家及其头像不纳入源码发行，因为其随附文档技能限制再分发。测试和示例使用公开包含的专家；独立文档处理库和本机工具不受此排除影响。
- 排除未作为当前文档维护的生成文件 `docs/user-guide.html` 与上游个人版介绍 `docs/octop-introduction.html`；员工快速上手以 [Markdown 版](user-guide.md)为准。
- FnOS 打包暂不随 `1.0.0` 源码发行；排除 `fnos/`、`scripts/build-fpk.sh`、`scripts/fnos/` 及对应测试，待独立镜像和发布渠道准备好后再恢复。
- 旧 `scripts/release_download_links.py` 及其测试不随本次源码发行；其预设资产表属于上游发行，不用于本项目尚未提供的二进制下载。
- 上游工作流移到 `.github/upstream-workflows/` 并改为 `.disabled`，不会执行发布或分支同步。启用前须调整仓库、包名、镜像、权限与签名配置。

## 导出与验证

在开发仓库执行，输出必须是仓库外尚不存在的新目录：

```sh
python3 tools/export_public_source.py --output /absolute/new-directory/octop-ent
python3 tools/check_public_source.py --root /absolute/new-directory/octop-ent
```

导出工具以 Git 跟踪和未忽略文件为候选，复制当前工作区内容，包括未提交修复。它不会修改 Git 索引、分支或 remote，不会提交、打标签或推送。修复后始终导出到新目录，保留此前快照。

`PUBLIC_SOURCE.json` 如实记录源 HEAD、`source_worktree_dirty`、导出范围和逐文件 SHA-256；dirty 为 true 时，源 HEAD 只是开发基线，不能代表完整快照。此时以文件清单和源码包 SHA-256 识别候选快照，不宣称它是干净提交的正式发行。正式冻结须由维护者另行安排提交与标签，再从对应干净工作树导出；本轮整理不自动执行这些操作，也不自动升级版本。

校验工具检查清单摘要、主要文档的本地链接、锚点与图片、专家提示文件和命令示例指向的专家，并拦截上游镜像、下载入口和打包身份回流。归档为 inactive 的上游工作流、来源致谢与带有效入口阻断的旧安装器不作为可用分发入口。符号链接只能指向发行范围内的相对路径，摘要针对链接文本。内置凭据模式筛查覆盖有限，不等同于完整安全或许可审计。

## 构建前提

服务端支持目标是 Linux + Docker Engine + Compose，命令见[企业部署与恢复](../docker/enterprise/README.md)。构建网络需要能获取基础镜像与依赖；当前不提供完整离线服务器安装包。

客户端构建见[桌面说明](../desktop/README.md)。企业员工安装包面向 Windows amd64/arm64 与 macOS 13+ universal；具体支持状态以发行验证记录为准。`desktop/package-company.py` 读取 Git HEAD 和 dirty 状态，要求已有可解析 `HEAD` 的本地仓库。源码包不含 `.git`，需先建立源码提交，或使用对应发行的 Git 检出；无需推送。服务端 Docker 构建没有这一 Git 前提。

登录 bundle、`company-dashboard.zip` 和生成清单不随源码包分发。桌面完整打包器会从源码构建这些资源；单独构建桌面壳时，先按桌面文档生成资源。

目录授权使用通用字段 `allow_company`。设备协议版本保持 2；服务端同时接受旧字段，字段冲突时拒绝授权。桌面客户端在原有 v2 WebSocket 上继续发送兼容字段，以兼容旧服务端；原生界面使用通用字段并能读取旧客户端结果。历史拒绝原因字符串仍保留兼容拼写，避免破坏旧日志和客户端。

## 仓库与安全入口

- 问题与建议：[本仓库 Issues](https://github.com/colorAi/Octop-Enterprise/issues)。
- 安全报告：按 [SECURITY](../SECURITY.md) 获取私密渠道，不在 Issue 中提交漏洞细节。当前私库未验证 **Report a vulnerability** 可用；公开前应核验所提供的私密入口。
- 贡献分支：工作分支向本仓库 `main` 提交 PR，见[贡献说明](../CONTRIBUTING.md)。
- 首次独立 Git 历史应从清理后的源码快照开始；来源和上游基线由 NOTICE 与 PUBLIC_SOURCE 保留，避免把排除材料重新带入仓库。不要向上游仓库推送本项目发行。

## 首次正式发行清单

- 对应提交或候选快照身份、源码包与 SHA-256，目录与压缩包逐文件一致。
- 版本、Harness 版本、主库和通信库 schema、设备与公司运行时协议要求。
- 构建平台和架构、依赖版本、构建命令、服务端部署和客户端安装说明。
- 实际验证的平台与架构，以及未验证的安装、签名、公证、容量、升级和恢复项目。
- 已知限制、数据边界、升级前备份和回退步骤；计划中的群机器人等能力不列为已交付。
- LICENSE、NOTICE、通信服务及第三方许可。后续分发安装包时，按实际组件附上 FFmpeg、WebView2、Python 等许可，源码包检查不能替代二进制分发检查。
- 若提供镜像或安装包，一并公布唯一标签/digest、架构、SHA-256、签名和公证状态，不复用同一正式标签覆盖不同源码。

桌面构建标识已独立为 `io.github.colorai.octop.enterprise`；Windows 产品名为 Octop Ent，使用独立的安装目录及卸载项。Docker 镜像元数据指向本仓库。安装身份变化不等于已有安装数据已迁移，旧包升级前须保留员工工作目录、账号配置与任务回执，不宣称可自动迁移。独立在线更新源、独立镜像命名空间与签名主体仍须在对应二进制发行前确定。源码导入路径、CLI 名称、环境变量和数据库名保持现有兼容设置。
