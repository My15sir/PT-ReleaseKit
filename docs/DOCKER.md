# PT ReleaseKit Docker 部署

Docker 模式用于在**媒体文件所在的 VPS**直接处理文件。容器运行 Web 控制端的 local 模式，扫描宿主机只读挂载的媒体目录，并把生成结果写入宿主机输出目录。

Windows/macOS 桌面 GUI 继续保留，可直接处理个人电脑媒体，也可控制远端 VPS。Docker 是额外的 VPS 本机处理方式，不是桌面 GUI 的替代品。

产品展示名已更新为 PT ReleaseKit；为避免破坏现有 Compose 部署，`pt-bdtool:local` 镜像标签、`PTBD_*` 环境变量和容器内 `/opt/PT-BDtool` 路径继续作为兼容接口保留。

Docker 镜像已包含 `ffmpeg`、`ffprobe`、`mediainfo` 和镜像提供的蓝光处理工具，属于依赖齐备的 local 模式。桌面便携包不包含这些系统媒体工具，两种交付形态不要混淆。

Web 扫描区会把主要空间留给候选结果，并实时显示遍历的目录数、文件数、候选数和当前路径；候选总量确定后切换为真实解析比例。每项都有独立复选框和“仅选此项”操作。扫描进程连续 120 秒无输出时会被终止并返回错误，停止任务也会直接结束对应子进程。

## 1. 前置条件

- Linux VPS 已安装 Docker Engine
- 已安装 `docker compose` 插件
- 媒体文件已位于该 VPS 的本地文件系统或已可靠挂载到该 VPS
- 有两个持久化可写目录用于输出和配置

确认环境：

```bash
docker version
docker compose version
```

## 2. 目录规划

以下路径仅为示例：

```text
/srv/media              原始媒体
/srv/ptbd/output        PT ReleaseKit 生成结果
/srv/ptbd/config        配置与运行状态
```

创建持久化目录：

```bash
sudo mkdir -p /srv/media /srv/ptbd/output /srv/ptbd/config
sudo chown 1000:1000 /srv/ptbd/output /srv/ptbd/config
```

上例使用 Compose 默认身份 `1000:1000`。如需其他非 root UID/GID，请同时修改 `.env` 和 `chown` 参数。容器不会写入 `/media`，不要把输出目录放在媒体只读挂载内部。

## 3. 配置 Compose

可以在仓库根目录创建 `.env`：

```dotenv
PTBD_MEDIA_DIR=/srv/media
PTBD_OUTPUT_DIR=/srv/ptbd/output
PTBD_CONFIG_DIR=/srv/ptbd/config
PTBD_WEB_PORT=8899
PTBD_UID=1000
PTBD_GID=1000
```

如有专用的非 root 宿主账号，可查看它的 ID 并把实际数字写入 `.env`：

```bash
id -u ptbd-user
id -g ptbd-user
```

不要把 `PTBD_UID` 或 `PTBD_GID` 配置为 `0`。

Compose 变量说明：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `PTBD_MEDIA_DIR` | `./media` | 宿主媒体目录，只读挂载到 `/media` |
| `PTBD_OUTPUT_DIR` | `./output` | 宿主输出目录，挂载到 `/output` |
| `PTBD_CONFIG_DIR` | `./config` | 宿主配置目录，挂载到 `/config` |
| `PTBD_WEB_PORT` | `8899` | Web 服务宿主端口 |
| `PTBD_UID` | `1000` | 容器进程 UID |
| `PTBD_GID` | `1000` | 容器进程 GID |
| `PTBD_ALLOW_INSECURE_IMAGE_HOST` | `0` | 是否明确允许非回环图床使用明文 HTTP；默认禁止 |

正常部署不需要设置 `PTBD_ALLOW_INSECURE_IMAGE_HOST`。图床地址只有在 `localhost`、`127.0.0.1`、`[::1]` 等容器回环端点时才默认允许 HTTP，其他地址必须使用 HTTPS。如果明确接受可信内网中的 Token 和截图明文传输风险，可在 `.env` 中设为 `1` 后重建容器；不要用它连接公网 HTTP 图床。

不创建 `.env` 时，也可以只对一次命令传入变量：

```bash
sudo mkdir -p /srv/media /srv/ptbd/output /srv/ptbd/config
sudo chown 1000:1000 /srv/ptbd/output /srv/ptbd/config

PTBD_MEDIA_DIR=/srv/media \
PTBD_OUTPUT_DIR=/srv/ptbd/output \
PTBD_CONFIG_DIR=/srv/ptbd/config \
PTBD_UID=1000 \
PTBD_GID=1000 \
docker compose up -d --build
```

先检查最终 Compose 配置：

```bash
docker compose config
```

重点确认 `/media` 的宿主路径正确且带有 `read_only: true` 或 `:ro`。

## 4. 启动

