# Octop Enterprise 客户端构建

本指南对应源码版本 **1.0.1b3**。企业安装包包含桌面程序、登录页、完整界面和本机工具环境。员工安装后填写企业服务器地址，无需自行安装 Python。

服务端部署见[企业部署与恢复](../docker/enterprise/README.md)，员工操作见[企业客户端指南](enterprise-client.md)。英文及桌面开发细节见[桌面说明](../desktop/README.md)。

## 构建入口与前提

| 用途 | 入口 | 自动完成的内容 |
| --- | --- | --- |
| 企业员工安装包 | `desktop/package-company.py` | 校验准备好的工具环境，构建登录页及完整界面，编译原生程序，生成安装包和 SHA-256 清单 |
| 便携 Python 与基础依赖 | `desktop/portable/bootstrap-runtime.sh`、`desktop/portable/package.sh` | 下载对应架构的 Python，按锁文件安装基础依赖和当前应用 |
| 企业工具验证 | `desktop/portable/verify_company_runtime.py` | 在目标系统上用包内 Python 验证办公、图片、媒体和终端工具 |
| 本地开发 | `desktop/package-dev.sh` | 启动开发后端和桌面窗口，不生成完整离线企业安装包 |

当前企业打包流程以 **macOS 构建机**为基础，可以生成 Mac 通用包和 Windows x64、ARM64 安装包。准备以下工具：

- Python 3.11+ 用于打包脚本，`uv` 用于 Python 依赖和 wheel 构建；应用工具环境使用 Python 3.12。
- Node.js/npm、Go 1.25+、Git、固定版本的 Wails CLI。
- Mac 包需要 Xcode Command Line Tools；Windows 安装包需要 NSIS 的 `makensis`。
- 构建网络需能下载 npm、Go、Python 依赖；离线构建需提前缓存全部材料。

在源码根目录准备界面依赖和 Wails：

```sh
npm ci --prefix dashboard
go install github.com/wailsapp/wails/v3/cmd/wails3@v3.0.0-beta.13
```

确保 `wails3` 在 PATH 中，或用打包参数 `--wails /absolute/path/to/wails3` 指定。Mac Developer ID 签名可选；公证与 Windows Authenticode 签名是另外的发行步骤。

## 下载源码包后的 Git 准备

打包器会读取 Git HEAD 记录源码身份。Git 检出须已有提交；下载的源码压缩包不含 `.git`，可在解压后的源码根目录建立本地提交：

```sh
git init
git add .
git commit -m "Import Octop Enterprise 1.0.1b3 source"
```

Git 用户名和邮箱使用构建者自己的配置；不需要推送。保存原源码包校验值和 `PUBLIC_SOURCE.json`，便于追溯。服务端 Docker 构建无需这一步。

## 准备离线工具环境

**打包器接收已经准备好的环境，不会自动补齐企业工具依赖。** 源码包不包含 Python 二进制、依赖缓存、FFmpeg 或 WebView2 安装器。基础便携包的端到端脚本 `desktop/package-release.sh` 也不能代替企业打包流程。

可以先用便携构建脚本生成基础材料。例如在仓库根目录执行以下命令，目标可换为 `darwin-arm64`、`darwin-amd64`、`windows-amd64` 或 `windows-arm64`：

```sh
make build-frontend
bash desktop/portable/bootstrap-runtime.sh windows-amd64
bash desktop/portable/package.sh windows-amd64
```

基础目录生成在 `desktop/portable/release/Octop-windows-amd64/`。在独立的构建输入目录中补齐企业所需材料，保持以下布局：

```text
runtime-inputs/
  windows-amd64/
    runtime/python.exe
    packages/                 # 当前 Octop、Harness 及对应架构的依赖
    launch.py
    tools/ffmpeg.exe
    tools/ffprobe.exe
  windows-arm64/              # 相同布局，全部原生组件须为 ARM64
  darwin-arm64/               # Python 入口为 runtime/bin/python3
  darwin-amd64/               # 工具名为 tools/ffmpeg、tools/ffprobe
```

