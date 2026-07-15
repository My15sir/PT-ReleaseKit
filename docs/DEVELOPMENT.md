# PT ReleaseKit 开发与发布说明

这份文档面向维护者。普通用户应先阅读根目录 `README.md`，Docker 运维请阅读 `docs/DOCKER.md`。

品牌展示统一使用 **PT ReleaseKit**，仓库 slug 使用 `PT-ReleaseKit`。Release 归档、打包程序和新桌面启动器使用 `PT-ReleaseKit-*` / `PT-ReleaseKit.*` 文件名。以下名称属于升级兼容接口，未经迁移设计不得直接重命名：`bdtool`、`ptbd-*`、`PTBD_*` 环境变量、`pt-bdtool:*` Docker 镜像标签、`/opt/PT-BDtool`、现有配置目录、旧版 `PT-BDtool.*` 源码启动器和 `bundle-latest` / `portable-latest` 标签。

## 1. 架构原则

项目采用“Python 模块化核心优先，Shell 保留兼容与远端 fallback”的结构：

- 新扫描、生成、打包和回传逻辑放入 `ptbd_core/`
- `bdtool` 是稳定命令入口，负责选择 Python 核心或兼容实现
- `bdtool-legacy.sh` 保留旧交互菜单和回退能力，不再作为新增业务逻辑的首选位置
- Web、GUI 和远端控制端复用共享配置模型与运行资产清单
- Docker 只运行依赖齐备的 Web local 模式，部署在媒体所在 VPS
- Windows/macOS/Linux GUI 同时支持桌面本机处理和 SSH 远端控制

不要在 GUI、Web 和 Shell 中分别复制媒体处理逻辑。新功能应先在 Python 核心形成可测试接口，再由各入口调用。

## 2. 入口与分发

### `bdtool`

默认执行：

```text
python3 -m ptbd_core.cli ...
```

以下情况转发到 `bdtool-legacy.sh`：

- 不带参数
- 参数为 `start`、`install`、`--lang` 或 `--non-interactive` 等旧菜单入口
- 环境变量 `PTBD_PYTHON_CORE=0`
- 找不到 Python 3

这条兼容约定属于用户接口。调整前必须覆盖安装版、便携包、远端 runtime 和回归测试。

### 用户界面

- `ptbd-gui.py`：Windows/macOS/Linux Tk 桌面控制端
- `ptbd-web.py`：Web 控制端，支持 `remote` 和 `local` 模式
- `ptbd_remote_backend.py`：桌面控制端的 SSH/远端运行后端
- `ptbd`、`ptbd-start.sh`、`ptbd-remote.sh`：现有新手与 Shell 流程

桌面 GUI 和 Docker 是并列交付形态，不互相替代：

- GUI 运行在用户电脑，可直接处理所选本机媒体根目录，也可通过 SSH 控制媒体 VPS 并回传结果
- Docker 运行在媒体 VPS，使用镜像内媒体依赖直接处理只读挂载到 `/media` 的文件

## 3. Python 核心

`ptbd_core/` 的主要职责：

- `models.py`：媒体类型、运行模式和共享配置数据模型
- `config.py`：配置默认值、规范化、校验与安全落盘
- `scanner.py`：视频、音频、音频目录、`BDMV` 和 `ISO` 扫描
- `media_tools.py`：`ffmpeg`、`ffprobe`、`mediainfo`、`BDInfo` 等工具封装
- `artifacts.py`：截图、MediaInfo、音频频谱和 BDInfo 产物生成
- `pipeline.py`：单个媒体的处理流水线与清理
- `returns.py`：打包、本地、HTTP 和 SCP 回传
- `local_runtime.py`：本机执行命令、依赖检查、允许根目录和回传记录校验
- `image_hosts.py`：结果回传后的可选图床上传、安全响应解析和 ZIP 清单原子更新
- `bundle_archive.py`：远端 bundle 的安全解包和成员限制
- `jobs.py`：Web 任务状态、取消回调和有界历史
- `cli.py`：无界面的命令行适配层
- `runtime_assets.py`：各交付形态共用的运行资产清单
- `assets/`：远端探测和依赖安装脚本

模块边界要求：

