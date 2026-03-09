# PT-BDtool

PT-BDtool 是给 PT 用户整理发种素材用的小工具。  
它会在 VPS 上扫描视频、音频、Blu-ray 原盘目录 `BDMV`、Blu-ray 镜像 `ISO`，自动生成截图、媒体信息，并把结果下载回你的电脑。

## 普通用户只看这里

**不要下载源码。**  
直接去这里下载成品：

- `https://github.com/My15sir/PT-BDtool/releases/tag/portable-latest`

按你的系统选一个：

- Windows：`PT-BDtool-windows-portable.zip`
- macOS：`PT-BDtool-macos-portable.zip`

## 你先准备

- 一台 VPS
- VPS 地址
- VPS 端口
- VPS 密码
- VPS 上已经有视频 / 音频 / `BDMV` / `ISO`

推荐 VPS 系统：

- `Debian`
- `Ubuntu`
- `Alpine`

## 3 步上手

### 第 1 步：下载并打开

- 下载上面的压缩包
- 解压
- Windows 双击 `PT-BDtool.exe`
- macOS 双击 `PT-BDtool.app`

### 第 2 步：填信息

打开后填这 4 项：

- VPS 地址
- SSH 端口
- SSH 密码
- 本机保存目录

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

## 常见卡点

### Windows 被拦住

点：

```text
更多信息 → 仍要运行
```

### macOS 被拦住

右键 `PT-BDtool.app`，点一次：

```text
打开
```

### 扫不到文件

先确认文件真的在 VPS 上。  
如果你不懂“扫描白名单”，就留空。

### 能不能保证任何空白 VPS 都 100% 免配置

不能保证 100%。  
目前优先支持 `Debian` / `Ubuntu` / `Alpine`，会先自动补依赖，不行再回退运行包。

## 给维护者

如果你是维护者，不要把源码仓库发给小白，直接发 `portable-latest` 成品包。  
开发和打包说明看 `DEVELOPMENT.md`。
