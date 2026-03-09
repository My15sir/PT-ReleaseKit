# PT-BDtool

PT-BDtool 是一个给 PT 用户整理发种素材用的“媒体信息打包工具”。  
它会把视频、音频、Blu-ray 原盘目录 `BDMV`、Blu-ray 镜像 `ISO` 处理成一个更适合整理、发帖、保存的结果包。

## 当前最新状态（2026-03）

这不是旧版 README 了，当前项目已经补上了下面这些新能力：

- **Windows 控制端**：支持打成真正免安装的单文件 `PT-BDtool.exe`
- **macOS 控制端**：支持打成真正免安装的 `PT-BDtool.app`
- **Windows 便携配置**：优先跟着 `PT-BDtool.exe` 同目录保存
- **macOS 便携配置**：优先跟着 `PT-BDtool.app` 同级目录保存
- **GitHub Actions 自动打包**：推送 `main` 后会自动构建 Windows / macOS 控制端产物
- **VPS 自动依赖检测**：`Debian` / `Ubuntu` / `Alpine` 会优先自动检测并安装依赖
- **远端主流程**：已支持 扫描 → 选择条目 → 生成 → 下载到本机 → 清理 VPS 输出

如果你现在只关心“这项目到底能不能给小白用”，先看这几段：

- `4.1）Windows / macOS 真正免安装独立版`
- `4.2）怎么发布 Windows / macOS 控制端`
- `6）双击后没反应，或者提示缺少 bash / ssh / Python`

处理完成后，常见输出大概是这样：

- 视频：`mediainfo.txt` + `1.png` 到 `6.png`
- 音频：`mediainfo.txt` + `频谱图.png`
- 原盘 / ISO：`BDInfo.txt` + `1.png` 到 `6.png`

如果你是第一次接触这个项目，建议直接按下面的 **新手最快上手** 走，不要先自己猜。

## 新手最快上手

### 1）先确认你在哪种环境

最常见的是这两种：

- **本地电脑直接跑**：生成结果后直接保存在你当前电脑
- **VPS / 远程 Linux 跑**：生成结果后先保存在 VPS，再自己下载回来

如果你不确定，先按“本地电脑直接跑”理解即可。

### 2）安装

新手更推荐这样装：

```bash
bash install.sh --offline --no-launch
export PATH="$HOME/.local/bin:$PATH"
hash -r
```

说明：
- `--no-launch`：先只安装，不要一装完马上跳进菜单，方便你先检查命令是否正常
- `export PATH=...`：让当前终端立刻能找到新装好的命令
- `hash -r`：让当前 shell 刷新命令缓存，避免还指向旧版

补充说明：
- **这套 `install.sh` 主要面向 Linux 本机处理 / Linux VPS**
- **如果你是 Windows 或 macOS，只想“本机控制 VPS → 结果回到本机”，更建议直接看下面的 GUI / 双击用法**
- **现在已经支持把控制端打成真正免安装的独立应用**：Windows 用 `PT-BDtool.exe`，macOS 用 `PT-BDtool.app`
- Windows / macOS 当前更适合当“控制端”；真正的媒体处理更推荐放在 Debian / Ubuntu / Alpine VPS 上
- 现在空白 VPS 会优先尝试**自动检测系统并自动安装依赖**，推荐系统是：`Debian`、`Ubuntu`、`Alpine`

安装完成后，先检查下面两个命令：

```bash
ptbd --help
bdtool status
```

如果这两个都正常，再继续往下走。  
如果你还想确认 GUI 入口是否能找到脚本，也可以额外执行：

```bash
ptbd-gui --self-check
```

### 3）建议把 PATH 永久写进去

很多新手第一次能用，重开终端后又提示“找不到 pt”或者“找不到 ptbd”。  
这是因为你刚才的 `export PATH=...` 只对当前终端生效。

如果你用的是 `bash`，建议执行：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

如果你用的是 `zsh`，把上面的 `~/.bashrc` 改成 `~/.zshrc`。

### 4）启动

