# 参与 Octop Enterprise

[English](CONTRIBUTING_EN.md)

欢迎提交问题、文档改进和代码。Octop Enterprise 独立维护企业场景改造；问题和 PR 请提交到[本仓库](https://github.com/colorAi/Octop-Enterprise)。向 TencentCloud/Octop 回馈通用修复时，请遵循上游自己的贡献流程。

## 开发准备

需要 Python 3.12+、uv。日常开发与 Docker 构建使用 Node.js 20（见 `mise.toml`）；桌面便携包本地重建脚本当前使用 Node.js 24。通信服务的 Go 版本见 `services/communication/go.mod`；桌面构建工具见 `desktop/README.md`（English）。

```sh
uv sync --locked
npm --prefix dashboard ci
make build-frontend
```

企业模式依赖 PostgreSQL、通信服务和 Docker 隔离，启动方法见[企业部署指南](docker/enterprise/README.md)。开发和测试使用独立配置与临时数据，不能连接正式业务库。

## 修改和验证

- 从本仓库 `main` 建立工作分支，向本仓库 `main` 提交 PR。
- Python 代码位于 `src/octop/`，前端源代码位于 `dashboard/`；不要直接编辑 `src/octop/dashboard/` 构建产物。
- 只修改与问题相关的部分，说明原因、实际行为和验证范围。
- 运行受影响模块及关联测试；非必要不做全量检查。Python 测试使用 `uv run pytest`。
- 修改前端后运行相关 Vitest 测试、TypeScript 类型检查及构建；修改通信服务后运行相关 Go 测试。
- 数据库迁移、权限、文件写入和任务恢复需要覆盖升级兼容与失败路径。

示例命令，按实际修改选择目标：

```sh
uv run pytest tests/unit/communication/test_api.py -q
cd services/communication
go test ./internal/groups ./internal/store
```

真实 PostgreSQL 测试使用专用测试数据库，配置要求见通信服务文档。没有配置而跳过的测试不能标记为通过真实数据库验收。

## 许可与发布

只贡献有权提供的代码和资料。自有改动按根目录 MIT 许可提交；引入第三方内容须保留许可和来源，不能把限制再分发的材料放入公开源码包。

提交 PR 时说明修改内容和验证结果。版本、标签与正式发行由维护者统一管理；源码导出与发行要求见[源码发行说明](docs/open-source-release.md)。
