# 工程任务验收

在已部署的 FAMSUN Agent 上运行以下命令。服务需连接支持工具调用的模型、Docker 和沙箱镜像。运行器会创建新的独立会话，通过浏览器使用的同源 HTTP 接口提交任务、分页拉取事件、下载产物，再在验收进程中计算数值和几何误差。

```bash
uv sync --locked --group eval
uv run --locked --group eval python -m evals.live \
  --base-url http://127.0.0.1:8000 \
  --parallel 2 --output var/evals/first-run
```

允许注册的测试部署会自动创建随机测试用户。生产式部署应预先建立专用测试账号，并由环境注入 `FAMSUN_EVAL_USERNAME`、`FAMSUN_EVAL_PASSWORD`。输出目录必须是新路径，以保留历史执行的证据。使用 `--case pellet_mill` 可单独重试某个场景。每次运行都会消耗模型和沙箱资源。

| 场景 | 自动验证 |
|---|---|
| 三体模型 | 对比独立 RK4 积分器的全部 303 个状态，检查能量、动量和质心，并解码三维轨迹 PNG。 |
| 环模制粒机 | 解码装配与爆炸 GLB，检查六个命名网格、环模通孔、压辊位置、零件形状保持和多方向相对位移。要求轨迹中包含子智能体实际调用工具。 |
| 振动诊断 | 检查 2,049 个频点及振幅、前三峰、交流均方根和可解码的频谱 PNG。 |
| 配方优化 | 用独立的最优解校验成本，复算质量守恒、各原料用量和三类营养约束。 |
| 跨日停机 | 核对七个按日期、机器、原因划分的时长，覆盖跨午夜、时间重叠、重复记录与汇总守恒。 |

每个场景保存实际下载的指定产物、SHA-256、运行与会话编号、时间、错误，以及每一步的节点 ID、父节点、类型和状态。失败步骤保留工具返回的诊断摘要。验收要求 Agent 返回最终答复、轨迹闭合、发生实际工具调用且产物通过外部校验。中途工具调用失败而后恢复会单独计数。进程退出码 0 表示所有选中场景通过。单元测试中的合成文件用于检查验收器，真实推理结果保存在独立目录。

下载证据后可复算 SHA-256、所有轨迹父子关系与产物验收断言，无需再次推理：

```bash
uv run --locked --group eval python -m evals.recheck var/evals/first-run
```

若真实推理已正常结束而文件服务一度不可用，可在修复后用同一用户复查原始运行。该模式校验实际保存的用户需求与场景一致，重新读取原始事件、下载产物和生成输入，并写入新目录。报告分别记录原始执行和只读复验的耗时。

```bash
uv run --locked --group eval python -m evals.live \
  --reinspect var/evals/first-run/three_body/result.json \
  --base-url http://127.0.0.1:8000 --output var/evals/three-body-files-recheck
```