现在推荐把 **`ptbd` 当成唯一主入口** 来理解。

最简单的启动方式：

```bash
ptbd
```

说明：
- `ptbd`：小白主入口，会根据你的配置自动进入本机模式或 VPS 模式
- `pt` / `bdtool`：保留给旧用法和高级用户
- `pt --help` / `bdtool --help`：显示命令帮助

如果你是“**本机控制 VPS，结果自动回到本机桌面**”这个场景，推荐直接用：

```bash
ptbd --setup
```

第一次运行 `--setup` 后，按提示填好：
- VPS 地址
- SSH 端口
- 密码或密钥模式
- 默认扫描目录（**不确定就留空**）
- 本机保存目录

说明：
- 默认扫描目录留空时，会自动优先扫描这些常见目录：`/home /root /data /mnt /media /srv`
- 只有你明确知道媒体都在某个目录时，才建议手动填白名单
- 如果 VPS 是 `Debian` / `Ubuntu` / `Alpine`，空白机首次启动会先尝试自动安装 `bash`、`python3`、`curl`、`ffmpeg`、`mediainfo` 等依赖

配置完成后，以后直接运行：

```bash
ptbd
```

如果你更希望用一个更适合双击的入口，也可以用：

```bash
ptbd-start
```

它会自动做几件事：
- 在你本机临时启动接收服务
- 自动建立到 VPS 的回传通道
- 打开 VPS 上的 `pt` 菜单
- 你只需要在菜单里选要处理的条目
- 处理完成后自动回传到你本机桌面
- 默认自动清理 VPS 上本次生成目录

### 4.1）Windows / macOS 真正免安装独立版

如果你是给别人发“控制端成品”，现在推荐直接发：

- **Windows**：`PT-BDtool.exe`
- **macOS**：`PT-BDtool.app`

这两个独立包的目标就是：

- 本机**不需要再装 Python**
- 本机**不需要再装 Git for Windows**
- 本机**不需要再装 bash / ssh / scp**
- 用户打开应用后，直接填 VPS 信息、扫描、双击条目、下载结果

现在 Windows 独立版还额外做了这件事：

- **优先把配置保存到 `PT-BDtool.exe` 同目录**
- 如果你把 `exe` 放在只读目录，例如 `Program Files`，才会自动回退到 `%APPDATA%`

macOS 独立版现在也做了类似处理：

- **优先把配置保存到 `PT-BDtool.app` 同级目录**
- 如果你把 `.app` 放在不可写位置，才会自动回退到 `~/Library/Application Support`

所以如果你想要更接近真正绿色便携版，建议把 `PT-BDtool.exe` 放在：

- 你自己建的普通文件夹
- 移动硬盘
- U 盘

如果是 macOS，建议把 `PT-BDtool.app` 放在：

- 你自己建的普通文件夹
- 移动硬盘
- U 盘

独立版控制端现在内置了 SSH 连接和结果下载逻辑，主流程会这样走：

1. 连接 VPS
2. 扫描候选媒体
3. 你双击一个条目
4. 远端自动生成信息图 / 媒体信息
5. 结果自动下载回你本机选定目录
6. 默认自动清理 VPS 上这次生成的输出目录

如果你要自己构建独立版，请在**对应系统本机**执行：

```bash
python3 scripts/build-controller-app.py
```

构建结果默认在：

- Windows：`dist/controller-app/windows/PT-BDtool.exe`
- macOS：`dist/controller-app/macos/PT-BDtool.app`

补充说明：

- **Windows 的 `.exe` 需要在 Windows 上构建**
- **macOS 的 `.app` 需要在 macOS 上构建**
- Linux 机器**不能直接交叉打出真正可用的 macOS `.app`**
- 第一次打开时，Windows 可能弹出 SmartScreen，macOS 可能弹出 Gatekeeper；这是系统拦截，不是程序没打包成功

### 4.2）怎么发布 Windows / macOS 控制端

如果你现在要给别人发独立控制端，按这个最短流程走：

#### Windows 发布

在 **Windows 本机** 运行：

