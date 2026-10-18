# Octop Ent 脚本

企业部署以[部署与恢复指南](../docker/enterprise/README.md)为入口。开发依赖用 `uv sync --locked` 和 `npm --prefix dashboard ci` 准备。

上游 `install.sh`、`install-octop.sh`、`install.ps1` 和 `install.bat` 原本默认安装腾讯的 PyPI 包/仓库，已在入口停用，避免覆盖本版本。独立安装源准备好后再适配，当前不提供远程一键安装。

FnOS 打包目录、构建脚本及旧 Release 下载表生成脚本暂不随 `1.0.0` 源码发行，待独立镜像、发布渠道和实际二进制资产准备好后再恢复。

其余脚本的用途以各文件说明为准。桌面构建见[桌面说明](../desktop/README.md)，公开源码导出见[发布准备](../docs/open-source-release.md)。不要直接执行上游发布脚本向第三方仓库推送或发布。
