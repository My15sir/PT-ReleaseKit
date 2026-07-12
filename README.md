# PT-BDtool

PT-BDtool 是一个给 PT 用户整理发种素材的小工具。

它会尽量把这几步串起来：

1. 连接你的 VPS
2. 扫描视频 / 音频 / `BDMV` / `ISO`
3. 生成截图和媒体信息
4. 打包结果
5. 下载回你的电脑
6. 按设置清理 VPS 上这次生成的临时结果

## 先分清两种用法

仓库导航：

- 维护者可先看 `docs/REPO-INDEX.md`

### 1. 普通用户

如果你只是想直接用，**不要把当前源码仓库当成成品包**。

请去发布页下载便携版：

- `https://github.com/My15sir/PT-BDtool/releases/tag/portable-latest`

按系统下载：

- Windows: `PT-BDtool-windows-portable.zip`
- macOS: `PT-BDtool-macos-portable.zip`
- Linux: `PT-BDtool-linux-portable.tar.gz`

发布包里才会有：

- Windows 的 `PT-BDtool.exe`
- macOS 的 `PT-BDtool.app`
- Linux 适合双击的启动文件

### 2. 当前这个仓库

当前仓库是源码目录，不是 Windows / macOS 成品目录。

所以你在这里看不到：

- `PT-BDtool.exe`
- `PT-BDtool.app`

如果你是在当前源码目录里直接运行，请按下面的“源码仓库怎么跑”操作。

## 这个项目真实怎么运行

### 实际入口

- Linux 图形入口：`./PT-BDtool.sh`
- 通用 GUI 包装：`./ptbd-gui`
- 本机 Web 控制端：`./ptbd-web`
- 新手模式入口：`./ptbd`
- 远端 shell 流程：`./ptbd-remote.sh`
- 本地 CLI 菜单：`./ptbd-start.sh`

### 实际本地依赖

源码直跑时，至少需要这些：

- `bash`
- `python3`
- `ssh`

如果你要跑图形界面，还需要：

- `tkinter`

说明：

- `paramiko` 有就走内置 Python 后端
- 没有 `paramiko` 时，GUI 会回退旧版 shell 后端
- 所以 `paramiko` 不是必装项，但 `python3` 和 `tkinter` 对源码 GUI 很关键

Linux 上如果 GUI 启动时报缺少 `tkinter`，常见修复是安装 `python3-tk`。

### 远端 VPS 真实依赖

VPS 主流程至少依赖：

- `tar`
- `bash`
- `python3`
- `curl`
- `ffmpeg`
- `ffprobe`
- `mediainfo`

程序会优先尝试在 VPS 上自动安装这些依赖。

当前自动安装优先支持：

- `Debian`
- `Ubuntu`
- `Alpine`

如果自动安装还是不够，才会回退上传内置 Linux 运行包。

## 安装前先准备什么

无论你是用发布包还是源码，至少要准备：

- 一台能 SSH 登录的 VPS
- VPS 上已经放好你要处理的视频 / 音频 / `BDMV` / `ISO`
- VPS 的 IP、端口、账号和密码，或者可用的 SSH 密钥
- 你电脑上的一个结果保存目录

## 普通用户怎么用发布包

### Windows

1. 下载 `PT-BDtool-windows-portable.zip`
2. 解压
3. 双击 `PT-BDtool.exe`

如果被系统拦住：

- 点“更多信息”
- 再点“仍要运行”

### macOS

1. 下载 `PT-BDtool-macos-portable.zip`
2. 解压
3. 双击 `PT-BDtool.app`

如果第一次打不开：

- 右键 `PT-BDtool.app`
- 点一次“打开”
- 按系统提示继续

### Linux

1. 下载 `PT-BDtool-linux-portable.tar.gz`
2. 解压
3. 先双击 `PT-BDtool.desktop`
4. 如果桌面文件不生效，再双击 `PT-BDtool.sh`

如果提示没权限，先在终端执行：

```bash
chmod +x PT-BDtool.sh PT-BDtool.command ptbd-gui ptbd-start.sh
```

## 源码仓库怎么跑

如果你当前就在这个仓库目录里：

### Linux

优先执行：

```bash
./PT-BDtool.sh
```

如果你只想走命令行菜单：

```bash
./ptbd
```

如果你想在浏览器里操作：

```bash
./ptbd-web --open
```

默认地址是：

