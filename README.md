# PT-BDtool

PT-BDtool 是一个给 PT 用户整理发种素材的小工具。

它做的事很直接：

- 连到你的 VPS
- 扫描视频、音频、`BDMV`、`ISO`
- 自动生成截图和媒体信息
- 把结果打包下载回你的电脑
- 按设置自动清理 VPS 上这次生成的临时结果

你如果只是想用，**不要下载源码，直接下载成品便携包**。

## 下载

发布页：

- `https://github.com/My15sir/PT-BDtool/releases/tag/portable-latest`

按系统下载：

- Windows：`PT-BDtool-windows-portable.zip`
- macOS：`PT-BDtool-macos-portable.zip`
- Linux：`PT-BDtool-linux-portable.tar.gz`

## 安装前先准备

先准备好这几样：

- 一台能 SSH 登录的 VPS
- VPS 上已经放好你要处理的视频 / 音频 / `BDMV` / `ISO`
- VPS 的 IP、端口、密码
- 电脑上一个你想保存结果的目录

说明：

- 远端 VPS 现在优先支持 `Debian` / `Ubuntu` / `Alpine`
- 程序会先尝试自动安装远端依赖
- 只有自动安装不够时，才会上传或下载 Linux 兜底运行包

## Windows 怎么用

1. 下载 `PT-BDtool-windows-portable.zip`
2. 解压
3. 双击 `PT-BDtool.exe`

如果被系统拦住：

- 点 `更多信息`
- 再点 `仍要运行`

## macOS 怎么用

1. 下载 `PT-BDtool-macos-portable.zip`
2. 解压
3. 双击 `PT-BDtool.app`

如果第一次打不开：

- 右键 `PT-BDtool.app`
- 点一次 `打开`
- 按系统提示继续

## Linux 怎么用

1. 下载 `PT-BDtool-linux-portable.tar.gz`
2. 解压
3. 优先双击 `PT-BDtool.desktop`
4. 如果桌面文件不生效，再双击 `启动PT-BDtool.sh`

如果提示没权限，先在终端执行：

```bash
chmod +x PT-BDtool.desktop 启动PT-BDtool.sh PT-BDtool
```

Linux 便携版说明：

- 配置文件优先保存在程序同目录
- 日志文件也优先保存在程序同目录
- 常见文件名就是 `PT-BDtool-config.json` 和 `PT-BDtool.log`

## 第一次打开后怎么配置

打开程序后，先填这些：

- VPS 地址，例如 `root@1.2.3.4`
- SSH 端口，默认一般是 `22`
- SSH 密码
- 本机保存目录

其他项怎么理解：

- `空白 VPS 自举`：建议保持开启。程序会先尝试自动装远端依赖。
- `扫描白名单`：不懂就留空。留空时会按默认规则扫描常见目录。
- `自动清理`：建议开启。处理完成后会清理这次生成的临时结果。

填完后，程序会把配置保存起来。便携版优先保存在程序旁边；非便携环境通常保存在用户配置目录。

## 怎么连接 VPS

程序走的是 SSH。

所以你至少要保证：

- VPS 地址能连通
- 端口正确
- 密码正确
- VPS 允许这个账号登录

如果这里填错，后面扫描一定失败。

## 怎么扫描

正常顺序就是：

1. 点 `扫描 VPS 候选`
2. 等程序连上 VPS
3. 等程序检测远端系统和依赖
4. 等候选列表出来

第一次扫描时，程序可能会多做几件事：

- 检查远端是不是 `Debian` / `Ubuntu` / `Alpine`
- 尝试自动安装 `bash`、`curl`、`python3`、`tar`、`ffmpeg`、`ffprobe`、`mediainfo`
- 在 VPS 的 `~/.cache/ptbd-remote` 下准备运行时文件

这一步如果是第一次，通常会比后面几次慢。

## 怎么选择条目并生成结果

扫描结果出来后：

1. 双击你要处理的条目
2. 程序会在 VPS 上生成截图和媒体信息
3. 程序会把结果打包
4. 程序会把打包结果下载回你的电脑

你不需要手动跑命令。

## 文件会下载到哪里

下载到你在界面里填写的“本机保存目录”。

注意：

- 下载回来的通常是 `.zip` 或 `.tar.gz` 结果包
- 不是自动帮你解压到桌面上一堆散文件
- 如果你把保存目录设成桌面，那结果包就会出现在桌面

## 用完后会不会自动清理 VPS

默认会。

清理的是：

- 这次生成的临时输出目录
- 这次待下载的结果包

不会删你原始视频、原始音频、原始 `BDMV`、原始 `ISO`。

如果你不想自动清理，可以把 `自动清理` 关掉。

## 常见报错怎么排查

### 1. 扫描失败 / 获取候选失败

先做这几件事：

1. 点程序里的“打开日志文件”
2. 直接看 `PT-BDtool.log`
3. 确认 SSH 地址、端口、密码有没有填错
4. 确认 VPS 能联网，至少能安装依赖或执行已有依赖

### 2. Linux 双击没反应

先试：

- 双击 `启动PT-BDtool.sh`
- 或先执行一次：

```bash
chmod +x PT-BDtool.desktop 启动PT-BDtool.sh PT-BDtool
```

### 3. VPS 依赖装不上

重点看 VPS 自己的问题：

- 软件源是否可用
- 网络是否可用
- 当前账号是否有权限装包

当前优先适配的是：

- `Debian`
- `Ubuntu`
- `Alpine`

其他发行版不保证自动安装一定成功。

### 4. 扫描不到你要的文件

先确认：

- 源文件确实已经放到 VPS 上
- 账号对这些目录有读取权限
- 你没有把白名单写错
- 你没有把目标目录误加进排除列表

## 现在这项目适合谁

适合：

- 已经有 VPS
- 只是想少敲命令
- 想把截图、媒体信息、下载、清理串成一套流程的人

不适合：

- 完全没有 SSH / VPS 使用基础
- 需要作者远程一对一排障的人

## 说明

- 本项目为 AI 生成项目
- 不接受反馈，不做答疑，不单独适配个别环境

维护者再看：

- `docs/DEVELOPMENT.md`
- `docs/README.en.md`
