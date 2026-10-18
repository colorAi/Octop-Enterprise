# 安全问题报告

Octop Enterprise 是独立维护的 Octop 衍生项目。请向本项目维护者报告与本项目有关的安全问题。

## 私密报告

不要在公开 Issue 中提交密钥、漏洞利用细节、真实员工资料或可识别的业务数据。

若[本仓库安全页面](https://github.com/colorAi/Octop-Enterprise/security)提供 **Report a vulnerability**，通过该入口私密提交。入口不可用时，可通过已有的维护者联系方式请求私密渠道，或在[本仓库 Issues](https://github.com/colorAi/Octop-Enterprise/issues)提交仅请求联系的消息；收到私密渠道后再发送漏洞详情。

本项目未设定固定响应或修复时限，支持版本和维护范围以本项目的发行说明为准。

## 部署边界

- 企业控制面持有模型凭据及 Docker 管理能力，仅供受信任管理员维护。
- 员工本机终端和 Python 使用其系统账号权限，工作目录不等同于沙箱。
- 保管管理员密码、会话凭据、数据库密码和通信签名密钥，正式入口使用 HTTPS。
- 备份可能包含凭据、会话和业务文件，需要访问控制及离机加密。
- 自托管不等于推理均在内网，模型和外部服务由部署方配置。

详见[企业部署](docker/enterprise/README.md)和[员工隔离](docs/employee-isolation.md)。
