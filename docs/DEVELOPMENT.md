# PT ReleaseKit 开发与发布说明

这份文档面向维护者。普通用户应先阅读根目录 `README.md`，Docker 运维请阅读 `docs/DOCKER.md`。

品牌展示统一使用 **PT ReleaseKit**，仓库 slug 使用 `PT-ReleaseKit`。以下名称属于升级兼容接口，未经迁移设计不得直接重命名：`bdtool`、`ptbd-*`、`PTBD_*` 环境变量、`pt-bdtool:*` Docker 镜像标签、`/opt/PT-BDtool`、现有配置目录、`PT-BDtool-*` 下载文件名和 `bundle-latest` / `portable-latest` 标签。

## 1. 架构原则

项目采用“Python 模块化核心优先，Shell 保留兼容与远端 fallback”的结构：

- 新扫描、生成、打包和回传逻辑放入 `ptbd_core/`
- `bdtool` 是稳定命令入口，负责选择 Python 核心或兼容实现
- `bdtool-legacy.sh` 保留旧交互菜单和回退能力，不再作为新增业务逻辑的首选位置
- Web、GUI 和远端控制端复用共享配置模型与运行资产清单
- Docker 只运行 Web local 模式，部署在媒体所在 VPS
- Windows/macOS GUI 继续作为桌面远端控制端交付

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

- GUI 通常运行在用户电脑，通过 SSH 控制媒体 VPS并回传结果
- Docker 运行在媒体 VPS，直接处理只读挂载到 `/media` 的文件

## 3. Python 核心

`ptbd_core/` 的主要职责：

- `models.py`：媒体类型、运行模式和共享配置数据模型
- `config.py`：配置默认值、规范化、校验与安全落盘
- `scanner.py`：视频、音频、音频目录、`BDMV` 和 `ISO` 扫描
- `media_tools.py`：`ffmpeg`、`ffprobe`、`mediainfo`、`BDInfo` 等工具封装
- `artifacts.py`：截图、MediaInfo、音频频谱和 BDInfo 产物生成
- `pipeline.py`：单个媒体的处理流水线与清理
- `returns.py`：打包、本地、HTTP 和 SCP 回传
- `bundle_archive.py`：远端 bundle 的安全解包和成员限制
- `jobs.py`：Web 任务状态、取消回调和有界历史
- `cli.py`：无界面的命令行适配层
- `runtime_assets.py`：各交付形态共用的运行资产清单
- `assets/`：远端探测和依赖安装脚本

模块边界要求：

- 扫描器只负责发现和分类，不生成产物
- 外部命令调用集中在工具层，调用者处理领域错误而不是拼接重复命令
- 流水线编排处理步骤，不包含 GUI/Web 状态
- 密码不得出现在 `repr`、公开配置 API 或普通日志中

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

批量处理必须逐项记录失败并继续。公开任务包含 `failed` 和 `result_summary`，处理状态使用 `success`、`partial`、`error` 或 `cancelled`；不要让 GUI/Web 各自维护不兼容的汇总结构。

配置文件通过同目录临时文件原子替换并以 `0600` 写入。公开配置响应必须隐藏 `remote_password`，只能返回是否已保存密码的状态。

远端后端优先使用 SFTP，子系统不可用时回退 SSH stdin/stdout 管道。打包远端 runtime 时，Shell/Python/文本配置统一转换为 LF，二进制文件保持原样；这两条兼容约定需要回归测试。

扫描根的兼容字符串采用 Shell 引号语义，空格或逗号可分隔多个根；路径本身含空格、逗号或撇号时使用双引号。适配器之间优先传 `*_ROOTS_JSON`，其次传逐行 `*_ROOTS_LINES`，旧字符串仅作最后兼容；显式结构化值无效时必须 fail-closed，不能退回更宽的扫描范围。

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

镜像基于 Debian bookworm slim，内置 Python 和媒体工具，以非 root 用户运行。Compose 默认：

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

不要在镜像入口中重新实现媒体处理。容器只负责准备目录、初始化 local 模式配置并启动 `ptbd-web.py`。

## 7. 本地开发与测试

### Python 单元测试

```bash
python3 -m unittest discover -s tests
```

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

- Windows：`PT-BDtool.exe`
- macOS：`PT-BDtool.app`
- Linux：`PT-BDtool-linux-portable.tar.gz`

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

涉及桌面端时，至少运行 `ptbd-gui --self-check`。Linux 上还应运行 `xvfb-run -a python3 ptbd-gui.py --ui-smoke-check`，确认候选区、主操作和日志区在默认窗口内均可见；同时确认 Windows/macOS 打包工作流仍包含新的共享资产。

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
