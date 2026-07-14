# PT-BDtool Docker 部署

Docker 模式用于在**媒体文件所在的 VPS**直接处理文件。容器运行 Web 控制端的 local 模式，扫描宿主机只读挂载的媒体目录，并把生成结果写入宿主机输出目录。

Windows/macOS 桌面 GUI 继续保留，用于从个人电脑控制远端 VPS。Docker 是额外的 VPS 本机处理方式，不是桌面 GUI 的替代品。

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
/srv/ptbd/output        PT-BDtool 生成结果
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

首次启动会在 `/config/config.json` 创建 local 模式配置，文件权限为 `0600`。在 Web 界面中保持“本机模式”，扫描根目录使用 `/media`，保存目录使用 `/output`。

## 5. 使用流程

1. 打开 Web 页面。
2. 确认运行模式为“本机”。
3. 确认本机扫描根目录为 `/media`。
4. 点击扫描。
5. 选择视频、音频、`BDMV` 或 `ISO`。
6. 启动处理并等待任务完成。
7. 在宿主机 `PTBD_OUTPUT_DIR` 中读取结果。

Docker local 模式不需要填写 VPS SSH 地址、端口或密码。浏览器只提交任务，媒体始终留在 VPS 文件系统上。

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
    proxy_set_header Host $host;
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
    proxy_set_header Host $host;
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

容器和镜像不是配置备份。需要备份的是宿主机的 `PTBD_CONFIG_DIR`，生成结果位于 `PTBD_OUTPUT_DIR`。

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

### 配置意外切到 remote 模式

在 Web 界面改回 local 模式，扫描根目录填 `/media`，保存目录填 `/output`。如果配置已损坏，可先停止容器、备份并移走 `PTBD_CONFIG_DIR/config.json`，再启动容器生成新的默认 local 配置。

## 11. 完整卸载

停止并删除容器与网络：

```bash
docker compose down
```

这不会删除 bind mount 指向的宿主目录。确认不再需要后，再由管理员单独处理 `PTBD_OUTPUT_DIR` 和 `PTBD_CONFIG_DIR`；原始媒体目录不属于 PT-BDtool 的卸载范围。
