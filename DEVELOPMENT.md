# PT-BDtool 开发与发布说明

这份文档是给维护者看的，不是给普通用户看的。  
普通用户直接看 `README.md` 就够了。

---

## 1. 仓库为什么看起来文件多

源码本身并不算特别多。  
让仓库显得“臃肿”的主要原因，是这里带了一整套离线运行包：

- `third_party/bundle/linux-amd64/bin`
- `third_party/bundle/linux-amd64/lib`

它的作用是：

- 支持离线安装
- 支持空白 VPS 回退运行
- 减少目标机手动装依赖

所以大部分“文件很多”的感觉，其实来自离线 bundle，不是业务逻辑碎成了一堆小文件。

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

- `full-test.sh`
- `.github/workflows/ci.yml`

---

## 3. 普通用户交付原则

不要把源码仓库直接发给小白。

正确交付方式是：

- Windows：发 `PT-BDtool.exe` 或 `PT-BDtool-windows-portable.zip`
- macOS：发 `PT-BDtool.app` 或 `PT-BDtool-macos-portable.zip`

也就是：

- 用户下载压缩包
- 解压
- 双击
- 填 VPS 信息
- 开始处理

---

## 4. 本地开发常用命令

### 语法与基础检查

```bash
python3 -m py_compile ptbd-gui.py ptbd_remote_backend.py scripts/build-controller-app.py scripts/remote-upload-server.py
```

### 全量回归

```bash
./full-test.sh
```

### GUI 自检

```bash
ptbd-gui --self-check
```

### 控制端打包

```bash
python3 scripts/build-controller-app.py
```

---

## 5. Windows / macOS 成品打包

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

## 6. GitHub Actions

### 控制端构建

工作流：

- `.github/workflows/controller-build.yml`

作用：

- 自动构建 Windows 控制端
- 自动构建 macOS 控制端
- 自动上传可分发产物

### 常规回归

工作流：

- `.github/workflows/ci.yml`

作用：

- shell 语法检查
- `full-test.sh`
- 离线 bundle 构建
- 离线安装检查

---

## 7. 目前还没解决到 100% 的点

下面这些目前仍然不能承诺 100%：

- 任何空白 VPS 都免配置
- 任何极老 Linux 都兼容
- 没有 `root` / `sudo`、没有网络、软件源坏掉的 VPS 还能自动装依赖

当前更现实的目标是：

- 小白只面对成品包，不面对源码
- Windows / macOS 控制端尽量做到双击可用
- Debian / Ubuntu / Alpine 尽量自动补依赖

---

## 8. 维护建议

如果后面还想继续瘦身，优先做这两件事：

1. 把离线 bundle 从源码仓库搬到 Release 资产
2. 继续保持 `README.md` 只写给普通用户看

不要再把开发说明塞回 `README.md`，不然小白还是会看懵。
