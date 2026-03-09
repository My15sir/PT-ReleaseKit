# PT-BDtool 控制端发布清单

这份清单只管 **Windows / macOS 控制端独立应用**。  
目标是让用户拿到包后，直接打开应用，填 VPS 信息，扫描，双击条目，结果回到本机。

---

## 1. 发布前先确认

发布前至少确认这几件事：

- `README.md` 已同步更新
- `ptbd-gui.py`、`ptbd_remote_backend.py`、`scripts/build-controller-app.py` 已是最新
- Debian / Ubuntu VPS 主流程已跑通
- 本地 `./full-test.sh` 通过
- 独立控制端至少做过一次打包自检

如果这几项没过，不建议发给别人。

---

## 2. 发布包清单

### Windows 要发什么

建议最终发这几个文件：

- `PT-BDtool.exe`
- `README.md`
- 一份简单的“第一次怎么填 VPS”说明

如果你想保守一点，也可以附带：

- `PT-BDtool.bat`

但对普通用户来说，真正要点开的还是：

```text
PT-BDtool.exe
```

### macOS 要发什么

建议最终发这几个文件：

- `PT-BDtool.app`
- `README.md`
- 一份简单的“第一次被系统拦住怎么右键打开”的说明

如果你想保守一点，也可以附带：

- `PT-BDtool.command`

但对普通用户来说，真正要点开的还是：

```text
PT-BDtool.app
```

---

## 3. Windows 发布步骤

下面这些步骤要在 **Windows 本机** 执行。

### 第一步：准备环境

安装：

- Python 3

然后进项目目录执行：

```bash
python -m pip install --upgrade pip
python scripts/build-controller-app.py
```

### 第二步：检查产物

打包完成后，看这里：

```text
dist/controller-app/windows/PT-BDtool.exe
```

### 第三步：做最小验证

至少验证：

1. 双击 `PT-BDtool.exe` 能打开窗口
2. 能保存配置
3. 能连接 VPS
4. 能扫出候选
5. 能处理一个样本
6. 结果能回到本机保存目录

### 第四步：给用户打包

建议最终发一个压缩包，例如：

```text
PT-BDtool-windows-x64.zip
```

压缩包里至少放：

- `PT-BDtool.exe`
- `README.md`

---

## 4. macOS 发布步骤

下面这些步骤要在 **macOS 本机** 执行。

### 第一步：准备环境

安装：

- Python 3

然后进项目目录执行：

```bash
python3 -m pip install --upgrade pip
python3 scripts/build-controller-app.py
```

### 第二步：检查产物

打包完成后，看这里：

```text
dist/controller-app/macos/PT-BDtool.app
```

### 第三步：做最小验证

至少验证：

1. 双击 `PT-BDtool.app` 能打开窗口
2. 第一次被系统拦住时，右键“打开”后能正常进入
3. 能保存配置
4. 能连接 VPS
5. 能扫出候选
6. 能处理一个样本并把结果拉回本机

### 第四步：给用户打包

建议最终发一个压缩包，例如：

```text
PT-BDtool-macos.zip
```

压缩包里至少放：

- `PT-BDtool.app`
- `README.md`

---

## 5. 推荐发版自测清单

每次准备发版，建议至少过这 6 项：

1. `./full-test.sh`
2. `python3 ptbd-gui.py --self-check`
3. Linux 打包烟测：`python3 scripts/build-controller-app.py`
4. Windows 真机双击验证
5. macOS 真机双击验证
6. Debian / Ubuntu / Alpine 各至少一台 VPS 验证

---

## 6. 当前已知限制

现在还不能保证：

- **任何空白 VPS 都 100% 免配置**
- Linux 一台机器直接交叉打出真正可用的 macOS `.app`
- 没有 `root` / `sudo`、没有网络、软件源失效的 VPS 还能自动装依赖

当前更现实的发布口径是：

- **Windows / macOS 控制端可以做成真正免安装独立应用**
- **Debian / Ubuntu / Alpine 的 VPS 会优先自动检测依赖并尽量自动安装**
- 如果远端条件太差，程序会明确报错，不再静默失败

---

## 7. 发布时建议附带给用户的话

可以直接用这段白话文：

```text
打开 PT-BDtool 后，只需要填 VPS 地址、端口、密码和本机保存目录。
然后点“扫描 VPS 候选”，双击你要处理的条目即可。
处理完成后，结果会自动下载回你的电脑。
如果第一次打开被系统拦住，按系统提示允许一次再打开即可。
```
