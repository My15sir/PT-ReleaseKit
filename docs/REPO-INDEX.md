# PT-BDtool 仓库导航

这份文档只做一件事：帮你快速看懂这个仓库每块是干什么的。

如果你是普通用户：

- 直接看根目录 `README.md`
- 不要把源码仓库当成最终成品包

如果你是维护者：

- 先看 `docs/DEVELOPMENT.md`
- 再看这份仓库导航

---

## 1. 根目录入口

这些文件是最常碰到的入口：

- `PT-BDtool.bat`
  - Windows 双击入口
- `PT-BDtool.command`
  - macOS 双击入口
- `PT-BDtool.desktop`
  - Linux 桌面入口
- `PT-BDtool.sh`
  - Linux 统一双击启动脚本
- `ptbd-gui`
  - GUI 包装器
- `ptbd-gui.py`
  - GUI 主程序
- `ptbd-web`
  - 本机 Web 控制端入口
- `ptbd-web.py`
  - Web 控制端主程序
- `ptbd`
  - 新手模式入口
- `ptbd-remote.sh`
  - 远端一键流程入口
- `ptbd-start.sh`
  - 本地菜单入口
- `bdtool`
  - 主 CLI / 扫描与生成核心入口
- `install.sh`
  - 离线安装入口

说明：

- Linux 现在统一使用 `PT-BDtool.sh`
- 不要再恢复旧文件名 `启动PT-BDtool.sh`

---

## 2. 代码分区

### 核心处理

- `bdtool`
- `lib/ui.sh`

说明：

- `bdtool` 是唯一核心处理实现
- `bdtool.sh` 仅保留为旧命令兼容转发入口

### GUI / 控制端

- `ptbd-gui`
- `ptbd-gui.py`
- `ptbd-web`
- `ptbd-web.py`
- `ptbd_remote_backend.py`

### 远端运行与回传

- `ptbd-remote.sh`
- `ptbd-remote-start.sh`
- `scripts/prepare-remote-runtime.sh`
- `scripts/remote-upload-server.py`

### 安装与打包

- `install.sh`
- `scripts/build-bundle.sh`
- `scripts/build-controller-app.py`
- `scripts/fetch-deps.sh`
- `scripts/ensure-bundle.py`

---

## 3. 文档

- `README.md`
  - 面向普通用户
- `docs/README.en.md`
  - 英文说明
- `docs/DEVELOPMENT.md`
  - 面向维护者
- `docs/REPO-INDEX.md`
  - 仓库导航

---

## 4. GitHub 相关

### Actions 工作流

- `.github/workflows/controller-build.yml`
  - 构建并发布 Windows / macOS / Linux 便携包
- `.github/workflows/bundle-release.yml`
  - 构建并发布 Linux bundle 资产
- `.github/workflows/ci.yml`
  - 语法检查、回归测试、离线安装检查

### Release 标签

- `portable-latest`
  - 给普通用户下载成品包
- `bundle-latest`
  - 给源码仓库和控制端打包复用 Linux bundle

---

## 5. 本地产物

下面这些是运行或测试时生成的，不是源码主体：

- `bdtool-output/`
- `.tmp/`
- `.full-test*`
- `build/`
- `dist/`
- `artifact/`
- `release-assets/`
- `third_party/bundle/linux-amd64/`

---

## 6. 维护建议

- 普通用户只面向 `portable-latest`
- 源码仓库不要再混入大体积 bundle 二进制
- 改 Linux 启动方式时，要同时改：
  - `README.md`
  - `docs/README.en.md`
  - `.github/workflows/controller-build.yml`
  - 根目录启动文件