构建并启动：

```bash
docker compose up -d --build
```

查看状态和健康检查：

```bash
docker compose ps
docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q ptbd)"
```

检查 API：

```bash
curl --fail http://127.0.0.1:8899/api/status
```

Compose 默认只绑定 VPS 的 `127.0.0.1`，不会直接发布到公网。在桌面电脑建立 SSH 隧道：

```bash
ssh -L 8899:127.0.0.1:8899 user@VPS-IP
```

保持该 SSH 会话运行，然后在桌面浏览器访问：

```text
http://127.0.0.1:8899/
```

首次启动会在 `/config/config.json` 创建 local 模式配置，文件权限为 `0600`。在 Web 界面中保持“本机服务器”，媒体根目录使用 `/media`，结果保存目录使用 `/output`。local 模式会自动隐藏 VPS、SSH 和远端自举字段。

容器只扫描 `/media` 和 Web 配置中显式添加的额外根目录，不会遍历 VPS 的 `/root`、`/data`、`/mnt`、`/srv` 或整个根文件系统。需要另一个宿主媒体目录时，应先把它明确只读挂载进容器，再把容器内路径加入扫描目录。

## 5. 使用流程

1. 打开 Web 页面。
2. 确认处理位置为“本机服务器”。
3. 确认媒体根目录为 `/media`。
4. 点击“扫描本机资源”。
5. 用每项复选框选择视频、音频、`BDMV` 或 `ISO`；“仅选此项”会清空其他选择并只保留当前项。
6. 点击“生成发布材料”并等待任务完成；刷新页面会继续显示当前任务。
7. 在宿主机 `PTBD_OUTPUT_DIR` 中读取结果。

Docker local 模式不需要填写 VPS SSH 地址、端口或密码。浏览器只提交任务，原始媒体始终留在 VPS 文件系统上；只有用户明确开启图床时，生成的截图才会发送到所选图床 API。

### 可选图床上传

图床上传默认关闭。开启后可选择 ImgBB、Lsky Pro v2、S.EE/SM.MS 兼容接口或自定义 Bearer API，并填写完整 API 地址（需要时）和 Token。非回环 API 地址默认必须使用 HTTPS；只有明确设置 `PTBD_ALLOW_INSECURE_IMAGE_HOST=1` 才允许可信内网 HTTP。上传在材料包生成到 `/output` 后执行，不参与原始媒体处理。

成功或部分完成后，结果 ZIP 内会追加 `image-host.json`、`image-host-links.txt` 和 `image-host-bbcode.txt`。它们分别记录逐图状态、成功链接和可粘贴的 BBCode。单图或整批上传失败不会删除材料，也不会把已经成功的生成任务改成失败；无法安全更新归档时保留原 ZIP。`.tar.gz` 回退包不会执行图床归档更新。

Docker 使用本机 Python 处理路径，不经过桌面远端模式的 Shell fallback，因此可以正常执行图床后处理。Docker 控制端就运行在媒体 VPS 上，图床 Token 会保存在宿主 `PTBD_CONFIG_DIR/config.json` 对应的容器 `/config/config.json` 中，而不是“回传到另一台电脑后再上传”。该文件以 `0600` 写入，Web 公开配置、任务状态和普通日志不会返回 Token，只返回是否已保存。备份配置目录时按凭据备份处理，并保持 Web 端口的回环绑定或使用有认证的代理。

## 6. 挂载与权限

Compose 使用以下容器挂载：

- `/media`：只读，原始媒体
- `/output`：可写，结果包和生成文件
- `/config`：可写，`config.json` 和 runtime 数据
- `/tmp`：容器 tmpfs，带 `noexec`、`nosuid` 限制

容器默认 UID/GID 为 `1000:1000`。如果 VPS 上目录属于其他非 root 用户，优先把 `PTBD_UID` 和 `PTBD_GID` 设置为目录所有者，而不是给目录开放全局写权限。Compose 不会代替管理员修正 bind 目录权限；必须在首次启动前创建目录并设置所有者。

检查目录数字所有者：

```bash
stat -c '%u:%g %a %n' /srv/media /srv/ptbd/output /srv/ptbd/config
```

检查容器身份：

```bash
docker compose exec ptbd id
```

输出或配置出现 `Permission denied` 时，调整目录所有者或 Compose UID/GID，然后重建容器：

```bash
chown -R 1000:1000 /srv/ptbd/output /srv/ptbd/config
docker compose up -d --force-recreate
```

上例的 `1000:1000` 必须替换成 `.env` 中实际配置的 UID/GID。原始媒体只需允许该身份读取和遍历，不需要写权限。

## 7. 网络与反向代理

`compose.yaml` 将端口固定绑定到 `127.0.0.1:${PTBD_WEB_PORT}`。Web 控制端本身不提供账户认证；远程访问优先使用 SSH 隧道、VPN，或带认证和 TLS 的宿主反向代理。不要为了省略隧道而把 Compose 端口直接改成公网监听。

