# 单机部署与运维

## 网络与入口

控制面通过 Docker socket 创建会话容器，拥有其连接的 daemon 管理权限。使用独立主机或专用的 rootless daemon，socket 所属组只授予服务身份。沙箱使用独立内网、非 root UID、只读根文件系统、去除 Linux capabilities、独立临时目录与持久卷。普通 HTTP(S) 流量走每会话代理，代理检查目标地址并阻止内网、回环和链路本地目标。可使用 `uv pip install` 向会话环境安装第三方包。基础镜像预装 NumPy、SciPy、Pandas、Matplotlib、Pillow、openpyxl、trimesh 和 uv。

将浏览器端域名指向已配置 TLS 的反向代理，示例 Caddy 配置如下。把示例域名换成部署域名，同时配置相同的 `FAMSUN_PUBLIC_ORIGIN`。API 自行比对 Origin，入口代理应保留 Host，部署默认按连接对端限速。若多位用户经过同一个代理，他们会共享该入口的登录速率额度；需要按客户端 IP 限速时，在可信边缘实施限流，并让 Uvicorn 仅接受固定代理地址的转发头。

```caddy
agent.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

企业代理环境可设置 `FAMSUN_CA_FILE` 为受信任的公开根证书，再用 `docker compose -f compose.yaml -f deploy/corporate-ca.compose.yaml --profile build build sandbox-image helper-image app` 构建。按网络要求设置 `FAMSUN_EGRESS_UPSTREAM`，指向出口代理可以访问的企业 HTTP 代理。保持证书验证开启。

## 容量与隔离

- `FAMSUN_MAX_PARALLEL_RUNS` 默认 4，同时运行的任务共享模型额度与主机能力。每会话最多一个进行中的任务。对繁忙的服务，按模型吞吐、CPU 和峰值内存调整并发。
- `FAMSUN_RUN_TIMEOUT` 默认 900 秒，`FAMSUN_SANDBOX_TIMEOUT` 默认 120 秒。图的单轮步数上限由 `FAMSUN_GRAPH_RECURSION_LIMIT` 配置，默认 160，包含 LangGraph 中间件步骤。SQLite 使用 WAL、本地磁盘和单 API 进程；指向同一个数据目录的第二个进程会拒绝启动。
- 使用 `docker info` 和 `python -m famsun.doctor --strict` 检查 cgroup。在 rootless daemon 上应将控制器委派给运行 Docker 的服务账号，并在一个实际会话内确认 `cpu.max`、`memory.max` 和 `pids.max`。测试专用开关 `FAMSUN_ALLOW_UNENFORCED_CGROUPS=true` 允许在控制器缺失的主机进行功能测试。
- 工作区及安装环境使用持久命名卷。部署者需给卷配置容量预算、告警和保留策略。可以通过 `FAMSUN_VOLUME_DRIVER` 和 `FAMSUN_VOLUME_OPTIONS`（字符串键值的 JSON 对象）传入支持持久卷配额的存储驱动设置。驱动必须按卷名分配独立空间。将所有卷指向相同的宿主目录或 NFS 子目录会破坏租户隔离。选项只用于新建卷。Docker 的默认 local 驱动按宿主文件系统可用空间增长。宿主宜采用支持写时复制的存储驱动；vfs 会为并发容器复制文件系统，验收期间需要额外的临时磁盘空间。
- 默认使用流式 Chat Completions 以接收长工具调用。仅支持非流式的第三方端点可设置 `FAMSUN_LLM_STREAMING=false`。模型密钥仅在控制面进程环境中可见。模型提供方收到对话和工具结果，选择端点时应符合企业对工程数据的处理要求。工作区和数据库均应使用受访问控制的加密存储。用户会话 cookie 的默认有效期为七天。
- API 账户使用 Argon2id；离职或权限撤销应由管理者处理。生产关闭开放注册。文件读取有权限、类型和大小检查，浏览器模型预览只接受自包含 GLB。单文件传输上限为 32 MiB。

## 备份、恢复与更新

1. 在维护窗口停止接收新任务，等待进行中的任务结束，再运行 `docker compose stop app`。此时控制面 SQLite 主库、检查点库及会话卷处于一致的停写状态。
2. 使用卷驱动的快照/备份工具备份 Compose 的 `famsun-data` 卷（包括所有 WAL 文件）以及专属 daemon 上 `docker volume ls --filter label=io.famsun.sandbox=v1` 列出的工作区卷、环境卷。保存卷名、驱动选项、镜像摘要和代码提交号。备份必须保留 Unix UID/GID 及读写权限。
3. 在隔离的恢复主机上创建同名卷，恢复这些快照和镜像，从密钥系统重新注入服务端配置。启动一个 API worker，运行 `doctor --strict --live-model`，然后以验收账号登录历史会话，核对产物哈希并继续一次对话。中断中的历史运行将被标记为失败，后台会重新清理其容器。
4. 升级前完成备份并保留镜像，重建沙箱、服务端及前端。新运行使用新镜像，工作区与个人 Python 安装环境随卷保留。升级 Python ABI 或大版本依赖时，应在恢复副本中验证已有环境卷与新镜像的兼容性。

监控健康检查、任务失败率/耗时、模型供应商错误、Docker 孤儿资源、剩余磁盘与 inode。排查任务时从页面运行编号关联服务日志、SQLite 事件与模型工具状态。周期性恢复演练、容量测量和真实业务模型基准应在目标主机上执行。