```bash
python -m pip install --upgrade pip
python scripts/build-controller-app.py
```

产物默认在：

```text
dist/controller-app/windows/PT-BDtool.exe
```

发包前至少确认：

1. 双击 `PT-BDtool.exe` 能打开
2. 能保存配置
3. 能连接 VPS
4. 能扫出候选
5. 能处理一个样本
6. 结果能回到本机保存目录

建议最终发一个压缩包，例如：

```text
PT-BDtool-windows-x64.zip
```

里面至少放：

- `PT-BDtool.exe`
- `README.md`

#### macOS 发布

在 **macOS 本机** 运行：

```bash
python3 -m pip install --upgrade pip
python3 scripts/build-controller-app.py
```

产物默认在：

```text
dist/controller-app/macos/PT-BDtool.app
```

发包前至少确认：

1. 双击 `PT-BDtool.app` 能打开
2. 第一次被系统拦住时，右键“打开”后能进去
3. 能保存配置
4. 能连接 VPS
5. 能扫出候选
6. 能处理一个样本并把结果拉回本机

建议最终发一个压缩包，例如：

```text
PT-BDtool-macos.zip
```

里面至少放：

- `PT-BDtool.app`
- `README.md`

#### 发版前统一检查

每次准备发版，建议至少过这几项：

1. `./full-test.sh`
2. `python3 ptbd-gui.py --self-check`
3. `python3 scripts/build-controller-app.py`
4. Windows 真机双击验证
5. macOS 真机双击验证
6. Debian / Ubuntu / Alpine 各至少一台 VPS 验证

如果你不想每次手工打包，现在仓库也支持 GitHub Actions 自动构建 Windows / macOS 包。
推到 `main` 或手动触发后，可以直接去 Actions 下载构建产物。

如果你想在 **Windows / macOS / Linux** 上尽量走“图形窗口 + 双击”路线，也可以试试：

```bash
ptbd-gui
```

这是当前的跨平台 GUI MVP，主要做这几件事：
- 先填 VPS 地址、密码、本机保存目录
- 扫描目录留空时，自动优先扫描常见媒体目录
- 可以直接从 VPS 拉候选列表到图形界面里看
- 扫描后可以直接双击候选条目，自动执行“生成 → 回传 → 清理”
- 如果当前只扫到 1 个候选，点击“一步到位启动”会先自动扫描，再直接开跑
- 如果当前有多个候选，“一步到位启动”会先选中第一项，等你双击确认，避免误处理
- 独立版现在内置 SSH 客户端；源码直接跑时，才会回退系统 `ssh` / `bash` 模式
- 空白 VPS 会优先尝试 **Debian / Ubuntu / Alpine 自动装依赖**
- 只有系统依赖还不够时，才会回退到“上传内置运行包”
- 真走回退上传时，第一次可能要传几百 MB；慢的时候等 1～3 分钟都算正常
- 如果日志里出现 `GLIBC_xxx not found` 或 `bundle runtime check failed`，说明这台 VPS 系统太老或和离线包不兼容；这时要么手动在 VPS 安装系统 `ffmpeg` / `mediainfo`，要么换更新的 Linux 发行版

仓库里也附带了几个双击文件：
- `PT-BDtool.bat`：更适合 Windows
- `PT-BDtool.command`：更适合 macOS
- `PT-BDtool.desktop`：更适合 Linux

这 3 个双击文件现在会优先尝试打开已经打好的独立应用；如果旁边没有独立应用，才会回退打开源码版 `ptbd-gui`。

