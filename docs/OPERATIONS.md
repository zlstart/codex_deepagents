# 单机部署与运维

## 网络与入口

控制面通过 Docker socket 创建会话容器，拥有其连接的 daemon 管理权限。使用独立主机或专用的 rootless daemon，socket 所属组只授予服务身份。沙箱使用独立内网、非 root UID、只读根文件系统、去除 Linux capabilities、独立临时目录与持久卷。普通 HTTP(S) 流量走每会话代理，代理检查目标地址并阻止内网、回环和链路本地目标。可使用 `uv pip install` 向会话环境安装第三方包。基础镜像预装 NumPy、SciPy、Pandas、Matplotlib、Pillow、openpyxl、python-docx、python-pptx、trimesh 和 uv。

将浏览器端域名指向已配置 TLS 的反向代理，示例 Caddy 配置如下。把示例域名换成部署域名，同时配置相同的 `FAMSUN_PUBLIC_ORIGIN`。API 自行比对 Origin，入口代理应保留 Host，部署默认按连接对端限速。若多位用户经过同一个代理，他们会共享该入口的登录速率额度；需要按客户端 IP 限速时，在可信边缘实施限流，并让 Uvicorn 仅接受固定代理地址的转发头。

```caddy
agent.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

企业代理环境可设置 `FAMSUN_CA_FILE` 为受信任的公开根证书，再用 `docker compose -f compose.yaml -f deploy/corporate-ca.compose.yaml --profile build build sandbox-image helper-image preview-image app` 构建。按网络要求设置 `FAMSUN_EGRESS_UPSTREAM`，指向出口代理可以访问的企业 HTTP 代理。保持证书验证开启。

## 实时执行记录与断线恢复

浏览器通过同源登录 cookie 订阅 `GET /api/sessions/{sid}/runs/{rid}/stream?after=N`。代理应持续转发 `text/event-stream` 响应并关闭该路由的响应缓冲。服务返回 `X-Accel-Buffering: no`、禁止缓存，空闲连接每 10 秒发送心跳注释。代理的上游空闲超时应大于心跳间隔。Caddy 会即时刷新事件流。使用 Nginx 时，可在相应 location 设置 `proxy_buffering off` 与 `proxy_read_timeout 60s`。

- 每条 `activity` 的 SSE `id` 是已提交的 SQLite 事件序号 `seq`。其 JSON 数据与分页接口中的同一行一致。业务节点的 `id` 可以重复，`parent_id` 用于还原层级。结构化参数、结果和模型公开文本增量位于可选 `payload`。合并时按 `seq` 去重，并保留此前已收到的节点内容。
- 重连时使用 `Last-Event-ID`，或者在新连接上设置 `after`。同时提供时使用较大的游标。服务按升序回放，查询每页最多 500 条、事件 JSON 预算 4 MiB。已发出的事件保存在 SQLite，关闭页面不影响任务。账户归属在每批数据读取时核验，撤销登录或令牌过期会关闭流。
- 全部终态记录送达后，服务发送 `end`，JSON 包含 `status`、`cursor`。客户端收到后关闭 EventSource。需要分页时，使用 `GET /api/sessions/{sid}/runs/{rid}/events?after=N`，依次读取 `events`、推进 `cursor`，并在 `has_more` 为真时继续读取。
- 旧库首次启动时自动为事件表添加可空的 `payload` 列。历史记录保留原序号和时间；其结构化字段为 null。无需重建会话或重置浏览器游标。新增大字段可能增加数据库体积，容量监控及备份应包含事件数据。

## 容量与隔离

需要跨任务查看模型用量、子智能体执行耗时和工具错误时，管理员可以将控制面连接到团队的 Langfuse 项目，按用户、会话和任务跟踪。配置、隐私选项及任务编号查询见[运行追踪指南](LANGFUSE.md)。

- `FAMSUN_MAX_PARALLEL_RUNS` 默认 4，同时运行的任务共享模型额度与主机能力。每会话最多一个进行中的任务。对繁忙的服务，按模型吞吐、CPU 和峰值内存调整并发。
- `FAMSUN_RUN_TIMEOUT` 默认 900 秒，`FAMSUN_SANDBOX_TIMEOUT` 默认 120 秒。图的单轮步数上限由 `FAMSUN_GRAPH_RECURSION_LIMIT` 配置，默认 160，包含 LangGraph 中间件步骤。SQLite 使用 WAL、本地磁盘和单 API 进程；指向同一个数据目录的第二个进程会拒绝启动。
- 使用 `docker info` 和 `python -m famsun.doctor --strict` 检查 cgroup。在 rootless daemon 上应将控制器委派给运行 Docker 的服务账号，并在一个实际会话内确认 `cpu.max`、`memory.max` 和 `pids.max`。测试专用开关 `FAMSUN_ALLOW_UNENFORCED_CGROUPS=true` 允许在控制器缺失的主机进行功能测试。
- 工作区及安装环境使用持久命名卷。部署者需给卷配置容量预算、告警和保留策略。可以通过 `FAMSUN_VOLUME_DRIVER` 和 `FAMSUN_VOLUME_OPTIONS`（字符串键值的 JSON 对象）传入支持持久卷配额的存储驱动设置。驱动必须按卷名分配独立空间。将所有卷指向相同的宿主目录或 NFS 子目录会破坏租户隔离。选项只用于新建卷。Docker 的默认 local 驱动按宿主文件系统可用空间增长。宿主宜采用支持写时复制的存储驱动；vfs 会为并发容器复制文件系统，验收期间需要额外的临时磁盘空间。
- 默认使用流式 Chat Completions 以接收长工具调用。仅支持非流式的第三方端点可设置 `FAMSUN_LLM_STREAMING=false`。模型密钥仅在控制面进程环境中可见。模型提供方收到对话和工具结果，选择端点时应符合企业对工程数据的处理要求。工作区和数据库均应使用受访问控制的加密存储。用户会话 cookie 的默认有效期为七天。
- API 账户使用 Argon2id；离职或权限撤销应由管理者处理。生产关闭开放注册。文件读取有权限、类型和大小检查，浏览器模型预览只接受自包含 GLB。单文件传输上限为 32 MiB。

## 文档预览服务

Office 转换使用专用镜像 `famsun-preview:local`，可用 `FAMSUN_PREVIEW_IMAGE` 指定同一 Docker daemon 上的预构建镜像。服务先按用户和会话读取文件快照，再通过标准输入送入转换容器。容器无网络和会话卷，以非 root 用户在只读根文件系统上运行，临时文件随容器释放。Office 的宏和文档外部更新均已禁用。PDF 字体、CMap 和解码器随前端构建部署。HTML 静态预览在沙箱 iframe 中打开，正文中引用的站外脚本、字体和图像不会加载。

每份 Office 源文件和转换生成的 PDF 上限均为 32 MiB。控制面最多同时执行两次不同的转换，重复请求共享结果，内存缓存上限为 64 MiB。遇到 429 可稍后重试，503 时检查转换镜像、Docker 容量与 cgroup，504 表示转换超时。复杂排版的实际呈现取决于 LibreOffice 和镜像内的字体，页面保留原件下载入口。

## 本地 Langfuse

管理员可将 Web、Worker、PostgreSQL、ClickHouse、Redis 和对象存储部署在同一台 Docker 主机。FAMSUN 仅访问 Web 的追踪接口，项目密钥保存在控制面。部署文件包含初始化、健康验证、镜像摘要及冷备份恢复步骤。[自托管步骤](../deploy/langfuse/README.md) · [FAMSUN 追踪配置](LANGFUSE.md)

## 删除单个会话

用户确认删除后，服务检查会话所有权及运行状态。正在排队或执行的任务返回 HTTP 409，停止任务后可以重试。一旦开始删除，该会话立即从用户历史中隐藏，并停止接收新运行与文件访问。服务等待已获准的文件读取与文档预览完成，清空该会话的预览缓存，按会话标识清理容器、网络和两个独立卷，再移除 LangGraph 检查点及写入记录、聊天历史和事件。全部完成后返回 HTTP 204。

如果 Docker、磁盘或检查点库暂时不可用，服务持久记录待删除状态并返回 HTTP 503。单 worker 在启动时及运行期间每 30 秒重试。管理员可以从包含会话编号的 `Session deletion pending` 日志排查存储故障，恢复后服务将自动完成删除。已下载的文件、历史备份和外部遥测受各自保留策略管理。

## 备份、恢复与更新

1. 在维护窗口停止接收新任务，等待进行中的任务结束，再运行 `docker compose stop app`。此时控制面 SQLite 主库、检查点库及会话卷处于一致的停写状态。
2. 使用卷驱动的快照/备份工具备份 Compose 的 `famsun-data` 卷（包括所有 WAL 文件）以及专属 daemon 上 `docker volume ls --filter label=io.famsun.sandbox=v1` 列出的工作区卷、环境卷。保存卷名、驱动选项、镜像摘要和代码提交号。备份必须保留 Unix UID/GID 及读写权限。
3. 在隔离的恢复主机上创建同名卷，恢复这些快照和镜像，从密钥系统重新注入服务端配置。启动一个 API worker，运行 `doctor --strict --live-model`，然后以验收账号登录历史会话，核对产物哈希并继续一次对话。中断中的历史运行将被标记为失败，后台会重新清理其容器。
4. 升级前完成备份并保留镜像，重建沙箱、服务端及前端。新运行使用新镜像，工作区与个人 Python 安装环境随卷保留。升级 Python ABI 或大版本依赖时，应在恢复副本中验证已有环境卷与新镜像的兼容性。

监控健康检查、任务失败率/耗时、模型供应商错误、Docker 孤儿资源、剩余磁盘与 inode。排查任务时从页面运行编号关联服务日志、SQLite 事件与模型工具状态。周期性恢复演练、容量测量和真实业务模型基准应在目标主机上执行。
