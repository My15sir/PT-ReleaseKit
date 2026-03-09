# PT-BDtool

PT-BDtool 是给 PT 用户整理发种素材用的小工具。  
它会在 VPS 上扫描视频、音频、Blu-ray 原盘目录 `BDMV`、Blu-ray 镜像 `ISO`，自动生成截图、媒体信息，并把结果下载回你的电脑。

## 下载

**不要下载源码，直接下成品包。**

- `https://github.com/My15sir/PT-BDtool/releases/tag/portable-latest`

按你的系统选一个：

- Windows：`PT-BDtool-windows-portable.zip`
- macOS：`PT-BDtool-macos-portable.zip`
- Linux：`PT-BDtool-linux-portable.tar.gz`

说明：

- Windows / macOS / Linux 现在都是轻量便携包
- 如果 VPS 缺少依赖，程序会优先尝试自动安装
- 只有自动安装不够时，才会按需下载 Linux 兜底运行包

## 3 步上手

### 第 1 步：下载并打开

- 下载上面的压缩包
- 解压
- Windows 双击 `PT-BDtool.exe`
- macOS 双击 `PT-BDtool.app`
- Linux 优先双击 `PT-BDtool.desktop`
- 如果桌面文件不生效，再双击 `启动PT-BDtool.sh`

### 第 2 步：填信息

打开后填这 4 项：

- VPS 地址
- SSH 端口
- SSH 密码
- 本机保存目录

VPS 上先放好你要处理的视频 / 音频 / `BDMV` / `ISO`。  
如果你不懂“扫描白名单”，直接留空。

### 第 3 步：扫描并双击

- 点 `扫描 VPS 候选`
- 等它扫出条目
- 双击你要处理的条目

程序会自动：

- 生成截图和媒体信息
- 下载结果回你的电脑
- 清理这次生成的 VPS 输出目录

不会删你原始源文件。

如果系统拦住：

- Windows：`更多信息 → 仍要运行`
- macOS：右键 `PT-BDtool.app` 后点一次 `打开`
- Linux：如果桌面文件不生效，就改双击 `启动PT-BDtool.sh`
- Linux：如果提示无执行权限，先运行 `chmod +x PT-BDtool.desktop 启动PT-BDtool.sh PT-BDtool`
- Linux：第一次真遇到空白 VPS 且系统依赖不够时，可能会额外联网下载兜底运行包
- 如果提示“获取候选失败”，先点程序里的“打开日志文件”，直接看 `PT-BDtool.log`

说明：

- 本项目为 AI 生成项目
- 不接受反馈，不做答疑，不单独适配个别环境
- 当前更优先支持 `Debian` / `Ubuntu` / `Alpine`

维护者再看：

- `docs/DEVELOPMENT.md`
- `docs/README.en.md`
