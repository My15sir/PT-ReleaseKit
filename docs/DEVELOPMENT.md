# PT-BDtool 开发与发布说明

这份文档是给维护者看的，不是给普通用户看的。  
普通用户直接看根目录 `README.md` 就够了。

---

## 1. 仓库现在为什么轻了一些

源码本身并不算特别多。  
之前最占体积的是整套 Linux 离线运行包。

现在它不再长期跟踪进源码仓库，而是改成：

- 默认从 GitHub Release 资产自动拉取
- 本地缓存到 `third_party/bundle/linux-amd64`
- 继续给 Windows / macOS 控制端打包使用
- 继续给远端空白 VPS 回退运行使用

它的作用是：

- 支持离线安装
- 支持空白 VPS 回退运行
- 减少目标机手动装依赖

所以现在主分支里看起来会干净很多，`third_party/bundle/linux-amd64` 只是按需生成目录，不是常驻源码。

---

## 2. 当前主要入口

### 用户入口

- `PT-BDtool.exe`
- `PT-BDtool.app`
- `ptbd-gui.py`

### 远端主流程

- `ptbd_remote_backend.py`
- `ptbd-remote.sh`
- `scripts/prepare-remote-runtime.sh`

### 核心处理

- `bdtool`
- `bdtool.sh`
- `lib/ui.sh`

### 打包与发布

- `scripts/build-controller-app.py`
- `.github/workflows/controller-build.yml`

### 回归测试

- `scripts/full-test.sh`
- `.github/workflows/ci.yml`

---

## 3. 仓库目录怎么分

仓库现在建议按下面这个理解来维护，不要再把“源码入口”“发布入口”“测试产物”混在一起看。

### 根目录里真正重要的入口

- `PT-BDtool.bat`
- `PT-BDtool.command`
- `PT-BDtool.desktop`
- `PT-BDtool.sh`
- `ptbd-gui`
- `ptbd-gui.py`
- `ptbd`
- `ptbd-remote.sh`
- `ptbd-start.sh`

说明：

- `PT-BDtool.sh` 是 Linux 现在统一的双击启动脚本名
- GitHub Release 里的 Linux 便携包也应该统一用这个名字
- 不要再把旧名字 `启动PT-BDtool.sh` 加回来

### 文档

- `README.md`
  - 只写给普通用户
- `docs/README.en.md`
  - 英文说明
- `docs/DEVELOPMENT.md`
  - 给维护者看

### GitHub 配置

- `.github/workflows/controller-build.yml`
  - 构建 Windows / macOS / Linux 便携包并发布到 `portable-latest`
- `.github/workflows/bundle-release.yml`
  - 生成 Linux bundle 资产
- `.github/workflows/ci.yml`
  - 语法、回归、离线安装检查

### 本地产物

下面这些都应该继续视为本地产物，不要当源码：

- `bdtool-output/`
- `.tmp/`
- `.full-test*`
- `dist/`
- `build/`
- `artifact/`
- `release-assets/`

---

## 4. 普通用户交付原则

不要把源码仓库直接发给小白。

正确交付方式是：

- Windows：发 `PT-BDtool.exe` 或 `PT-BDtool-windows-portable.zip`
- macOS：发 `PT-BDtool.app` 或 `PT-BDtool-macos-portable.zip`
- Linux：发 `PT-BDtool-linux-portable.tar.gz`
- 最好直接发 GitHub `portable-latest` Release 页面

也就是：

- 用户下载压缩包
- 解压
- 双击
- 填 VPS 信息
- 开始处理

---

## 5. 本地开发常用命令

### 语法与基础检查

```bash
python3 -m py_compile ptbd-gui.py ptbd_remote_backend.py scripts/build-controller-app.py scripts/remote-upload-server.py
```

### 全量回归

```bash
./scripts/full-test.sh
```

### GUI 自检

```bash
ptbd-gui --self-check
```

### 控制端打包

```bash
python3 scripts/ensure-bundle.py
python3 scripts/build-controller-app.py
```

如果本地已经有 `third_party/bundle/linux-amd64`，`ensure-bundle.py` 会直接复用，不会重复下载。

---

## 6. Windows / macOS 成品打包

### Windows

在 Windows 本机执行：

```bash
python -m pip install --upgrade pip
python scripts/build-controller-app.py
```

产物默认在：

```text
dist/controller-app/windows/PT-BDtool.exe
```

### macOS

在 macOS 本机执行：

```bash
python3 -m pip install --upgrade pip
python3 scripts/build-controller-app.py
```

产物默认在：

```text
dist/controller-app/macos/PT-BDtool.app
```

---

## 7. GitHub Actions

### 控制端构建

工作流：

- `.github/workflows/controller-build.yml`

作用：

- 自动构建 Windows 控制端
- 自动构建 macOS 控制端
- 自动构建 Linux 控制端
- 自动发布到 `portable-latest` Release

### Linux bundle 资产

工作流：

- `.github/workflows/bundle-release.yml`

作用：

- 生成 `PT-BDtool-linux-amd64.tar.gz`
- 发布 / 更新 `bundle-latest` Release 资产
- 供源码仓库按需下载和控制端打包复用

### 常规回归

工作流：

- `.github/workflows/ci.yml`

作用：

- shell 语法检查
- `scripts/full-test.sh`
- 离线 bundle 构建
- 离线安装检查

---

## 8. 目前还没解决到 100% 的点

下面这些目前仍然不能承诺 100%：

- 任何空白 VPS 都免配置
- 任何极老 Linux 都兼容
- 没有 `root` / `sudo`、没有网络、软件源坏掉的 VPS 还能自动装依赖

当前更现实的目标是：

- 小白只面对成品包，不面对源码
- Windows / macOS 控制端尽量做到双击可用
- Debian / Ubuntu / Alpine 尽量自动补依赖

---

## 9. 维护建议

如果后面还想继续维护这个思路，优先记住这两件事：

1. `README.md` 继续只写给普通用户看
2. Linux bundle 通过 Release 资产更新，不要再把 200MB+ 二进制直接塞回主分支

不要再把开发说明塞回 `README.md`，不然小白还是会看懵。
