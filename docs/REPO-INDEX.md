# PT ReleaseKit 仓库导航

普通用户先看根目录 `README.md`。维护者先看 `docs/DEVELOPMENT.md`，Docker 部署看 `docs/DOCKER.md`。

仓库已更名为 PT ReleaseKit。下列仍含 `PT-BDtool` 的文件名是有意保留的兼容入口，不代表文档或界面仍使用旧品牌。

## 1. 稳定入口

- `bdtool`：Python-first CLI 包装器，根据兼容规则分发
- `bdtool-legacy.sh`：旧 Shell 菜单与兼容处理实现
- `bdtool.sh`：旧命令名兼容转发
- `ptbd`：新手入口
- `ptbd-start.sh`：本地菜单入口
- `ptbd-remote.sh`、`ptbd-remote-start.sh`：远端 Shell 流程
- `install.sh`：离线安装入口

`bdtool` 的分发规则：处理命令默认进入 `ptbd_core.cli`；无参数、旧菜单参数、`PTBD_PYTHON_CORE=0` 或缺少 Python 3 时进入 `bdtool-legacy.sh`。

## 2. Python 核心

- `ptbd_core/cli.py`：命令行适配
- `ptbd_core/models.py`：共享数据模型
- `ptbd_core/config.py`：共享配置、校验和落盘
- `ptbd_core/scanner.py`：媒体扫描和分类
- `ptbd_core/media_tools.py`：媒体工具调用
- `ptbd_core/artifacts.py`：截图、报告和频谱产物
- `ptbd_core/pipeline.py`：处理流程编排
- `ptbd_core/returns.py`：打包和回传
- `ptbd_core/bundle_archive.py`：bundle 安全解包与资源限制
- `ptbd_core/jobs.py`：异步任务状态与取消
- `ptbd_core/runtime_assets.py`：交付形态的规范运行资产清单
- `ptbd_core/assets/`：远端探测与依赖安装脚本

## 3. 桌面与 Web 控制端

- `PT-BDtool.bat`：Windows 源码双击入口
- `PT-BDtool.command`：macOS 源码双击入口
- `PT-BDtool.desktop`、`PT-BDtool.sh`：Linux 桌面入口
- `ptbd-gui`、`ptbd-gui.py`：Windows/macOS/Linux Tk 控制端
- `ptbd-web`、`ptbd-web.py`：Web 控制端
- `ptbd_remote_backend.py`：SSH/远端控制后端

Windows/macOS GUI 继续交付。它们与 Docker 服务不同：桌面端控制远端 VPS；Docker 在媒体 VPS 上直接运行 local 模式。

## 4. Docker

- `Dockerfile`：非 root 生产镜像
- `compose.yaml`：媒体、输出和配置挂载
- `.dockerignore`：镜像构建上下文过滤
- `docker/entrypoint.sh`：初始化 local 模式配置并启动 Web
- `docker/healthcheck.py`：检查 `/api/status`

默认容器路径：

- `/media`：媒体，只读
- `/output`：生成结果，可写
- `/config`：配置和运行状态，可写

部署说明：`docs/DOCKER.md`。

## 5. 远端运行与回传

- `scripts/prepare-remote-runtime.sh`：准备远端运行目录
- `scripts/remote-upload-server.py`：HTTP 回传接收端
- `ptbd_core/assets/remote-probe.sh`：远端环境探测
- `ptbd_core/assets/remote-install-deps.sh`：远端依赖安装

远端 runtime 的文件清单来自 `ptbd_core/runtime_assets.py`，不要在适配器中重复维护。

远端传输优先使用 SFTP，不可用时自动回退 SSH 管道。上传 runtime 前会把脚本、Python 和文本配置规范化为 LF，以兼容 Linux VPS。

## 6. 安装、打包与发布

- `scripts/fetch-deps.sh`：获取或整理运行依赖
- `scripts/build-bundle.sh`：构建 Linux 离线 bundle
- `scripts/ensure-bundle.py`：复用或下载 bundle
- `scripts/build-controller-app.py`：PyInstaller 桌面控制端构建
- `.github/workflows/bundle-release.yml`：发布 `bundle-latest`
- `.github/workflows/controller-build.yml`：发布 `portable-latest`

## 7. 测试与 CI

- `tests/`：Python 核心、配置、任务和 Web 接口单元测试
- `scripts/full-test.sh`：跨入口全量回归与真实媒体 fixture
- `.github/workflows/ci.yml`：ShellCheck、全量回归、Docker smoke、bundle 和离线安装

常用验证：

```bash
python3 -m unittest discover -s tests
./scripts/full-test.sh
docker compose config --quiet
docker build -t pt-bdtool:local .
```

## 8. 文档

- `README.md`：中文用户说明
- `docs/README.en.md`：英文用户说明
- `docs/DOCKER.md`：Docker 部署、升级和排障
- `docs/DEVELOPMENT.md`：维护、测试和发布
- `docs/REPO-INDEX.md`：当前文件

## 9. 生成目录

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

这些是运行、测试或发布产物，不应被当作核心源码提交。
