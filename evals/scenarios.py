"""Acceptance briefs. All inputs are synthetic and included in each user prompt."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    brief: str
    outputs: tuple[str, ...]
    require_subagent: bool = False

    @property
    def prompt(self) -> str:
        return (
            self.brief.strip()
            + "\n把交付文件直接保存到本会话工作区 /workspace 根目录，文件名须匹配。"
            "请实际在沙箱内运行程序、读取产物做核验，结束时用中文简述结果。"
        )


SCENARIOS = (
    Scenario(
        "three_body",
        "三体三维轨道仿真",
        """
用 Python 建立牛顿三体模型，G=1，三体质量均为 1，无软化。
未旋转的初始位置依次为 (-0.97000436,0.24308753,0)、(0.97000436,-0.24308753,0)、(0,0,0)；
初始速度依次为 (0.466203685,0.43236573,0)、(0.466203685,0.43236573,0)、(-0.93240737,-0.86473146,0)。
对所有初始位置和速度绕 x 轴按右手定则旋转 +30 度，积分 t=0 到 1；
每隔 0.01 输出一次，首尾均包含。生成 trajectory.csv，列为 t,body,x,y,z,vx,vy,vz，
body 用 1,2,3 表示；生成白底可读的三维轨道图 orbits.png（至少 640×360）。
核验系统能量相对漂移、总动量以及质心位移。
""",
        ("trajectory.csv", "orbits.png"),
    ),
    Scenario(
        "pellet_mill",
        "环模制粒机装配与爆炸图",
        """
建立一个可旋转展示的饲料环模制粒机概念模型，环模留有清晰中央通孔，两个压辊位于环模内。
请调用一个工程子智能体分担建模或复核，通过沙箱运行代码。使用 trimesh 等离线工具输出两个独立、自包含的 glTF 2.0 二进制文件：
pellet.glb 为装配，exploded.glb 为同一设备的爆炸视图。两份场景都至少要有六个同名的独立网格节点，
以下六个节点名称须精确为 frame, motor, shaft, ring_die, roller_left, roller_right。
生成时为每个零件分配可识别材料/颜色，把各零件连接成有合理相对位置的设备；
爆炸图至少将四个零件沿不同方向拉开，保持网格形状、大小、朝向和层次，不要合并网格。
""",
        ("pellet.glb", "exploded.glb"),
        True,
    ),
    Scenario(
        "vibration",
        "轴承振动频谱诊断",
        """
合成一段传感器加速度序列：采样率 1024 Hz、4096 个样本、t=n/1024，单位 m/s²。
x(t)=0.12+0.8*sin(2π*48.25*t)+0.35*cos(2π*119.5*t)+0.06*sin(2π*239*t)。
去均值后计算单边 FFT 峰值振幅（矩形窗，除 DC/Nyquist 外两倍幅值，按 N 归一）。
输出 spectrum.csv，列 frequency_hz,amplitude，保留全部含 DC 与 Nyquist 的频点。
输出 vibration.json，字段 ac_rms、top_hz（按振幅降序的三种频率）、sample_count。
输出 spectrum.png（至少 640×360），坐标含单位，标出三个主峰。
""",
        ("spectrum.csv", "vibration.json", "spectrum.png"),
    ),
    Scenario(
        "feed_mix",
        "四原料饲料配方优化",
        """
为一吨饲料建立最小成本线性规划，按 corn,soy,bran,lime 顺序，各原料单价为 [240,430,200,100] 元/吨，
粗蛋白质量百分数 [9,46,16,0]，粗纤维百分数 [2,4,12,0]，钙百分数 [0.03,0.32,0.13,38]。
各原料用量非负，合计1000 kg；玉米至少350 kg，豆粕至多400 kg，麸皮至多250 kg，石粉至多40 kg。
配方蛋白不少于18%，纤维不多于5%，钙在0.75%到1.0%之间。求成本最低的可行配方。
写出 formula.json，字段 kg（对象，键 corn,soy,bran,lime）、cost_per_tonne、protein_pct、fiber_pct、calcium_pct。
数值保留至少五位小数。复算质量守恒、营养与成本后说明哪些约束取等。
""",
        ("formula.json",),
    ),
    Scenario(
        "downtime",
        "跨日停机去重与归因",
        """
两条产线的本地停机时间（无夏令时）如下，每行依次为 machine,reason,start,end：
A,jam,2025-12-31 23:40,2026-01-01 00:20
A,electrical,2025-12-31 23:55,2026-01-01 00:05
A,jam,2026-01-01 00:20,2026-01-01 00:30
B,maintenance,2025-12-31 23:50,2026-01-01 00:10
B,jam,2026-01-01 00:00,2026-01-01 00:20
A,jam,2025-12-31 23:40,2026-01-01 00:20
区间左闭右开，同一机器的重叠分钟只能归于一种原因。优先级 electrical > maintenance > jam。
按自然日切分，忽略完全重复的记录。生成 downtime.csv，列 date,machine,reason,minutes，
只写正时长组合；生成 downtime.json，结构为 {"total_machine_minutes":数值,"by_reason":{"jam":数值,"electrical":数值,"maintenance":数值}}。
逐台解释跨午夜与重叠处理。
""",
        ("downtime.csv", "downtime.json"),
    ),
)