```text
http://127.0.0.1:8899/
```

Web 控制端默认只监听本机地址。它会复用远端扫描和生成流程，支持填写 VPS、扫描候选、选择视频 / 音频 / `BDMV` / `ISO`，并启动素材生成。

如果部署在 VPS 并由 Nginx / FileBrowser 反向代理到子路径，可以指定前缀：

```bash
./ptbd-web --host 127.0.0.1 --port 8899 --base-path /ptbd
```

VPS 本机处理模式可通过配置文件或环境变量启用，用于直接扫描当前服务器上的 FileBrowser 根目录：

```bash
PTBD_WEB_MODE=local PTBD_WEB_LOCAL_ROOT=/data/downloads ./ptbd-web --base-path /ptbd
```

### macOS

优先执行：

```bash
./PT-BDtool.command
```

### Windows

优先双击：

- `PT-BDtool.bat`

但要注意：

- 当前源码仓库里没有 `PT-BDtool.exe`
- 所以它会回退到本机 Python 去启动 `ptbd-gui.py`
- 也就是说，Windows 源码直跑需要你自己先装 Python 3

## 第一次打开后怎么填

图形界面里先填这些：

- `VPS 地址`，例如 `root@1.2.3.4`
- `SSH 端口`，一般默认 `22`
- `SSH 密码`，如果你走密钥可以留空
- `本机保存目录`

其他项怎么理解：

- `空白 VPS 自动上传运行包（推荐）`
  - 开着更省心
  - 程序会先尝试远端自动装依赖
  - 还不够时才回退上传运行包
- `扫描白名单`
  - 不懂就留空
  - 留空时默认优先扫描：`/home /root /data /mnt /media /srv`
- `启用全盘扫描（高级）`
  - 默认关闭
  - 只有媒体不在常见目录时再打开
- `成功后自动清理 VPS 生成目录`
  - 建议开启
  - 只清这次生成的结果目录和结果包，不删原始媒体文件

## 主流程真实顺序

正常使用顺序就是：

1. 保存配置
2. 建议先点“测连”，确认 SSH 和依赖状态
3. 点“扫描 VPS 候选”
4. 等程序连上 VPS
5. 等程序检测远端系统和依赖
6. 在候选列表勾选条目后点“启动”
7. 等程序在 VPS 上生成截图和媒体信息
8. 等程序把结果包下载回本机，并在日志看到明确成功/失败汇总
9. 如有失败项，可点“重试失败”
10. 如果开启了自动清理，再清理 VPS 上这次生成的临时目录

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

GUI 里可以直接点：

- “打开日志文件”

### 2. Linux 双击没反应

先试：

```bash
chmod +x PT-BDtool.sh PT-BDtool.command ptbd-gui ptbd-start.sh
./PT-BDtool.sh
```

如果提示缺少 `tkinter`，先装 `python3-tk` 再试。

### 3. Windows 源码仓库双击没反应

重点确认：

- 你现在用的是源码仓库，不是发布包
- 本机已经安装 Python 3
- `PT-BDtool.bat` 和 `ptbd-gui.py` 在同一个目录

如果你不想装 Python，就不要跑源码仓库，直接改用发布页的 `PT-BDtool.exe`。

### 4. macOS 源码仓库打不开

重点确认：

- 你运行的是 `PT-BDtool.command`
- 本机已经安装 Python 3
- 当前目录里确实有 `ptbd-gui`

如果你只想双击即用，优先下载发布页里的 `PT-BDtool.app`。

### 5. VPS 依赖装不上

重点看 VPS 自己的问题：

- 软件源是否可用
- 网络是否可用
- 当前账号是否有权限装包

当前优先适配的是：

- `Debian`
- `Ubuntu`
- `Alpine`

其他发行版不保证自动安装一定成功。

### 6. 扫描不到你要的文件

先确认：

- 源文件确实已经放到 VPS 上
- 当前账号对目录有读取权限
- 你没有把白名单写错
- 你没有把目标目录误加进排除列表

## 这项目现在更适合谁

适合：

- 已经有 VPS
- 知道 SSH 是什么
- 想把扫描、截图、媒体信息、下载、清理串起来的人

不适合：

- 完全没有 SSH / VPS 使用基础
- 希望任何系统都 100% 免配置即开即用的人

## 给维护者

维护说明看：

- `docs/DEVELOPMENT.md`
- `docs/README.en.md`