- 扫描器只负责发现和分类，不生成产物
- 外部命令调用集中在工具层，调用者处理领域错误而不是拼接重复命令
- 流水线编排处理步骤，不包含 GUI/Web 状态
- 本机选择路径必须位于 `local_root` 或显式 `scan_include` 根目录内，不能仅依赖 UI 过滤
- 图床上传只能处理已经回到控制端的常规 ZIP，不能把 Token 注入远端 SSH 环境或远端 runtime
- 非回环图床 endpoint 必须使用 HTTPS；`PTBD_ALLOW_INSECURE_IMAGE_HOST=1` 只能作为用户明确接受可信内网明文风险的进程级覆盖，不能成为默认配置
- 密码和图床 Token 不得出现在 `repr`、公开配置 API、任务快照、普通日志或异常正文中

## 4. Web 与任务模型

`ptbd-web.py` 使用共享 `AppConfig` 和 `JobRegistry`。任务接口包括：

- `GET /api/status`
- `GET /api/config`
- `POST /api/config`
- `POST /api/scan`
- `POST /api/process`
- `POST /api/diagnose`
- `GET /api/tasks/<id>`
- `POST /api/tasks/<id>/cancel`

任务预留必须保持原子性，同一时间只允许一个活动扫描、处理或诊断任务。添加新的阻塞子进程时，必须注册取消回调，并在结束路径移除回调。

批量处理必须逐项记录失败并继续。公开任务包含 `failed`、`result_summary` 和非关键的 `image_uploads`，处理状态使用 `success`、`partial`、`error` 或 `cancelled`；图床失败不得把成功的材料生成任务降级为主流程失败，也不要让 GUI/Web 各自维护不兼容的汇总结构。

配置文件通过同目录临时文件原子替换并以 `0600` 写入。公开配置响应必须隐藏 `remote_password` 和 `image_host_token`，只能分别返回 `password_saved` 与 `image_host_token_saved`。空 Token 表示保留已有值，只有显式 `clear_image_host_token` 才清除。

远端后端优先使用 SFTP，子系统不可用时回退 SSH stdin/stdout 管道。打包远端 runtime 时，Shell/Python/文本配置统一转换为 LF，二进制文件保持原样；这两条兼容约定需要回归测试。

扫描根的兼容字符串采用 Shell 引号语义，空格或逗号可分隔多个根；路径本身含空格、逗号或撇号时使用双引号。适配器之间优先传 `*_ROOTS_JSON`，其次传逐行 `*_ROOTS_LINES`，旧字符串仅作最后兼容；显式结构化值无效时必须 fail-closed，不能退回更宽的扫描范围。

远端无人值守扫描的默认根只有 `/home`。`/root`、`/data`、`/mnt`、`/media`、`/srv` 和 `/` 必须来自显式 `scan_include` 或用户明确启用的 `scan_full`；不得以“常见媒体目录”为由重新加入默认遍历。本机模式以 `local_root` 为主根，`scan_include` 只增加显式允许根，`scan_full` 不得扩展为整机扫描。

图床配置默认关闭，提供方枚举为 `imgbb`、`lsky_v2`、`see` 和 `custom`。ImgBB 与 S.EE 有默认地址，Lsky Pro v2 和 custom 要求显式 endpoint，`see` 可用 endpoint 覆盖为兼容 SM.MS 服务。endpoint 校验只允许 HTTPS 或真实回环主机的 HTTP；非回环 HTTP 必须默认 fail-closed，仅当进程环境明确设置 `PTBD_ALLOW_INSECURE_IMAGE_HOST=1` 时放行，并继续禁止 URL 内凭据和 fragment。

remote 图床上传仅属于内置 Python/Paramiko 后端：必须先回传归档再在控制端上传，因此 Token 不得进入媒体 VPS。本机控制端退回 Bash/SSH 的旧 Shell backend 时，材料处理与回传可以继续，但必须记录并跳过图床，不能为了上传而扩大凭据边界。桌面/Web local 模式和 Docker local 模式不经过这条远端 fallback；Docker 控制端就在 VPS，Token 会留在 `/config/config.json`，相关文档与部署边界必须保持清楚。

成功或部分上传后，`image_hosts.py` 在结果 ZIP 的公共结果目录中原子写入 `image-host.json`、`image-host-links.txt` 和 `image-host-bbcode.txt`。单图失败写入报告并继续，致命配置、归档或替换错误返回脱敏报告且保留原材料。任何 ZIP 成员读取与重写都必须继续执行成员数量、总解压大小、单图大小、符号链接、加密成员和路径穿越限制。

## 5. 运行资产清单