推荐理解成这样：
- **Windows 独立版**：双击 `PT-BDtool.exe`，不需要再装 Python / Git for Windows
- **Windows 独立版**：配置默认优先跟着 `PT-BDtool.exe` 走；如果目录不可写，才回退到 `%APPDATA%`
- **macOS 独立版**：双击 `PT-BDtool.app`，不需要再装 Python / `bash` / `ssh`；如果第一次被系统拦住，先右键“打开”一次
- **macOS 独立版**：配置默认优先跟着 `PT-BDtool.app` 走；如果目录不可写，才回退到 `~/Library/Application Support`
- **Windows 源码版**：双击 `PT-BDtool.bat` 前，还是建议先装好 Python 3；如果要回退旧模式，再装 Git for Windows
- **macOS 源码版**：双击 `PT-BDtool.command` 前，先确认本机有 Python 3、`bash`、`ssh`
- **Linux**：双击 `PT-BDtool.desktop`，或者在应用菜单里启动安装后的 PT-BDtool

双击后的推荐顺序：
1. 填 VPS 地址、端口、密码、本机保存目录
2. 点“扫描 VPS 候选”
3. 双击你要处理的条目
4. 等它自动执行“生成 → 回传 → 清理”

### 5）菜单里怎么走

进入菜单后，按这个顺序走就行：

1. 输入 `1` 开始扫描
2. 选择“全盘扫描”或“扫描指定目录”
3. 等扫描结束
4. 输入你想处理的条目前面的序号
5. 等它自动生成、打包
6. 到提示的结果目录拿包

---

## 最推荐的 3 种用法

### 用法 1：本地电脑直接用

```bash
export PATH="$HOME/.local/bin:$PATH"
pt
```

结果默认保存在当前机器本地。  
如果你的系统能识别桌面目录，通常会优先放在桌面附近的默认结果目录里。

### 用法 2：VPS 上运行，结果先留在 VPS

这是 **最稳、最不容易翻车** 的 VPS 用法：

```bash
export PATH="$HOME/.local/bin:$PATH"
export BDTOOL_DOWNLOAD_DIR="$HOME/PT-BDtool-downloads"
pt
```

处理完成后，结果通常会放到：

```bash
$HOME/PT-BDtool-downloads
```

你再从自己电脑下载：

```bash
scp user@你的VPSIP:$HOME/PT-BDtool-downloads/*.zip .
```

如果打包器没生成 `zip`，也可能是 `tar.gz`。

### 用法 3：不进菜单，直接处理一个文件

如果你已经知道目标文件路径，直接命令模式更快：

```bash
bdtool /path/to/movie.mp4 --out /path/to/output
```

例如：

```bash
bdtool ~/Videos/test.mp4 --out ~/PT-output
```

---

## 真正的启动 / 运行逻辑

这个项目现在推荐这样理解：

- `install.sh`：把程序和离线依赖安装到本机
- `ptbd`：小白主入口
- `ptbd --setup`：首次配置
- `ptbd-start`：双击友好入口
- `ptbd-gui`：跨平台图形启动器 MVP
- `scripts/build-controller-app.py`：把 Windows / macOS 控制端打成独立应用
- `pt` / `bdtool`：旧入口和高级入口
- `bdtool <文件或目录>`：直接走命令模式

## 项目目录说明

为了避免仓库根目录越堆越乱，现在可以把项目简单理解成这几类：

- **主入口**：`ptbd`、`ptbd-start.sh`、`ptbd-gui`、`ptbd-remote.sh`
- **核心处理逻辑**：`bdtool`、`bdtool.sh`
- **公共函数**：`lib/`
- **安装和依赖打包**：`install.sh`、`scripts/`、`third_party/bundle/`
- **双击入口**：`PT-BDtool.bat`、`PT-BDtool.command`、`PT-BDtool.desktop`
- **CI / 回归测试**：`.github/workflows/ci.yml`、`full-test.sh`

下面这些通常都是**运行后自动产生的临时内容**，不属于项目源码，删掉也不会影响功能：

- `bdtool-output/`
- `.tmp/`、`.tmp-fetch-deps/`
- `.full-test*`
- `.rmtest/`
- `__pycache__/`
- `bdtool doctor`：检查依赖
- `bdtool status`：检查安装状态
- `bdtool clean`：清理默认输出目录

也就是说，新手最常用的只有两条：

```bash
bash install.sh --offline
ptbd --setup
```

---

## VPS 用户推荐写法

