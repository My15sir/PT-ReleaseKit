# PT ReleaseKit

PT ReleaseKit（原 PT-BDtool）用于扫描视频、音频、`BDMV` 和 `ISO`，生成截图、MediaInfo、频谱图或 BDInfo，并把结果打包。

> 更名兼容说明：GitHub 仓库现使用 `PT-ReleaseKit`。现有 `bdtool`、`ptbd-*` 命令、`PTBD_*` 环境变量、`pt-bdtool:*` Docker 镜像标签、安装路径、配置目录和旧版 `PT-BDtool.*` 源码启动器继续兼容；新 Release 归档与打包程序统一使用 `PT-ReleaseKit` 文件名。

当前架构是 **Python 模块化核心优先 + Shell 兼容层 + Windows/macOS/Linux 桌面 GUI（本机或远端）+ Docker VPS 本机处理**。Docker 是新增部署方式，不会替代现有桌面控制端。

## 处理结果

- 视频：`mediainfo.txt`、`1.png` 至 `6.png`
- 音频：`mediainfo.txt`、`频谱图.png`
- `BDMV` / `ISO`：`BDInfo.txt`、`1.png` 至 `6.png`
- 最终包：通常为 `.zip`，不可用时回退为 `.tar.gz`
- 可选图床上传：在结果 ZIP 内追加 `image-host.json`、`image-host-links.txt` 和 `image-host-bbcode.txt`

## 选择运行方式

### Windows / macOS 桌面 GUI

桌面端继续保留，并同时支持两种处理位置：直接处理当前电脑上的媒体，或从个人电脑连接媒体 VPS 执行扫描、生成、回传和清理。没有 VPS 也可以使用本机模式。

