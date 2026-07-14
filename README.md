# PT ReleaseKit

PT ReleaseKit（原 PT-BDtool）用于扫描视频、音频、`BDMV` 和 `ISO`，生成截图、MediaInfo、频谱图或 BDInfo，并把结果打包。

> 更名兼容说明：GitHub 仓库现使用 `PT-ReleaseKit`。现有 `bdtool`、`ptbd-*` 命令、`pt-bdtool:*` Docker 镜像标签、配置目录以及 `PT-BDtool-*` 下载文件名继续保留，已有安装和自动化脚本无需迁移。

当前架构是 **Python 模块化核心优先 + Shell 兼容层 + Windows/macOS 桌面 GUI + Docker VPS 本机处理**。Docker 是新增部署方式，不会替代现有桌面控制端。

## 处理结果

- 视频：`mediainfo.txt`、`1.png` 至 `6.png`
- 音频：`mediainfo.txt`、`频谱图.png`
- `BDMV` / `ISO`：`BDInfo.txt`、`1.png` 至 `6.png`
- 最终包：通常为 `.zip`，不可用时回退为 `.tar.gz`

## 选择运行方式

### Windows / macOS 桌面 GUI

桌面端继续保留，适合从个人电脑连接媒体 VPS，执行扫描、生成、回传和清理。