如果你是在 VPS 上跑，最推荐先用下面这套，不要一上来折腾自动回传：

```bash
bash install.sh --offline
export PATH="$HOME/.local/bin:$PATH"
export BDTOOL_DOWNLOAD_DIR="$HOME/PT-BDtool-downloads"
pt
```

这样做的好处：
- 不依赖桌面环境
- 不依赖自动上传
- 出问题时更容易定位
- 对新手最友好

### VPS 自动装依赖说明

现在 `ptbd` / `ptbd-remote` / `ptbd-gui` 在空白 VPS 上会优先做这件事：

1. 自动识别系统类型
2. 如果是 `Debian` / `Ubuntu` / `Alpine`，先尝试自动安装依赖
3. 依赖够了就直接用系统命令跑
4. 只有系统依赖不够时，才回退上传项目自带运行包

当前自动安装优先补这些：

- `bash`
- `python3`
- `curl`
- `ffmpeg`
- `mediainfo`
- `zip`

说明：

- `ffprobe` 一般跟着 `ffmpeg` 一起提供
- 原盘 / ISO 需要的 `BDInfo` 会尽量复用系统里的 `bd_info`；如果系统里没有，也会尽量降级生成可用报告，而不是直接整单卡死
- 如果 VPS 登录用户既不是 `root`，也没有免密码 `sudo`，那“自动安装依赖”这一步就可能做不到完全自动

### VPS 扫描建议

现在开始，`pt` 在 **SSH / VPS 环境下执行“全盘扫描”** 时，会默认优先只扫描这些目录：

```bash
/home /root /data /mnt /media /srv
```

这样做是为了尽量避开这些常见噪音来源：
- `node_modules`
- `.git`
- `.cache`
- `/var/lib/docker`
- `/proc` `/sys` `/dev` `/run`

如果你想自己指定“只扫哪些目录”，可以这样写：

```bash
export BDTOOL_SCAN_INCLUDE_ROOTS="/home/your-user/Downloads /data/media"
pt
```

如果你还想额外排除一些目录，可以这样写：

```bash
export BDTOOL_SCAN_EXCLUDE_ROOTS="/home/admin/.cache /home/admin/test"
pt
```

说明：
- `BDTOOL_SCAN_INCLUDE_ROOTS`：白名单，多个目录用空格或逗号分隔
- `BDTOOL_SCAN_EXCLUDE_ROOTS`：额外排除目录，多个目录用空格或逗号分隔
- 如果你**不确定**该填什么，直接留空即可，让程序自动优先扫描常见目录
- 如果你已经明确知道媒体都在 `~/Downloads`，强烈建议直接用白名单，速度和结果都会更干净

### 一步到位：本机控制 VPS，自动回传桌面

如果你希望用户安装好后，只要“进入菜单选文件”，后面的步骤都自动完成，推荐用这个命令：

```bash
ptbd --setup
```

配好后，平时直接运行：

```bash
ptbd
```

这个模式下，用户实际只需要做两件事：

1. 在本机运行 `ptbd`
2. 在远端菜单里选要处理的条目

如果你是图形桌面用户，也可以直接运行：

```bash
ptbd-start
```

后面的动作会自动完成：
- 自动生成
- 自动打包
- 自动回传到本机桌面
- 自动清理 VPS 上本次生成目录

默认回传目录：

```bash
~/Desktop
```

如果你想换成本机其他目录：

```bash
ptbd --setup
```

如果你不想进向导，也仍然可以直接一次性写参数：

```bash
ptbd-remote --host root@你的VPSIP --password '你的密码' --scan-include "/home/your-user/Downloads /data/media" --save-dir /你的/保存目录
```

---

## 如果你想“处理完自动回传到本地”

这是高级功能。能用，但建议在基础流程跑通后再配。

通过变量 `BDTOOL_RETURN_MODE` 控制：

- `local`：默认模式，结果保存在当前机器
- `http`：处理完成后自动上传到 HTTP 接收端
- `scp`：处理完成后自动通过 `scp` 回传

### 方案 A：HTTP 自动回传

