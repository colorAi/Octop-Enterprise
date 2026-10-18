# 来源与第三方许可

## Octop

Octop Ent 基于 [TencentCloud/Octop](https://github.com/TencentCloud/Octop) 二次开发，保留上游 `Copyright (c) 2026 Octop` 及 MIT 许可正文。企业桌面本机执行、企业服务适配、内部通信等改造由 Octop Ent 贡献者维护；项目不代表腾讯官方企业发行。

本地分支从上游 `eb280112`（1.0.2b6）之后继续开发，并选择性移植了截至 `f79015b078c4d5d4c309a1bf6f3cd39ac06cc18f` 的部分上游变更。公开导出的 `PUBLIC_SOURCE.json` 记录本地源提交、工作树状态和逐文件摘要，不代表已完整合并该上游提交。

## 自有通信服务

`services/communication/` 从项目权利人自行开发的 Miaox 通信代码提取并持续改造。本次由权利人明确选择与主项目一并开源，采用目录内的 [MIT License](services/communication/LICENSE)。此前来源清单未附独立许可；现有开源授权以随包许可证为准。

[SOURCE.json](services/communication/SOURCE.json) 中的提交和摘要记录首次提取时的来源，不保证等于后续已修改文件的当前摘要。绝对本机路径和与通信无关的工作树清单已从公开来源记录中移除。

## 第三方组件

根目录 MIT 许可不替代第三方许可证。以下是主要来源及随附许可的索引，不是完整依赖 SBOM：

| 内容 | 来源或许可位置 |
| --- | --- |
| Octop Harness、Gateway、Memory、Browser | 各依赖包的元数据、许可证及对应 TencentCloud 上游仓库 |
| 工程专家与设计专家 | 各专家目录内的 `LICENSE`（Apache-2.0）；保留随附来源声明 |
| SheetJS CE 0.20.3 | `dashboard/vendor/README.md`；tarball 内的 Apache-2.0 许可与第三方声明 |
| Python、Node.js、Go 与各语言依赖 | 各运行时和依赖包随附的许可；版本由 uv.lock、package-lock.json、go.sum 记录 |
| Wails、系统 WebView 及其他桌面依赖 | 各项目的许可与分发要求 |

制作二进制、Docker 镜像或客户端安装包时，还要附上实际携带组件的许可证。FFmpeg 的许可取决于具体构建选项；WebView2、Python 和其他运行时不能仅用本项目 MIT 标签替代各自分发要求。

## 未纳入公开导出的材料

本地 `office-automation` 专家中的 docx、pptx、pdf、xlsx 技能附有 Anthropic 限制性许可，明确限制复制、衍生和再分发。公开导出工具排除整个该专家目录，避免留下依赖缺失技能的专家介绍；未在公开包中将其改标为 MIT。

这不影响本项目基于 python-docx、python-pptx、pypdf、openpyxl 等独立依赖实现的文档工具。上述第三方材料的本地副本保持原样；若以后取得明确分发授权或完成独立替代，再评估是否纳入公开发行。