从 [`portable-latest`](https://github.com/My15sir/PT-ReleaseKit/releases/tag/portable-latest) 下载：

- Windows：`PT-BDtool-windows-portable.zip`，解压后运行 `PT-BDtool.exe`
- macOS：`PT-BDtool-macos-portable.zip`，解压后运行 `PT-BDtool.app`
- Linux：`PT-BDtool-linux-portable.tar.gz`

源码入口仍然可用：

- Windows：`PT-BDtool.bat`
- macOS：`PT-BDtool.command`
- Linux：`PT-BDtool.sh`
- 通用 GUI：`ptbd-gui`

源码运行 GUI 至少需要 Python 3 和 Tk。连接诊断和内置远端后端需要 Paramiko；缺少 Paramiko 时，扫描和处理会回退到系统 Bash/SSH。发布包由 PyInstaller 构建并包含控制端依赖，不要求普通用户直接运行源码。

首次连接一台新 VPS 前，请先在系统终端执行 `ssh -p 端口 用户@主机`，通过可信渠道核对服务器显示的主机密钥指纹后再接受。桌面端会读取 `~/.ssh/known_hosts`，并拒绝未知或发生变化的主机密钥，避免把 SSH 密码发送给未经确认的服务器。

首次使用时填写 VPS 地址（如 `root@host`）、SSH 端口、密码或已有密钥，以及本机保存目录。正常顺序是保存配置、扫描候选、选择条目、远端生成、下载归档；开启自动清理只会删除本次远端生成目录和临时包，不会删除原始媒体。

桌面配置和日志位置：

- Linux：`~/.config/ptbd-gui/config.json` 与同目录的 `PT-BDtool.log`
- Windows 便携版：程序旁的 `PT-BDtool-config.json`
- macOS 便携版：`PT-BDtool.app` 同级目录的 `PT-BDtool-config.json`
- Shell 远端模式：`~/.config/ptbd-remote/config.env`

扫描或处理失败时，先检查系统终端能否直接 SSH 登录、VPS 账号是否能读取媒体目录，以及日志中是否有依赖或软件源错误。远端自动安装优先支持 Debian、Ubuntu 和 Alpine；其他发行版可能需要手动准备依赖。

VPS 禁用 SFTP 子系统时，内置后端会自动回退到 SSH 管道上传和下载，不需要为此开放额外端口。

### Docker：媒体 VPS 本机处理

Docker 应部署在**媒体文件所在的 VPS**。容器直接读取该 VPS 上的媒体目录并把结果写回宿主机，不再通过 SSH 把媒体搬到另一个处理节点。

```bash
export PTBD_UID=1000
export PTBD_GID=1000
sudo mkdir -p /srv/media /srv/ptbd/output /srv/ptbd/config
sudo chown "$PTBD_UID:$PTBD_GID" /srv/ptbd/output /srv/ptbd/config

PTBD_MEDIA_DIR=/srv/media \
PTBD_OUTPUT_DIR=/srv/ptbd/output \
PTBD_CONFIG_DIR=/srv/ptbd/config \
docker compose up -d --build
```

Compose 默认只绑定 VPS 回环地址。先在桌面电脑建立 SSH 隧道：

```bash
ssh -L 8899:127.0.0.1:8899 user@VPS-IP
```

然后在桌面浏览器访问：

```text
http://127.0.0.1:8899/
```

挂载规则：

- `PTBD_MEDIA_DIR` → `/media`，只读
- `PTBD_OUTPUT_DIR` → `/output`，可写，保存生成结果
- `PTBD_CONFIG_DIR` → `/config`，可写，保存配置与运行状态
- `PTBD_UID` / `PTBD_GID`，容器进程使用的非 root 宿主用户 ID，默认 `1000:1000`
- `PTBD_WEB_PORT`，宿主回环 Web 端口，默认 `8899`

容器默认以非 root 用户运行、删除全部 Linux capabilities，并启用 `no-new-privileges`。输出和配置目录必须允许配置的 UID/GID 写入。

完整的部署、升级、反向代理和排障说明见 [`docs/DOCKER.md`](docs/DOCKER.md)。

### Linux / VPS 命令行

直接处理一个媒体文件或目录：

```bash
./bdtool /path/to/movie.mkv --out /path/to/output
```

扫描并输出 JSON：

```bash
./bdtool scan-json --dir /path/to/media
```

检查依赖：

```bash
./bdtool doctor
./bdtool status
```

Python 核心需要 `python3`、`ffmpeg`、`ffprobe` 和 `mediainfo`；处理蓝光时建议提供 `BDInfo`。安装脚本和 Docker 镜像会准备相应运行环境。

单曲频谱不要求额外 Python 图像模块；只有“组合频谱”需要 NumPy 和 Pillow。远端自动安装会按所选模式补齐它们，Docker 镜像则已内置。

仍保留的其他入口：`./ptbd` 是新手流程，`./ptbd-start.sh` 是本地菜单，`./ptbd-remote.sh` 是直接使用 Shell 控制远端 VPS 的兼容流程。

## Python-first 与 Shell 兼容

`bdtool` 是稳定入口，默认把处理命令交给 `python3 -m ptbd_core.cli`。Python 包按职责拆分：

- `scanner.py`：媒体发现与类型识别
- `media_tools.py`：外部媒体工具调用
- `artifacts.py`：截图、MediaInfo、频谱图和 BDInfo 产物
- `pipeline.py`：处理流程编排
- `returns.py`：打包与回传
- `config.py`、`models.py`、`jobs.py`：共享配置、模型和任务状态

为避免破坏旧用户流程，以下情况仍进入 `bdtool-legacy.sh`：

- `bdtool` 不带参数时打开旧交互菜单
- 使用 `start`、`install`、`--lang` 或 `--non-interactive` 等旧菜单参数
- 显式设置 `PTBD_PYTHON_CORE=0`
- 找不到 Python 3 时自动回退

需要直接运行兼容实现时：

```bash
./bdtool-legacy.sh
```

Shell 层承担兼容、旧菜单，以及 GUI/Web 缺少 Paramiko 时的远端 fallback；新处理逻辑应优先放入 `ptbd_core/`。

## 桌面 GUI 使用流程

桌面端分为“工作台”和“连接设置”两个页签。工作台固定显示四步进度、候选列表、任务状态和实时日志；连接参数单独放在设置页，避免展开配置后把扫描区和日志挤出窗口。

首次启动会直接打开“连接设置”，先填这些：

- `VPS 地址`，例如 `root@1.2.3.4`
- `SSH 端口`，一般默认 `22`
- `SSH 密码`，如果你走密钥可以留空
- `本机保存目录`

其他项怎么理解：

- `空白 VPS 自动准备运行环境`
  - 开着更省心
  - 程序会先尝试远端自动装依赖
  - 还不够时才回退上传运行包
- `额外扫描目录`
  - 不懂就留空
  - 留空时默认优先扫描：`/home /root /data /mnt /media /srv`
  - 显式白名单优先于全盘扫描开关
  - 多个根目录可用空格或逗号分隔；含空格、逗号或撇号的单个路径请使用双引号，例如 `"/data/PT Movies" "/mnt/O'Brien, Archive"`
- `启用全盘扫描（高级）`
  - 默认关闭
  - 只有媒体不在常见目录时再打开
- `成功后自动清理 VPS 生成目录`
  - 建议开启
  - 只清这次生成的结果目录和结果包，不删原始媒体文件

## 主流程真实顺序

正常使用顺序就是：

1. 保存配置
2. 建议先点“测试连接”，确认 SSH 和依赖状态
3. 返回工作台，点“扫描 VPS”
4. 等程序连上 VPS
5. 等程序检测远端系统和依赖
6. 在候选列表勾选条目后点“生成所选”
7. 等程序在 VPS 上生成截图和媒体信息
8. 等程序把结果包下载回本机，并在日志看到明确成功/失败汇总
9. 如有失败项，可点“重试失败”
10. 完成后可直接点“打开结果目录”；如果开启了自动清理，程序会清理 VPS 上这次生成的临时目录

## 文件会保存到哪里

### 结果包

优先保存到你在 GUI 里填的“本机保存目录”。

如果你走的是 shell 远端流程：

- 默认优先尝试桌面
- 桌面不可用时会回退到 `~/PT-BDtool-downloads`

下载回来的通常是：

- `.zip`
- `.tar.gz`

也就是说，它默认下载的是结果包，不是自动帮你解压成一堆散文件。

### 配置文件

真实位置分三类：

- GUI 配置
  - Linux: `~/.config/ptbd-gui/config.json`
  - Windows 便携版: 程序旁边的 `PT-BDtool-config.json`
  - macOS 便携版: `PT-BDtool.app` 同级目录的 `PT-BDtool-config.json`
- `ptbd` 新手模式配置
  - `~/.config/ptbd/config.env`
- shell 远端模式配置
  - `~/.config/ptbd-remote/config.env`

### 日志文件

- GUI 日志通常在 GUI 配置目录旁边的 `PT-BDtool.log`
- CLI 日志通常在项目或安装目录下的 `bdtool-output/logs/`

## 自动清理到底会清什么

默认会清：

- 这次生成的临时输出目录
- 这次待下载的结果包

默认不会清：

- 你原始的视频文件
- 你原始的音频文件
- 你原始的 `BDMV`
- 你原始的 `ISO`

## 常见报错怎么排查

### 1. 扫描失败 / 获取候选失败

先看这几项：

1. VPS 地址、端口、密码有没有填错
2. 这个账号能不能 SSH 登录
3. VPS 网络是不是正常
4. VPS 软件源是不是可用
5. 日志里是不是提示缺依赖或 SSH 失败

GUI 里可以直接点“打开日志文件”。

## Web 模式

源码可启动本机 Web 控制端：

```bash
./ptbd-web --host 127.0.0.1 --port 8899 --open
```

Web 控制端支持两种模式：

- `remote`：桌面或控制端通过 SSH 操作远端 VPS
- `local`：直接扫描当前主机目录；Docker 默认并应保持此模式，扫描根目录使用 `/media`

Web 和 GUI 都支持连接检查、批量逐项处理、成功/失败汇总及失败项重试。Web 任务可能结束为 `success`、`partial`、`error` 或 `cancelled`；页面刷新后会自动恢复当前运行任务的日志和停止操作。Docker local 模式只显示本机媒体与输出路径，不显示无关的 SSH 字段。

直接在 Linux 主机运行 local 模式：

```bash
PTBD_WEB_MODE=local \
PTBD_WEB_LOCAL_ROOT=/srv/media \
./ptbd-web --host 127.0.0.1 --port 8899
```

启动后在 Web 配置中把保存目录设为 `/srv/ptbd/output`。

## 安装源码版本

```bash
bash install.sh --offline --no-launch
export PATH="$HOME/.local/bin:$PATH"
hash -r
```

安装后可验证：

```bash
bdtool --version
bdtool doctor
ptbd-gui --self-check
```

`install.sh` 不通过系统包管理器修改宿主机；离线 bundle 可由 `scripts/ensure-bundle.py` 准备。官方 bundle 优先校验 Release 的 `.sha256` sidecar；旧版官方资产使用仓库内固定摘要完成首次迁移。

使用自定义 bundle 镜像时可配置：

- `PTBD_BUNDLE_URL`：自定义归档地址
- `PTBD_BUNDLE_SHA256`：可信 SHA256 摘要，优先级最高
- `PTBD_BUNDLE_CHECKSUM_URL`：自定义 sidecar 地址，默认是归档地址加 `.sha256`
- `PTBD_BUNDLE_ALLOW_UNVERIFIED=1`：仅在明确接受风险时允许“校验地址不可用”的自定义下载；默认关闭，格式损坏的 sidecar 仍会失败

## 开发验证

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q ptbd_core ptbd-gui.py ptbd-web.py ptbd_remote_backend.py
xvfb-run -a python3 ptbd-gui.py --ui-smoke-check
./scripts/full-test.sh
docker build -t pt-bdtool:local .
```

更多维护信息：

- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
- [`docs/REPO-INDEX.md`](docs/REPO-INDEX.md)
- [`docs/README.en.md`](docs/README.en.md)
- [`docs/DOCKER.md`](docs/DOCKER.md)