安装器、远端 runtime、Docker、离线 bundle 和 PyInstaller 控制端应复用 `ptbd_core/runtime_assets.py`，不要各自维护文件列表。

验证所有 profile：

```bash
for profile in install remote bundle controller docker; do
  python3 ptbd_core/runtime_assets.py validate \
    --profile "$profile" \
    --source-root "$PWD"
done
```

新增运行时文件时：

1. 先更新规范资产清单。
2. 更新对应复制/打包适配器。
3. 添加 profile 验证或回归 fixture。
4. 检查 controller、remote 和 Docker 是否都需要该文件。

## 6. Docker 开发

主要文件：

- `Dockerfile`
- `compose.yaml`
- `.dockerignore`
- `docker/entrypoint.sh`
- `docker/healthcheck.py`

镜像基于 Debian bookworm slim，内置 Python、`ffmpeg`、`ffprobe`、`mediainfo` 和镜像选用的蓝光工具，以非 root 用户运行。这是 Docker local 模式与桌面便携控制端的重要边界：便携包不捆绑系统媒体工具。Compose 默认：

- `/media` 只读
- `/output` 和 `/config` 可写
- 删除全部 capabilities
- 启用 `no-new-privileges`
- `/tmp` 使用受限 tmpfs

构建与验证：

```bash
mkdir -p media output config
chown "$(id -u):$(id -g)" output config
docker compose config --quiet
docker build -t pt-bdtool:local .
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8899/api/status
docker compose down
```

不要在镜像入口中重新实现媒体处理。容器只负责准备目录、初始化 local 模式配置并启动 `ptbd-web.py`。容器扫描必须受 `/media` 和显式额外挂载根约束，不能因为宿主是 VPS 就默认遍历系统目录。Compose 应以 `0` 传递 `PTBD_ALLOW_INSECURE_IMAGE_HOST` 默认值，只有部署者在 `.env` 中明确改为 `1` 才允许非回环 HTTP 图床。

## 7. 本地开发与测试

### Python 单元测试

```bash
python3 -m unittest discover -s tests
```

本机执行与图床变更至少覆盖 `tests/test_local_runtime.py`、`tests/test_image_hosts.py`、`tests/test_core_config.py`、`tests/test_ui_contract.py` 和 `tests/test_web_interface.py`。测试应使用临时目录或本地 mock HTTP 服务，不向真实图床上传。

### Python 语法与字节码

```bash
python3 -m compileall -q \
  ptbd_core \
  ptbd-gui.py \
  ptbd-web.py \
  ptbd_remote_backend.py \
  scripts
```

### Shell 语法

```bash
bash -n \
  bdtool \
  bdtool-legacy.sh \
  install.sh \
  ptbd \
  ptbd-gui \
  ptbd-start.sh \
  ptbd-remote.sh \
  docker/entrypoint.sh
```

### 全量回归

```bash
./scripts/full-test.sh
```

全量回归包含 Python 单元测试、运行资产 profile、兼容包装器，以及视频、组合音频、`BDMV` 和 `ISO` fixture。修改处理流程时不要只运行 Shell 语法检查。

### GUI 自检

```bash
./ptbd-gui --self-check
```

该检查验证完整 controller 资产，不应改成无条件成功。

## 8. 离线 Bundle 与安装

大型 Linux 离线依赖不长期提交到主分支。按需生成或从 `bundle-latest` Release 获取：

```bash
python3 scripts/ensure-bundle.py
```

下载校验顺序为：显式 `PTBD_BUNDLE_SHA256`、Release `.sha256` sidecar、仅限旧版官方 URL 的固定迁移摘要。自定义 `PTBD_BUNDLE_URL` 不得复用官方摘要，必须提供可信 digest/sidecar；默认 fail-closed。`PTBD_BUNDLE_ALLOW_UNVERIFIED=1` 只允许在 sidecar 不可用时跳过校验，不能放行格式错误或不匹配的校验内容。

组合音频频谱额外依赖 NumPy 与 Pillow；基础扫描、视频、单曲频谱不应因缺少这两个模块而判定核心依赖不完整。修改远端探测或安装脚本时，需要同时验证 `single` 与 `combined`。

关键文件：

- `scripts/fetch-deps.sh`
- `scripts/build-bundle.sh`
- `scripts/ensure-bundle.py`
- `.github/workflows/bundle-release.yml`

离线安装验证：

```bash
bash install.sh --offline --no-launch
```

