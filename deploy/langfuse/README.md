# 本机 Langfuse

这个单机 Compose 运行 Langfuse Web、Worker、PostgreSQL、ClickHouse、Redis 和 MinIO。FAMSUN 控制面通过 Web 的 OTLP 接口上报。模型输入、输出及工具内容默认关闭采集，项目保存在本机。应用使用 Langfuse Python SDK 5.2.0；Web 与 Worker 固定为 4.83.0，并对所有镜像锁定多架构摘要。基础服务版本与上游同代，PostgreSQL、Redis 使用 Alpine 变体。

[官方 4.83.0 发行版本](https://github.com/langfuse/langfuse/releases/tag/v4.83.0) · [该版本官方 Compose](https://github.com/langfuse/langfuse/blob/v4.83.0/docker-compose.yml) · [自托管架构](https://langfuse.com/self-hosting)

## 启动和登录

在仓库根目录执行。需要 Docker Engine、Docker Compose v2.24 或更高版本、Python 3，以及持久化磁盘。此方案在 Compose v2.40.3 验证。六个服务须能使用 Docker 服务名互访。先核对镜像空间、数据库增长预算、可用内存及 FAMSUN 创建并发沙箱的峰值容量。Docker 的 VFS 驱动会重复复制镜像层及容器根目录，容量需求明显增大。

```sh
python3 deploy/langfuse/init.py
cd deploy/langfuse
docker compose --env-file .env config -q
docker compose --env-file .env up -d --wait --wait-timeout 300
curl --fail http://127.0.0.1:3000/api/public/health
```

打开 `http://localhost:3000`，使用 `.env` 的 `LANGFUSE_INIT_USER_EMAIL` 与 `LANGFUSE_INIT_USER_PASSWORD` 登录，选择 `FAMSUN Agent` 项目。部署初始化已创建组织、项目和 API 密钥。重复执行 `init.py` 会沿用原文件。凭据文件权限为 0600，已加入 Git 忽略规则。将其存入自己的密钥管理系统；在已初始化的数据卷旁更改引导变量不会重置数据库内的账户或项目密钥。

Web 是唯一发布的主机端口，缺省绑定 `127.0.0.1:3000`。如有冲突可调整 `.env` 中 `LANGFUSE_PORT`。数据库、Worker 和对象存储位于独立私有网络。FAMSUN 导出器关闭媒体上传。远程浏览器可使用 `ssh -L 3000:127.0.0.1:3000 <host>` 后访问本地 3000 端口。面向团队开放时，在该回环端口前提供自己的 HTTPS 反向代理，并将 `LANGFUSE_PUBLIC_URL` 设为实际入口再重建 Web 与 Worker。

## 接入同机 FAMSUN

先启动 Langfuse 以创建 `famsun-observability` 网络。以下命令在仓库根目录执行，请将 `/secure/famsun.env` 换成服务原有配置，并保留现有部署使用的其他 Compose 覆盖文件。

```sh
docker compose --env-file /secure/famsun.env --env-file deploy/langfuse/.env \
  -f compose.yaml -f deploy/langfuse/famsun.compose.yaml up -d app
```

FAMSUN 保留原有网络，并加入仅与 Langfuse Web 共享的网络。其进程收到 `FAMSUN_LANGFUSE_ENABLED=true`、`LANGFUSE_BASE_URL=http://langfuse-web:3000`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY` 和 `FAMSUN_LANGFUSE_CAPTURE_CONTENT=false`。FAMSUN 执行沙箱无需 Langfuse 密钥。启用企业 HTTP/HTTPS 代理时，集成覆盖文件会在已有 `NO_PROXY` 和 `no_proxy` 名单中追加 `langfuse-web`，让这一条内部 HTTP 连接直达服务。若手工配置容器代理，须保留这一例外。若 FAMSUN 在宿主机直接运行，将 `LANGFUSE_BASE_URL` 改成 `http://127.0.0.1:3000`，并将此地址加入已有的 HTTP 代理绕过列表。

发起一个新任务，例如让智能体用工具核对一笔计算。在项目的 Tracing 页面查看 `FAMSUN Agent`。已启用观测的回合带有独立 trace，用户与会话使用项目密钥派生的标识。以下命令使用已知的 FAMSUN 用户 ID、会话 ID 和运行 ID 核对记录，只打印计数、标识及内容是否存在。

```sh
uv run --locked --env-file deploy/langfuse/.env python -m deploy.langfuse.verify_trace \
  --user "$FAMSUN_USER_ID" --session "$FAMSUN_SESSION_ID" --run "$FAMSUN_RUN_ID"
```

首次启动可能需要几分钟迁移数据库。`docker compose ps` 展示依赖健康状态；如 trace 尚未出现，先确认 Web 接入健康及项目 API 密钥，再检查 Worker 的 ClickHouse、Redis 和 S3 连通性。用 `docker compose logs --tail 100 langfuse-worker` 在受控终端查看。设置 `FAMSUN_LANGFUSE_ENABLED=false` 并重启 FAMSUN 控制面即可关闭新回合导出。

## 已验证的本机回合

2026-11-21，独立的宿主 FAMSUN 进程在 8195 端口使用临时 SQLite、专用演示身份、真实模型和 Docker 沙箱执行了 `1729 × 7`，工具返回 `12103`。本机 Langfuse 查询返回 14 个观测，其中包括两次模型生成与一次工具执行，所有输入输出字段均为空。重启 Web 与 Worker 后通过 v2 观测 API 再次读取同一 trace，并在 Chromium 登录对应项目查看了完整调用树。

此次验收宿主使用 VFS。为控制空间，验收将 Compose 指定的官方 Linux/amd64 镜像按 manifest 校验后，使用合并的 rootfs 和原运行配置加载。部署清单始终引用上游原始多架构 SHA256。该测试宿主的 FAMSUN 执行沙箱使用 `FAMSUN_ALLOW_UNENFORCED_CGROUPS=true`，正式沙箱按主项目运维文档配置 cgroup。验收还用一次性容器加入 `famsun-observability`，核对 Web 的内部 DNS 和健康接口。

[机器可读证据](verification.json) · [Langfuse 调用树截图](../../docs/screenshots/langfuse-local-trace.webp)

## 备份、恢复和升级

1. 维护窗口先暂停 FAMSUN 新任务，等待在途回合结束、观测队列落盘。停止 Langfuse Web 与 Worker，再停止存储服务。`docker compose stop` 会保留数据卷。
2. 为该项目五个命名卷创建同一冷备份集。`postgres-data` 保存账户、项目与设置；`clickhouse-data` 保存观测；`minio-data` 保存事件与对象；`redis-data` 保存队列；`clickhouse-logs` 保存诊断日志。可通过 `docker volume ls --filter label=com.docker.compose.project=famsun-langfuse` 找到实际卷名。使用支持保留数值 UID/GID、权限、符号链接与文件内容的卷快照或备份工具。
3. 将 `.env` 密文、Compose、版本摘要同批备份，并设置凭据访问权限。尤其要保留 `ENCRYPTION_KEY` 和 `SALT`。在隔离的恢复主机上以同名卷恢复所有内容，复原 0600 凭据并启动相同摘要版本，登录后查询一条已知的 trace 检查恢复结果。FAMSUN SQLite 和会话卷执行 FAMSUN 自身的备份流程。
4. 升级前阅读目标 Langfuse 版本迁移说明，在恢复副本上验证 Web 与 Worker 相同版本，核对上游基础存储兼容范围和新镜像摘要。再安排维护窗口更新生产。涉及数据迁移时，回滚使用升级前的整套快照、密钥文件和镜像摘要。

下面是默认项目名的文件级冷备份示例。在 `deploy/langfuse` 下运行，并事先暂停 FAMSUN 新回合。`/mnt/backup` 应是已挂载、加密、有足够容量的**独立**备份磁盘。示例使用已部署 Redis 镜像中的 `tar`，备份文件仅当前系统账号可读。

```sh
set -eu
umask 077
backup_dir="/mnt/backup/famsun-langfuse-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir "$backup_dir"
archive_image=$(docker compose images -q redis)
docker compose stop langfuse-web langfuse-worker
docker compose stop postgres clickhouse redis minio
for name in postgres-data clickhouse-data minio-data redis-data clickhouse-logs; do
  volume="famsun-langfuse_${name}"
  docker volume inspect "$volume" >/dev/null
  docker run --rm --network none --user 0:0 -v "$volume:/source:ro" \
    --entrypoint tar "$archive_image" -C /source -cpf - . >"$backup_dir/$name.tar"
done
cp .env compose.yaml "$backup_dir/"
(cd "$backup_dir" && sha256sum *.tar compose.yaml .env > SHA256SUMS)
docker compose up -d --wait --wait-timeout 300
```

在隔离恢复机上先校验 `sha256sum -c SHA256SUMS`。将保存的 `.env` 与 `compose.yaml` 放入空的部署目录，预取相同摘要镜像。执行 `docker compose create` 创建空卷，然后仅在这些**新卷**上逐卷用相同工具解包，例如 `docker run --rm -i --network none --user 0:0 -v famsun-langfuse_postgres-data:/restore --entrypoint tar "$archive_image" -C /restore -xpf - < postgres-data.tar`。恢复机上的 `archive_image` 取该机已载入的 Redis 镜像 ID。完成所有卷后再 `docker compose up -d --wait`。

## 离线使用

在相同架构的联网准备机预取 Compose 中六个锁定摘要。隔离网络可通过支持 OCI manifest list 的内部 registry 分发。用 `skopeo copy --all --preserve-digests docker://源仓库@摘要 docker://内网仓库/镜像:版本` 逐一镜像，并在本地 Compose 覆盖文件中指定 `内网仓库/镜像@相同摘要`。预先安装 Docker、Compose、项目锁定 Python 依赖，并准备本地 DNS、时钟、CA 证书、FAMSUN 模型端点与所需企业网络策略。镜像拉取完成后可用 `docker compose up -d --pull never` 启动。PostgreSQL、ClickHouse、Redis 和 MinIO 之间只使用本机容器网络。离线采集可用本地测试 trace 验证。