从 [`portable-latest`](https://github.com/My15sir/PT-ReleaseKit/releases/tag/portable-latest) 下载：

- Windows：`PT-ReleaseKit-windows-portable.zip`，解压后运行 `PT-ReleaseKit.exe`
- macOS：`PT-ReleaseKit-macos-portable.zip`，解压后运行 `PT-ReleaseKit.app`
- Linux：`PT-ReleaseKit-linux-portable.tar.gz`

源码入口仍然可用：

- Windows：`PT-ReleaseKit.bat`
- macOS：`PT-ReleaseKit.command`
- Linux：`PT-ReleaseKit.sh` 或 `PT-ReleaseKit.desktop`
- 通用 GUI：`ptbd-gui`

旧的 `PT-BDtool.bat`、`PT-BDtool.command`、`PT-BDtool.sh` 和 `PT-BDtool.desktop` 仍作为兼容入口保留。

源码运行 GUI 至少需要 Python 3 和 Tk。连接诊断和内置远端后端需要 Paramiko；缺少 Paramiko 时，远端扫描和处理会回退到系统 Bash/SSH。发布包由 PyInstaller 构建并包含控制端依赖，不要求普通用户直接运行源码。

便携包**不内置** `ffmpeg`、`ffprobe`、`mediainfo` 或 `BDInfo`。选择“本机电脑”时，当前电脑必须能从 `PATH` 找到 `ffmpeg`、`ffprobe` 和 `mediainfo`；`BDInfo` 是处理蓝光原盘的可选工具。可先点“检查本机依赖”，缺少必要工具时程序会停止本机任务并列出缺失项。Docker 镜像则已经包含媒体处理依赖。

三平台桌面冻结版当前对音频生成**单曲频谱**，不要把它当作组合频谱运行时。需要多轨组合频谱时，请使用 Docker/Web 或源码 CLI，并准备 NumPy 与 Pillow。

只有远端模式需要 SSH。首次连接一台新 VPS 前，请先在系统终端执行 `ssh -p 端口 用户@主机`，通过可信渠道核对服务器显示的主机密钥指纹后再接受。桌面端会读取 `~/.ssh/known_hosts`，并拒绝未知或发生变化的主机密钥，避免把 SSH 密码发送给未经确认的服务器。

首次使用先选择“本机电脑”或“远端 VPS”，再填写本机媒体目录或 SSH 参数以及本机保存目录。正常顺序是保存配置、检查环境、扫描候选、逐项勾选、生成并查看归档；开启自动清理只会删除本次临时工作目录和临时包，不会删除原始媒体。

远端扫描留空“扫描目录”时只遍历 `/home`。`/root`、`/data`、`/mnt`、`/media`、`/srv` 以及其他顶层目录不会再被默认遍历；媒体位于这些位置时，优先把所需路径明确填入“扫描目录”，确实需要时才开启“VPS 全盘扫描”。本机模式只扫描选择的“本机媒体目录”和显式添加的扫描目录，不会扫描整台电脑。

桌面配置和日志位置：

- Linux：`~/.config/ptbd-gui/config.json` 与同目录的 `PT-ReleaseKit.log`
- Windows 便携版：程序旁的 `PT-ReleaseKit-config.json`
- macOS 便携版：`PT-ReleaseKit.app` 同级目录的 `PT-ReleaseKit-config.json`
- Shell 远端模式：`~/.config/ptbd-remote/config.env`

升级时不会丢失原有桌面配置：新配置尚不存在时，程序会兼容读取旧的 `PT-BDtool-config.json`、Windows `%APPDATA%/PT-BDtool/gui-config.json` 或 macOS `Application Support/PT-BDtool/gui-config.json`；下次保存会写入新名称。

远端扫描或处理失败时，先检查系统终端能否直接 SSH 登录、VPS 账号是否能读取显式扫描目录，以及日志中是否有依赖或软件源错误。远端自动安装优先支持 Debian、Ubuntu 和 Alpine；其他发行版可能需要手动准备依赖。本机模式失败时先点“检查本机依赖”，再确认所选媒体根目录和保存目录权限。

VPS 禁用 SFTP 子系统时，内置后端会自动回退到 SSH 管道上传和下载，不需要为此开放额外端口。

### Docker：媒体 VPS 本机处理

Docker 应部署在**媒体文件所在的 VPS**。容器直接读取该 VPS 上的媒体目录并把结果写回宿主机，不再通过 SSH 把媒体搬到另一个处理节点。

Docker 镜像已包含 `ffmpeg`、`ffprobe`、`mediainfo` 和镜像提供的蓝光处理工具，是依赖齐备的 local 模式。它与桌面便携包不同，不要求宿主机另外为容器安装这些媒体工具。

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

Python 核心需要 `python3`、`ffmpeg`、`ffprobe` 和 `mediainfo`；处理蓝光时建议提供 `BDInfo`。远端安装流程和 Docker 镜像会准备相应运行环境，桌面便携包不会捆绑这些体积较大的系统媒体工具。

单曲频谱不要求额外 Python 图像模块；只有“组合频谱”需要 NumPy 和 Pillow。远端自动安装会按所选模式补齐它们，Docker 镜像则已内置。

仍保留的其他入口：`./ptbd` 是新手流程，`./ptbd-start.sh` 是本地菜单，`./ptbd-remote.sh` 是直接使用 Shell 控制远端 VPS 的兼容流程。

### 图床上传（可选）

“生成完成后上传截图到图床”默认关闭。开启后需要选择提供方并填写 API Token：

- `ImgBB`：内置官方上传地址，可填写兼容地址覆盖
- `Lsky Pro v2`：填写完整上传 API 地址
- `S.EE / SM.MS`：默认使用 S.EE，也可用 API 地址切换到兼容的 SM.MS 服务
- `custom`：填写返回 JSON 图片链接的 Bearer Token 兼容 API 地址

图床端点默认执行 HTTPS 策略：只有 `localhost`、`127.0.0.1`、`[::1]` 等回环端点可使用明文 HTTP，其他端点必须使用 HTTPS。如果明确接受可信内网中的 Token 和截图被明文传输的风险，可在启动控制端前设置 `PTBD_ALLOW_INSECURE_IMAGE_HOST=1`；不要对公网 HTTP 端点使用该开关。

桌面 GUI 或 Web 的 remote 图床上传只适用于内置 Python/Paramiko 后端：程序先在 VPS 生成材料并把结果包回传到控制端，再由控制端读取 ZIP 内的截图并上传。图床 Token 不会进入 SSH 命令、远端运行包或发送到媒体 VPS。源码环境缺少 Paramiko 而进入本机 Bash/SSH 的旧 Shell 回退时，材料生成和下载仍可继续，但图床上传会明确跳过；需要远端图床时请安装 Paramiko 或使用便携版。本机处理不经过这个远端 Shell fallback，可以正常执行图床后处理。

Docker local 模式的控制端本身运行在 VPS 容器内，因此其 Token 保存在该部署的 `/config/config.json`；应继续通过回环监听、SSH 隧道或受保护的反向代理访问。

上传完成后，结果 ZIP 会包含：

- `image-host.json`：提供方、逐图成功/失败状态和公开链接
- `image-host-links.txt`：每行一个成功上传的图片链接
- `image-host-bbcode.txt`：可直接粘贴的 `[img]...[/img]` 文本

上传是非关键后处理。单张或整批上传失败不会删除原材料，也不会把已经成功的材料生成任务改成失败；归档更新使用原子替换，无法安全更新时保留原 ZIP。图床处理只支持常规 ZIP，回退生成的 `.tar.gz` 仍会保留但不会被改写。

密码和图床 Token 只保存在私有配置中。POSIX 平台的配置文件以 `0600` 写入，公开 Web 配置响应、普通日志和错误消息只显示“是否已保存”，不会返回 Token 内容。

## Python-first 与 Shell 兼容

`bdtool` 是稳定入口，默认把处理命令交给 `python3 -m ptbd_core.cli`。Python 包按职责拆分：

- `scanner.py`：媒体发现与类型识别
- `media_tools.py`：外部媒体工具调用
- `artifacts.py`：截图、MediaInfo、频谱图和 BDInfo 产物
- `pipeline.py`：处理流程编排
- `returns.py`：打包与回传
- `local_runtime.py`：桌面/Web 本机执行环境和路径边界
- `image_hosts.py`：回传后的可选图床上传与 ZIP 清单
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

桌面端分为“工作台”和“连接设置”两个页签。工作台把主要空间留给可滚动候选列表，并继续显示四步进度、任务状态和实时日志；连接参数单独放在设置页。Tk 列表可点击“选择”列或按空格逐项切换，也可使用“只选当前”“全选结果”和“清空勾选”。筛选不会丢失已勾选项，新一轮扫描会清理已经不存在的路径。

首次启动会直接打开“连接设置”，先选择处理位置并填写对应字段：

- `本机电脑`：选择一个本机媒体目录；程序只扫描该目录和显式添加的目录
- `远端 VPS`：填写 VPS 地址（例如 `root@1.2.3.4`）、SSH 端口以及密码或已有密钥
- `本机保存目录`

其他项怎么理解：

- `空白 VPS 自动准备运行环境`
  - 开着更省心
  - 程序会先尝试远端自动装依赖
  - 还不够时才回退上传运行包
- `额外扫描目录`
  - 不懂就留空
  - 远端留空时只扫描 `/home`
  - `/root`、`/data`、`/mnt`、`/media`、`/srv` 需要明确填写后才扫描
  - 本机模式中，这些值作为“本机媒体目录”之外的额外允许根目录
  - 显式白名单优先于全盘扫描开关
  - 多个根目录可用空格或逗号分隔；含空格、逗号或撇号的单个路径请使用双引号，例如 `"/data/PT Movies" "/mnt/O'Brien, Archive"`
- `启用全盘扫描（高级）`
  - 默认关闭
  - 只有媒体不在常见目录时再打开
- `任务结束后清理临时工作目录`
  - 建议开启
  - 只清这次生成的结果目录和结果包，不删原始媒体文件
- `生成完成后上传截图到图床`
  - 默认关闭
  - 只有主动开启并配置提供方、API 地址和 Token 后才上传
  - remote 模式在结果回传后上传，Token 不会发到媒体 VPS

## 主流程真实顺序

正常使用顺序就是：

1. 选择“本机电脑”或“远端 VPS”并保存配置
2. 本机模式先点“检查本机依赖”；远端模式先点“测试连接”
3. 返回工作台，点“扫描本机”或“扫描 VPS”
4. 遍历目录时查看实时目录、文件、候选计数和当前路径
5. 解析候选时查看真实完成比例；`.ts` 文件探测会显示正在处理的文件
6. 在宽幅候选列表逐项勾选，或用“只选当前”快速保留一项，再点“生成所选”
7. 等程序在所选位置生成截图和媒体信息；批量任务会逐项继续
8. 远端模式会把结果包下载回控制端，本机模式直接写入保存目录
9. 如启用图床，回传后查看逐图结果，并可复制 BBCode
10. 在日志查看成功/失败汇总；失败项可点“重试失败”
11. 完成后可直接点“打开结果目录”；自动清理只处理本次临时文件

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
  - Windows 便携版: 程序旁边的 `PT-ReleaseKit-config.json`
  - macOS 便携版: `PT-ReleaseKit.app` 同级目录的 `PT-ReleaseKit-config.json`
- `ptbd` 新手模式配置
  - `~/.config/ptbd/config.env`
- shell 远端模式配置
  - `~/.config/ptbd-remote/config.env`

### 日志文件

- GUI 日志通常在 GUI 配置目录旁边的 `PT-ReleaseKit.log`
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
- `local`：直接扫描当前主机上指定的媒体根目录；Docker 默认并应保持此模式，扫描根目录使用 `/media`

Web 和 GUI 都支持环境检查、批量逐项处理、成功/失败汇总及失败项重试。候选区会占据工作台的主要空间；每项都有独立复选框，Web 还提供“仅选此项”，不会再强制全选。筛选或翻页不会清空其他已选项。

Web 任务可能结束为 `success`、`partial`、`error` 或 `cancelled`；页面刷新后会自动恢复当前运行任务的日志和停止操作。local 模式只显示本机媒体与输出路径，不显示无关的 SSH 字段。remote 模式留空扫描目录时也只扫描 `/home`；需要 `/root`、`/data`、`/mnt`、`/media`、`/srv` 时必须明确填写，或主动开启全盘扫描。

Web 与桌面 GUI 使用同一套可选图床配置和结果清单。页面会显示成功链接并提供 BBCode 复制；公开配置和任务状态不会暴露 Token。

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

`install.sh` 不通过系统包管理器修改宿主机；离线 bundle 可由 `scripts/ensure-bundle.py` 准备。新名称官方 bundle 必须通过 Release 的 `.sha256` sidecar 或显式摘要校验；只有旧文件名资产可使用仓库内固定摘要完成迁移。

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