```bash
export BDTOOL_RETURN_MODE=http
export BDTOOL_RETURN_HTTP_URL='http://127.0.0.1:18080/upload'
pt
```

旧变量 `BDTOOL_CLIENT_UPLOAD_URL` 仍然兼容。

### 方案 B：SCP 自动回传

推荐优先使用 SSH 密钥，不建议新手先用密码模式。

```bash
export BDTOOL_RETURN_MODE=scp
export BDTOOL_RETURN_SCP_HOST='127.0.0.1'
export BDTOOL_RETURN_SCP_PORT='10022'
export BDTOOL_RETURN_SCP_USER='your-local-user'
export BDTOOL_RETURN_SCP_REMOTE_DIR='/home/your-local-user/Downloads/PT-BDtool'
export BDTOOL_RETURN_SCP_IDENTITY_FILE="$HOME/.ssh/id_ed25519"
pt
```

可选变量：
- `BDTOOL_RETURN_SCP_PASSWORD`：只有必须密码认证时才用
- `BDTOOL_RETURN_SCP_STRICT_HOST_KEY_CHECKING`：默认 `accept-new`

说明：
- 如果 VPS 访问不到你的本机，要先做好端口映射或反向隧道
- 如果你不确定怎么配，先别开这个功能，先用“结果保存在 VPS”方案

---

## 常用命令

### 查看帮助

```bash
bdtool --help
```

### 检查依赖

```bash
bdtool doctor
```

### 查看安装状态

```bash
bdtool status
```

### 启动菜单

```bash
pt
```

### 清理默认输出目录

```bash
bdtool clean
```

---

## 直接命令示例

### 处理视频

```bash
bdtool /data/movie.mkv --out /data/output
```

### 处理音频

```bash
bdtool /data/song.flac --out /data/output
```

### 处理整个目录

```bash
bdtool /data/media-dir --out /data/output
```

### 只测试流程，不生成截图和 MediaInfo

```bash
bdtool /data/movie.mkv --mode dry --out /data/output
```

### 打开调试日志

```bash
bdtool /data/movie.mkv --log-level debug --out /data/output
```

---

## 常见报错和排查

### 1）提示 `pt: command not found` / `ptbd: command not found`

通常是 `~/.local/bin` 没在 PATH 里。

先执行：

```bash
export PATH="$HOME/.local/bin:$PATH"
hash -r
ptbd --help
```

如果这样能好，再把 PATH 写进 `~/.bashrc` 或 `~/.zshrc`。

### 2）明明安装过，但命令还是旧路径，或者 `ptbd` 还是不见

这通常是两种情况：

- 你当前终端还缓存着旧命令路径
- 你以前装过旧版，PATH 里还残留旧链接

先执行：

```bash
export PATH="$HOME/.local/bin:$PATH"
hash -r
command -v ptbd
command -v bdtool
command -v pt
```

如果输出不是你刚安装的位置，最稳的做法是回到项目目录重新装一次：

```bash
bash install.sh --offline --no-launch
export PATH="$HOME/.local/bin:$PATH"
hash -r
```

### 3）提示缺少 `ffmpeg` / `mediainfo` / `BDInfo`

先不要乱装，先直接检查：

```bash
bdtool doctor
```

如果依赖不完整，先回到项目目录重新执行：

```bash
bash install.sh --offline
```

### 4）菜单能打开，但扫不到文件

先确认你输入的是目录，不是某个可执行脚本路径。  
支持的主要类型有：

- 视频：`mkv` `mp4` `m2ts` `ts` `avi` `mov`
- 音频：`mp3` `flac` `wav` `m4a` `aac`
- 蓝光：`BDMV` 目录、`iso` 文件

### 5）VPS 上处理完成后不知道结果在哪

建议你显式指定下载目录：

```bash
export BDTOOL_DOWNLOAD_DIR="$HOME/PT-BDtool-downloads"
pt
```

这样结果就统一在 `$HOME/PT-BDtool-downloads` 里。