`packages/` 必须包含同一份当前源码构建的 Octop、Harness 1.0.3，以及 pandas、openpyxl、python-docx、python-pptx、pypdf、Pillow 等依赖。依赖依据 `uv.lock` 和便携脚本的架构覆盖规则准备，额外工具依赖记录版本与来源。FFmpeg/FFprobe 需准备对应架构的可分发二进制及随附材料。

跨架构构建缺少兼容 wheel 时，需要在目标环境编译或准备已验证的对应架构 wheel，尤其是 Windows ARM64；不能复制 x64 的 `.pyd` 或 `.dll`。基础脚本可能在这一步停止，需要构建者处理。打包器会校验 Python、FFmpeg/FFprobe 的架构，以及 Octop 版本和全部 Python 源码摘要。

在对应 Windows 目标环境中运行工具验证，例如 PowerShell：

```powershell
& 'C:\build\runtime-inputs\windows-amd64\runtime\python.exe' `
  '.\desktop\portable\verify_company_runtime.py' `
  'C:\build\runtime-inputs\windows-amd64'
```

Mac 使用对应目录里的 `runtime/bin/python3` 执行同一脚本。成功后生成 `company-runtime-build.json`；验证后再修改文件会使记录失效。跨平台打包可以暂缺此记录，产物会明确记为未完成目标运行验证，不得复制旧记录作为本次验证结果。

## 生成 Windows 安装包

准备完整的 `windows-amd64/`、`windows-arm64/` 两份工具环境。将微软完整离线 WebView2 安装器放入单独目录，文件名分别为：

- `MicrosoftEdgeWebView2RuntimeInstallerX64.exe`
- `MicrosoftEdgeWebView2RuntimeInstallerARM64.exe`

使用完整离线安装器，下载引导程序不能代替。在 macOS 构建机的源码根目录执行：

```sh
python3 desktop/package-company.py --platform windows \
  --output /absolute/fresh-build/client \
  --runtime-dir /absolute/runtime-inputs \
  --webview2-dir /absolute/webview2
```

输出 `Octop-company-windows-amd64-1.0.1b3.exe`、`Octop-company-windows-arm64-1.0.1b3.exe`、`SHA256SUMS.txt` 和 `company-package-status.json`。x64 包供常见 Intel/AMD Windows 电脑使用，ARM64 包供 Windows ARM 电脑使用。

## 生成 Mac 安装包

准备 `darwin-arm64/`、`darwin-amd64/` 两份工具环境，生成支持 macOS 13+ 的通用包：

```sh
python3 desktop/package-company.py --platform darwin \
  --output /absolute/fresh-mac-build/client \
  --runtime-dir /absolute/runtime-inputs \
  --webview2-dir /absolute/webview2
```

当前命令行仍要求提供 `--webview2-dir` 参数，选择 `darwin` 时不读取其中的 Windows 安装器。需要签名时追加 `--sign-identity 'Developer ID Application: Your Name (TEAMID)'`。

`--platform all` 同时构建上述三个安装包，需要四份工具环境。每次构建使用新的输出父目录，打包器在其旁创建 `build-company/`；已有同名暂存目录时会停止，避免混入旧产物。

## 校验与交付

确认安装包校验值与 `company-package-status.json`，并在目标 Windows/Mac 上验证安装、登录、项目目录、本机任务、群聊和卸载保留数据。成功编译、安装包解包检查和目标运行验证分别记录。

四份工具 ZIP 解压后，可检查完整版本矩阵：

```sh
make check-enterprise ARGS='--section packaging --manifest-dir /absolute/unpacked-payloads'
```

该矩阵入口要求四个目标全部存在；单个平台的打包校验由打包器完成。`--legacy-shell-only` 不包含本机执行环境，不能作为完整企业客户端交付。

企业地址由员工登录时填写。界面内置在客户端中，服务器的“更新配置”通知不会下载新程序；界面或本机工具更新后，需要重新分发安装包。
