# Octop Ent Docker 部署

企业模式从本仓库构建，使用 [enterprise/README.md](enterprise/README.md) 的首次安装、升级和恢复步骤。它包含 PostgreSQL、独立通信服务、设备通道和员工工作区隔离。

`Dockerfile` 构建基础应用镜像，`enterprise/compose.yml` 组合企业服务。其他 Compose 文件保留为基础模式/开发参考，不代表完整企业部署。不要使用腾讯上游的预构建镜像代替本项目。