### 6）双击后没反应，或者提示缺少 `bash` / `ssh` / Python

先分清你打开的是哪一种：

- **独立版**：`PT-BDtool.exe` / `PT-BDtool.app`
- **源码版**：`PT-BDtool.bat` / `PT-BDtool.command` / `ptbd-gui`

如果你用的是 **独立版**：

- 正常情况下**不需要**再装 `bash` / `ssh` / Python
- Windows 第一次可能被 SmartScreen 拦一下
- macOS 第一次可能被 Gatekeeper 拦一下，需要右键“打开”一次

如果你用的是 **源码版**，这通常不是项目本身坏了，而是**控制端前置条件不够**：

- **Windows**：先装 Python 3；如果还在走旧 shell 模式，再装 Git for Windows
- **macOS / Linux**：先确认 `python3`、`bash`、`ssh` 都能在终端里执行

你也可以先跑一次：

```bash
ptbd-gui --self-check
```

先看 `backend=`、`bash=`、`ssh=`、`remote_script=` 这些项是不是正常。

### 7）空白 VPS 自举后，日志里出现 `GLIBC_xxx not found` 或 `bundle runtime check failed`

这不一定是你填错了，而更像是 **VPS 系统版本太老**，和仓库里这份离线运行包不兼容。

先按下面两种思路选一个：

- **稳一点**：直接在 VPS 安装系统依赖，比如 `ffmpeg`、`mediainfo`
- **省事一点**：换一台更新的 Linux x86_64 VPS 再试

如果你当前 VPS 本身已经装好了系统 `ffmpeg` / `mediainfo`，项目会优先回退到系统依赖继续跑。

### 8）为什么它没有自动装好 VPS 依赖

最常见是这几种：

- 这台 VPS 不是 `Debian` / `Ubuntu` / `Alpine`
- 你登录的不是 `root`，而且也没有免密码 `sudo`
- VPS 本身没有网络，或者软件源不可用

这时候项目还是会尽量继续：

- 能用系统现有依赖就继续用
- 不行再尝试回退到内置运行包
- 如果两条路都不通，才会明确报错

---

## 已验证的基本能力

当前仓库里，下面这些流程已经有脚本验证：

- `bdtool --help`
- `bdtool doctor`
- `bdtool status`
- 直接命令模式处理样例视频
- 菜单扫描并生成结果
- VPS 场景下本地保存 / SCP 回传

如果你只是想尽快用起来，照着 README 的安装和启动步骤走即可。

## 结果里通常有什么

### 视频
- `mediainfo.txt`
- `1.png`
- `2.png`
- `3.png`
- `4.png`
- `5.png`
- `6.png`

### 音频
- `mediainfo.txt`
- `频谱图.png`

### 原盘 / ISO
- `BDInfo.txt`
- `1.png`
- `2.png`
- `3.png`
- `4.png`
- `5.png`
- `6.png`

## 卸载

```bash
set -euo pipefail
rm -f "$HOME/.local/bin/bdtool" "$HOME/.local/bin/ptbd" "$HOME/.local/bin/ptbd-gui" \
  "$HOME/.local/bin/ptbd-start" "$HOME/.local/bin/ptbd-remote" "$HOME/.local/bin/ptbd-remote-start" \
  "$HOME/.local/bin/pt" "$HOME/.local/bin/pts" "$HOME/.local/bin/BDInfo"
rm -rf "$HOME/.local/share/pt-bdtool/PT-BDtool-app"
rm -f "$HOME/.local/share/applications/PT-BDtool.desktop" "$HOME/Desktop/PT-BDtool.desktop" "$HOME/桌面/PT-BDtool.desktop" 2>/dev/null || true
rm -f /usr/local/bin/bdtool /usr/local/bin/ptbd /usr/local/bin/ptbd-gui /usr/local/bin/ptbd-start \
  /usr/local/bin/ptbd-remote /usr/local/bin/ptbd-remote-start /usr/local/bin/pt /usr/local/bin/pts /usr/local/bin/BDInfo 2>/dev/null || true
```