根路径 Nginx 示例：

```nginx
location / {
    proxy_pass http://127.0.0.1:8899;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

如需发布在 `/ptbd` 子路径，新增 `compose.override.yaml`：

```yaml
services:
  ptbd:
    environment:
      PTBD_WEB_BASE_PATH: /ptbd
```

Nginx 保留该路径转发：

```nginx
location /ptbd/ {
    proxy_pass http://127.0.0.1:8899;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

修改 base path 后重新创建容器，并用带前缀的健康接口检查：

```bash
docker compose up -d --force-recreate
curl --fail http://127.0.0.1:8899/ptbd/api/status
```

## 8. 日志与日常管理

跟随日志：

```bash
docker compose logs -f --tail=200 ptbd
```

重启：

```bash
docker compose restart ptbd
```

停止但保留宿主数据：

```bash
docker compose down
```

容器和镜像不是配置备份。需要备份的是宿主机的 `PTBD_CONFIG_DIR`，生成结果位于 `PTBD_OUTPUT_DIR`。如果启用了图床，配置备份包含 API Token，应使用与其他凭据相同的访问控制。

## 9. 升级与回滚

升级前备份配置目录：

```bash
cp -a /srv/ptbd/config "/srv/ptbd/config.backup.$(date +%Y%m%d-%H%M%S)"
```

拉取代码、更新基础镜像并重建：

```bash
git pull --ff-only
docker compose build --pull
docker compose up -d
docker compose ps
```

升级不会删除宿主的媒体、输出或配置目录。若新版本需要回滚，请切换到上一个已验证的 Git 提交或 Release，重新执行 `docker compose build` 和 `docker compose up -d`；持久化目录继续复用。

## 10. 排障

### 容器无法启动

```bash
docker compose ps -a
docker compose logs --tail=200 ptbd
docker compose config
```

优先检查端口占用、目录权限和 Compose 展开后的宿主路径。

### 健康检查失败

```bash
docker inspect --format '{{json .State.Health}}' "$(docker compose ps -q ptbd)"
docker compose exec ptbd python3 /opt/PT-BDtool/docker/healthcheck.py
```

健康检查访问容器内的 `/api/status`。如果设置了 `PTBD_WEB_BASE_PATH`，该变量必须在容器环境中保持一致。

### 扫描结果为空

检查宿主和容器看到的文件：

```bash
find /srv/media -maxdepth 2 -type f | head
docker compose exec ptbd find /media -maxdepth 2 -type f | head
```

确认：

- `PTBD_MEDIA_DIR` 指向实际媒体目录，而不是空目录
- Web 配置使用 local 模式和 `/media` 根目录
- 容器 UID/GID 对目录和父目录有读取、遍历权限
- 目标扩展名和目录结构属于支持的媒体类型

### 无法写入结果或配置

```bash
docker compose exec ptbd sh -c 'touch /output/.write-test && rm /output/.write-test'
docker compose exec ptbd sh -c 'touch /config/.write-test && rm /config/.write-test'
```

失败时按“挂载与权限”一节修正 UID/GID 或宿主目录所有者。

### 端口冲突

修改 `.env`：

```dotenv
PTBD_WEB_PORT=8900
```

然后执行：

```bash
docker compose up -d --force-recreate
```

### 图床上传失败

先确认图床开关、提供方、API 地址和 Token 已保存，再从任务结果查看逐图错误。Lsky Pro v2 和自定义提供方必须填写完整上传地址；S.EE 使用默认地址，切换 SM.MS 时填写兼容地址。非回环 HTTP 地址会默认拒绝，应优先改为 HTTPS；只有明确接受可信内网明文风险时才设置 `PTBD_ALLOW_INSECURE_IMAGE_HOST=1` 并重建容器。容器还必须能访问对应 API。

图床失败不需要重新生成材料。先确认 `/output` 中原 ZIP 仍然存在；修正配置后可重新处理所选条目。不要在 issue、终端复制或反向代理日志中粘贴 Token，公开 API 不会主动返回它。

### 配置意外切到 remote 模式

在 Web 界面把处理位置改回“本机服务器”，媒体根目录填 `/media`，结果保存目录填 `/output`。如果配置已损坏，可先停止容器、备份并移走 `PTBD_CONFIG_DIR/config.json`，再启动容器生成新的默认 local 配置。

## 11. 完整卸载

停止并删除容器与网络：

```bash
docker compose down
```

这不会删除 bind mount 指向的宿主目录。确认不再需要后，再由管理员单独处理 `PTBD_OUTPUT_DIR` 和 `PTBD_CONFIG_DIR`；原始媒体目录不属于 PT ReleaseKit 的卸载范围。