`install.sh` 不应调用 `apt` 等宿主包管理器。系统依赖安装只属于明确的远端 bootstrap 或 Docker 镜像构建过程。

## 9. 桌面应用构建

控制端构建脚本：

```bash
python3 scripts/ensure-bundle.py
python3 scripts/build-controller-app.py
```

发布目标：

- Windows：`PT-ReleaseKit.exe`
- macOS：`PT-ReleaseKit.app`
- Linux：`PT-ReleaseKit-linux-portable.tar.gz`

离线 Linux bundle 发布为 `PT-ReleaseKit-linux-amd64.tar.gz`。

PyInstaller 桌面包包含控制端 Python/Tk/Paramiko 资产，但不包含 `ffmpeg`、`ffprobe`、`mediainfo` 或 `BDInfo`。本机模式通过 `local_runtime.local_dependency_report()` 检查前三项必要工具和可选 BDInfo；打包验证不得把“控制端可启动”误当成“本机媒体依赖已安装”。`local_runtime.py` 和 `image_hosts.py` 必须进入 controller runtime 资产。

桌面候选区是主要工作区，Tk 必须保留独立勾选状态、“只选当前”、筛选后选择保持和新扫描清理失效路径。Web 每个候选必须有独立复选框与“仅选此项”，并保留键盘焦点。默认窗口的 UI smoke 必须同时确认候选区和日志区可见，而不是靠压缩候选列表容纳设置。

`scan-json --progress-json` 保持 stdout 为最终 JSON，并在 stderr 按行输出 `PTBD_SCAN_PROGRESS\t{json}`。遍历阶段 `phase=walking` 的总量未知，控制端必须使用不定进度；解析阶段 `phase=resolving` 才能按 `processed_candidates / total_candidates` 显示百分比。桌面与 Web 的 SSH 扫描必须分别消费 stdout/stderr，并保留取消、空闲超时和总时限。

`.github/workflows/controller-build.yml` 负责构建并更新 `portable-latest`。改动 GUI、共享配置、远端后端或 controller runtime 资产时，需要同步检查工作流的路径触发和打包清单。

## 10. GitHub Actions

- `.github/workflows/ci.yml`：ShellCheck、Python/全量回归、Docker build/smoke、bundle 与离线安装
- `.github/workflows/controller-build.yml`：Windows/macOS/Linux 便携控制端
- `.github/workflows/bundle-release.yml`：Linux 离线 bundle

Docker smoke 应至少验证：

- Compose 配置可解析
- 镜像可构建
- 容器以指定非 root UID/GID 启动
- Docker healthcheck 进入 `healthy`
- `/api/status` 返回 `ok: true`
- local 模式空目录扫描任务正常结束

## 11. 发布前检查

```bash
python3 -m unittest discover -s tests
./scripts/full-test.sh
python3 -m compileall -q ptbd_core ptbd-gui.py ptbd-web.py ptbd_remote_backend.py
git diff --check
git status --short
```

涉及 Docker 时额外运行：

```bash
docker compose config --quiet
docker build -t pt-bdtool:release-check .
```

涉及桌面端时，至少运行 `ptbd-gui --self-check`。Linux 上还应运行 `xvfb-run -a python3 ptbd-gui.py --ui-smoke-check`，确认宽幅候选区、逐项选择、主操作和日志区在默认窗口内均可见；同时确认 Windows/macOS 打包工作流包含 `local_runtime.py` 与 `image_hosts.py`，且发布说明没有暗示便携包内置系统媒体工具。

涉及配置或图床时，还要验证配置文件权限为 `0600`、公开 API 与任务快照不含密码/Token、错误文本不回显 endpoint 查询密钥或响应正文、上传失败保留原结果包。endpoint 测试必须覆盖 HTTPS、IPv4/IPv6/hostname 回环 HTTP、默认拒绝非回环 HTTP，以及 `PTBD_ALLOW_INSECURE_IMAGE_HOST=1` 的显式放行。remote 模式测试必须证明图床 Token 未进入 SSH 命令、远端环境或上传的 runtime，并证明 Shell fallback 只跳过上传而不丢失已回传材料。

## 12. 生成目录

以下目录或文件是构建、运行和测试产物，不属于源码主体：

- `bdtool-output/`
- `.tmp/`
- `.full-test*`
- `.docker-smoke/`
- `build/`
- `dist/`
- `artifact/`
- `release-assets/`
- `third_party/bundle/linux-amd64/`
- `__pycache__/`
