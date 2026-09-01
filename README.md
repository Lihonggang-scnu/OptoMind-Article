# OptoMind-Article

OptoMind-Article 是一个面向光学薄膜设计的可审计科学实验任务规划与反馈迭代系统。它接收自然语言形式的工程需求，将需求转换为可测量的光学目标，组织文献启发路线与独立对照路线，生成可执行的 VeriTMM 任务，并把真实仿真结果、物理证书、反馈和后续规划保存为可回放的研究记录。

项目的当前验证范围是平面、各向同性、多层薄膜的频域传输矩阵法（TMM）设计。它面向计算科学实验与方案筛选，不替代真实制备、实验测量或超出 TMM 适用范围的全波求解器。

## 核心能力

- 从用户问题中建立结构化的研究对象、波段、观测量、约束和假设。
- 根据具体问题动态选择可由 VeriTMM 计算的评价指标，并在实验开始前固定评分标准。
- 并行组织文献规划路线和不接收文献输入的记忆对照路线，支持研究来源的可观测比较。
- 将路线和方法编译为有明确材料、波段、入射条件、层结构与目标的 TMM 实验任务。
- 对候选进行独立验证、收敛检查、能量守恒检查、无源性检查、材料溯源检查和制造扰动鲁棒性分析。
- 以迭代观测和反馈驱动下一轮路线调整，并记录停止原因、预算状态和所有中间产物。
- 通过冻结评分、物理接受证书和运行清单连接最终候选与原始题面。

语言模型只负责受约束的自然语言分析、路线假设和任务编译；实验协议、字段校验、物理计算、证书签发、评分和产物索引由程序层完成。模型文本不能替代数值结果，也不能直接签发物理结论。

## 工作流程

```text
自然语言需求
    ↓
问题分析与能力边界
    ↓
动态指标选择 → 指标核验 → 冻结评分标准
    ↓
文献路线 + 记忆对照路线
    ↓
TMM 任务编译与材料绑定
    ↓
VeriTMM 执行、物理证书与候选验证
    ↓
反馈记录与路线再规划
    ↓
冻结排名、研究汇总与可回放产物
```

## 目录结构

| 路径 | 内容 |
|---|---|
| `code/` | 主 harness、配置、测试、工具和运行脚本。 |
| `code/replay_ui/` | 六组完整运行的只读可视化静态回放前端。 |
| `code/optomind_optics/harness/research_console.py` | 研究控制台服务，同时提供回放接口和真实研究接口。 |
| `code/optomind_optics/harness/live_research.py` | 受限的真实运行管理器，负责启动、观察、停止和恢复研究任务。 |
| `code/scripts/run_research_console.py` | 跨平台启动真实研究与静态回放一体化控制台。 |
| `code/scripts/run_static_replay_ui.py` | 静态回放台本地启动入口。 |
| `code/requirements-runtime.txt` | 容器化实时研究所需的最小运行依赖。 |
| `Dockerfile`、`compose.yaml`、`render.yaml` | 本地容器和云端部署配置。 |
| `code/prompts/optical_harness/` | 运行时使用的结构化提示模板。 |
| `veritmm/` | 与本项目同级挂载的 VeriTMM 物理执行引擎。 |
| `accepted_examples/` | 验收与示例资产。 |
| `article_memory/` | 文章链路使用的记忆边界和清单资产。 |
| `code/outputs/tmm_research_harness/` | 六组未经改写的正式 E2E 原始记录。 |
| `AGENT_GUIDE.zh-CN.md` | 面向评委和 AI Agent 的快速运行与摸底指南。 |

根目录的 Agent 指南是评测入口；面向普通使用者的项目说明以本 README 为准。运行时真正读取的是 `code/prompts/optical_harness/`，根目录不再保留历史 handoff 提示词目录。

公开发布包聚焦 TMM 科研 Harness、随项目固定的 VeriTMM 执行组件和六组可回放运行记录；论文流水线历史资产与在线文献缓存不属于运行时必需内容。六组运行树中的题面、路线、迭代、仿真、证书、排名和最终结果保持原始记录，不依赖这些历史缓存。

## 六次完整端到端运行成果

六组正式运行均从面向工程用户的自然语言题面开始，经过问题分析、动态指标选择、评分标准锁定、文献路线与记忆对照路线规划、TMM 任务编译、真实 VeriTMM 仿真、候选验证、反馈迭代、冻结排名和最终汇总，最终状态均为 `completed / finished`。以下结果直接取自六组运行目录中的 JSON、JSONL 和 Markdown 产物；原始记录保持原样，可沿每组的链接逐层回放。

### 总体规模

六次运行共同形成的研究与物理产物规模如下：

| 类别 | 指标 | 六组合计 | 说明 |
|---|---:|---:|---|
| 运行 | 完整端到端运行 | **6** | 六个独立的工程应用题面 |
| 路线 | 路线臂 | **24** | 含文献规划路线与不接收文献输入的记忆对照路线 |
| 迭代 | 迭代记录 | **126** | 每轮均有路线、编译、观测和反馈相关产物 |
| 任务 | 编译任务记录 | **117** | 已生成 `COMPILED_TASK.json` 的迭代 |
| 执行 | 完成的 TMM 迭代 | **105** | 形成 `FINAL_RESULT.json` 的迭代 |
| 候选 | 物理有效候选 | **926** | 通过候选级物理与任务验证 |
| 评分 | 可评分 / 已评分候选 | **829 / 829** | 能够按本次运行冻结标准直接比较 |
| 证书 | 物理接受证书 | **1,413** | `PHYSICS_ACCEPTANCE_CERTIFICATE.json` 文件数 |
| 计算 | 前向评估 | **101,711** | 由运行遥测记录的 TMM 前向计算 |
| 计算 | 优化器运行 | **213** | 由运行遥测记录的优化器执行 |
| 产物 | 原始文件总数 | **10,327** | 六个运行目录递归统计，包含中间记录与证书 |

模型和计算投入如下：

| 指标 | 六组合计 |
|---|---:|
| Qwen 实际调用 | **314 次** |
| 输入 Token | **2,082,999** |
| 输出 Token | **2,267,021** |
| Token 总量 | **4,350,020** |
| 估算模型成本 | **¥18.32** |
| 有效墙钟时间 | **33,709.24 秒，约 9 小时 21 分 49 秒** |
| 使用模型 | `qwen3.5-plus`、`qwen3.7-flash` |

模型调用、Token 和成本均按运行遥测中的实际记录统计；成本是按当前配置计算的估算值。前向评估、优化器运行和原始文件数不等同于模型调用数，而是实验执行层的独立计量。

### 六组冻结结果总览

每个运行在实验开始阶段根据用户题面选择并锁定自己的评分字段与公式。表中的“冻结标准得分”是该运行的直接评分公式结果，因此只能在同一运行内部比较；不同题面的目标数量和公式不同，不能把六个得分当作跨任务的统一排行榜。

| 组别与应用 | 本次冻结评分公式 | 冻结冠军 | 冻结标准得分 | 冠军指标分解 | 对照 − 文献 |
|---|---|---|---:|---|---:|
| 1 · 石化园区 / 海上风电甲烷巡检 SWIR | `mean_transmittance_1000_1700nm + mean_reflectance_300_450nm` | 记忆对照；24 层 HfO2/SiO2 | **1.776082** | T=0.918708；R=0.857374 | **+0.084438**，对照更高 |
| 2 · 高空无人机 SWIR 遥感 | `mean_transmittance_800_1500nm + reflectance_stopband_200_400nm` | 文献路线；16 层 HfO2/SiO2 | **1.729851** | T=0.965155；R=0.764697 | **−0.064662**，文献更高 |
| 3 · 星载量子密钥分发 C 波段接收 | `mean_transmittance_1530_1565nm + mean_reflectance_400_700nm` | 记忆对照；24 层 HfO2/SiO2 | **1.868417** | T=0.986861；R=0.881556 | **+0.029803**，对照更高 |
| 4 · 低轨太阳盲紫外探测 | `mean_transmittance_255_280nm + reflectance_stopband_300_700nm + reflectance_stopband_700_1100nm` | 记忆对照；29 层 HfO2/MgF2 | **2.177978** | T=0.513988；R=0.790385；R=0.873605 | **+0.188864**，对照更高 |
| 5 · 小卫星甲烷 / 二氧化碳双气体遥感 | `mean_transmittance_3250_3350nm + mean_transmittance_4200_4300nm + reflectance_stopband_2500_3100nm + reflectance_stopband_4450_5000nm` | 文献路线；23 层 Si/Al2O3 | **3.979005** | T=0.986448；T=0.997653；R=0.998391；R=0.996512 | **−0.327489**，文献更高 |
| 6 · 燃气轮机 / 工业烟气 CO 在线监测 | `mean_transmittance_4150_4350nm + mean_transmittance_4550_4750nm + reflectance_stopband_3600_4000nm + reflectance_stopband_4850_5200nm` | 文献路线；20 层 Ge/ZnS | **3.909151** | T=0.985538；T=0.964043；R=0.988678；R=0.970892 | **−0.107807**，文献更高 |

六组中，文献路线和记忆对照路线各获得 3 次冻结冠军；6/6 组的路线来源比较均有效。这个对照设计让“检索到的科学依据是否带来可观测收益”成为可测量问题，而不是预先假定文献路线一定优于模型记忆路线。

### 六组运行的逐组记录

#### 1. 甲烷泄漏巡检短波红外窗口

题面：

> 我负责一台用于石化园区和海上风电场甲烷泄漏巡检的短波红外成像相机前端防护窗口研发。为了在白天强太阳背景下提高甲烷吸收特征的信噪比，相机需要尽可能完整地接收 1000–1700 nm 波段的短波红外信号，同时尽可能反射 300–450 nm 波段的近紫外和蓝光杂散辐射，以降低太阳散射背景和探测器的杂散响应。请在空气入射、熔融石英基底的条件下，仅使用 HfO2 和 SiO2 设计一个平面多层介质膜，膜系总层数不超过 30 层。

| 项目 | 实际记录 |
|---|---|
| 动态评分公式 | `mean_transmittance_1000_1700nm + mean_reflectance_300_450nm` |
| 运行规模 | 4 条路线；20 条迭代记录；18 条已编译；17 条完成执行 |
| 候选规模 | 142 个物理有效候选；129 个可评分候选 |
| 计算投入 | 10,317 次前向评估；34 次优化器运行 |
| 模型投入 | 51 次 Qwen 调用；输入 417,550 Token；输出 369,657 Token；估算成本 ¥3.0526 |
| 墙钟时间 | 5,184.13 秒，约 1 小时 26 分 |
| 冻结冠军 | `control_route_01`，候选 `opt_24layer_dual_band_high_R_low_R__172eaad160ed` |
| 代表结构 | 24 层 HfO2 / SiO2 膜系，熔融石英基底 |
| 冻结结果 | 得分 1.776082；1000–1700 nm 平均 T=0.918708；300–450 nm 平均 R=0.857374 |
| 扰动鲁棒性 | 相对均匀扰动，扰动比例 0.5；16 个样本，0 次失败；运行汇总鲁棒性软分 0.699990，p10=0.695693，最差=0.695230 |
| 物理证书 | `561e5a6cc5f8cf82050e87fc0a0ddc72499484f0adaf3389a4625d9d0dc8454c` |
| 路线对照 | 对照路线 − 文献路线 = +0.084438 |
| 原始产物 | [题面](code/outputs/tmm_research_harness/e2e-methane-swir-window-20260828-default4-w10800/REQUEST.json) · [冻结排名](code/outputs/tmm_research_harness/e2e-methane-swir-window-20260828-default4-w10800/SCORING_RANKING.json) · [路线汇总](code/outputs/tmm_research_harness/e2e-methane-swir-window-20260828-default4-w10800/TOURNAMENT_SUMMARY.json) · [最终回答](code/outputs/tmm_research_harness/e2e-methane-swir-window-20260828-default4-w10800/FINAL_ANSWER.md) · [事件流](code/outputs/tmm_research_harness/e2e-methane-swir-window-20260828-default4-w10800/RESEARCH_EVENTS.jsonl) |

#### 2. 高空无人机短波红外遥感窗口

题面：

> 我负责一款用于高空无人机短波红外遥感相机的前端防护窗口研发。相机需要尽可能完整地接收 800–1500 nm 波段的短波红外目标信号，同时尽可能反射 200–400 nm 波段的紫外辐射，以减少太阳紫外杂散光进入探测器，并降低长期紫外辐照对探测器和后端光学组件的影响。请在空气入射、熔融石英基底的条件下设计一个平面多层介质膜。膜层材料仅允许使用 HfO2 和 SiO2，膜系总层数不超过 30 层。在这些材料和层数限制下，希望同时获得 800–1500 nm 波段的高透射率和 200–400 nm 波段的高反射率。

| 项目 | 实际记录 |
|---|---|
| 动态评分公式 | `mean_transmittance_800_1500nm + reflectance_stopband_200_400nm` |
| 运行规模 | 3 条路线；16 条迭代记录；16 条已编译；15 条完成执行 |
| 候选规模 | 135 个物理有效候选；120 个可评分候选 |
| 计算投入 | 7,546 次前向评估；32 次优化器运行 |
| 模型投入 | 35 次 Qwen 调用；输入 238,739 Token；输出 239,452 Token；估算成本 ¥1.9254 |
| 墙钟时间 | 3,715.81 秒，约 1 小时 2 分 |
| 冻结冠军 | `route_01` 文献路线，候选 `opt_16layer_hsfs_200_1500nm__gradi__8d43943f7ff0` |
| 代表结构 | 16 层 HfO2 / SiO2 膜系，8 对周期结构，熔融石英基底 |
| 冻结结果 | 得分 1.729851；800–1500 nm 平均 T=0.965155；200–400 nm 反射带平均 R=0.764697 |
| 扰动鲁棒性 | 绝对正态扰动，σ=1 nm；16 个样本，0 次失败；运行汇总鲁棒性软分 0.461768，p10=0.461260，最差=0.460899 |
| 物理证书 | `f12f7663e68b86fc864f754cee3705fe2a3b030ea36464ae2c3a2ea24d3e5127` |
| 路线对照 | 对照路线 − 文献路线 = −0.064662 |
| 原始产物 | [题面](code/outputs/tmm_research_harness/e2e-uav-swir-window-20260829-default4-w10800/REQUEST.json) · [冻结排名](code/outputs/tmm_research_harness/e2e-uav-swir-window-20260829-default4-w10800/SCORING_RANKING.json) · [路线汇总](code/outputs/tmm_research_harness/e2e-uav-swir-window-20260829-default4-w10800/TOURNAMENT_SUMMARY.json) · [最终回答](code/outputs/tmm_research_harness/e2e-uav-swir-window-20260829-default4-w10800/FINAL_ANSWER.md) · [事件流](code/outputs/tmm_research_harness/e2e-uav-swir-window-20260829-default4-w10800/RESEARCH_EVENTS.jsonl) |

#### 3. 星载量子密钥分发 C 波段接收窗口

题面：

> 我负责星载量子密钥分发接收机前端防护窗口的研发。接收机需要尽可能完整地接收 1530–1565 nm 电信波段的单光子信号，同时尽可能反射 400–700 nm 波段的可见光太阳背景，以降低空间太阳散射和探测器杂散光对量子信号接收的影响。请在空气入射、熔融石英基底的条件下，仅使用 HfO2 和 SiO2 设计一个平面多层介质膜，膜系总层数不超过 30 层。

| 项目 | 实际记录 |
|---|---|
| 动态评分公式 | `mean_transmittance_1530_1565nm + mean_reflectance_400_700nm` |
| 运行规模 | 5 条路线；24 条迭代记录；24 条已编译；23 条完成执行 |
| 候选规模 | 203 个物理有效候选；181 个可评分候选 |
| 计算投入 | 57,549 次前向评估；47 次优化器运行 |
| 模型投入 | 55 次 Qwen 调用；输入 277,989 Token；输出 402,980 Token；估算成本 ¥3.2319 |
| 墙钟时间 | 5,944.24 秒，约 1 小时 39 分 |
| 冻结冠军 | `control_route_01` 记忆对照，候选 `opt_24layer_dualband__gradient_thickness__01` |
| 代表结构 | 24 层 HfO2 / SiO2 膜系，熔融石英基底 |
| 冻结结果 | 得分 1.868417；1530–1565 nm 平均 T=0.986861；400–700 nm 平均 R=0.881556 |
| 扰动鲁棒性 | 相对均匀扰动，扰动比例 0.15；16 个样本，0 次失败；运行汇总鲁棒性软分 0.484860，p10=0.477052，最差=0.466795 |
| 物理证书 | `674b7735a6d791300c7b7b9e1afe4e49e9710f504307f4f8fcfeedfb817b5564` |
| 路线对照 | 对照路线 − 文献路线 = +0.029803 |
| 原始产物 | [题面](code/outputs/tmm_research_harness/e2e-space-qkd-cband-window-20260829-default4-w10800/REQUEST.json) · [冻结排名](code/outputs/tmm_research_harness/e2e-space-qkd-cband-window-20260829-default4-w10800/SCORING_RANKING.json) · [路线汇总](code/outputs/tmm_research_harness/e2e-space-qkd-cband-window-20260829-default4-w10800/TOURNAMENT_SUMMARY.json) · [最终回答](code/outputs/tmm_research_harness/e2e-space-qkd-cband-window-20260829-default4-w10800/FINAL_ANSWER.md) · [事件流](code/outputs/tmm_research_harness/e2e-space-qkd-cband-window-20260829-default4-w10800/RESEARCH_EVENTS.jsonl) |

#### 4. 低轨太阳盲紫外探测滤光膜

题面：

> 我正在为低轨空间平台上的太阳盲紫外臭氧与高空燃烧羽流探测器设计前端滤光膜。探测器需要尽可能透过 255–280 nm 的太阳盲紫外信号，同时尽可能抑制 300–700 nm 可见光背景和 700–1100 nm 近红外杂散光。请在空气入射、熔融石英基底的条件下，设计一个不含金属层、仅使用常规可沉积无机介质材料的平面多层膜，膜系总层数不超过 30 层。

| 项目 | 实际记录 |
|---|---|
| 动态评分公式 | `mean_transmittance_255_280nm + reflectance_stopband_300_700nm + reflectance_stopband_700_1100nm` |
| 运行规模 | 4 条路线；20 条迭代记录；20 条已编译；19 条完成执行 |
| 候选规模 | 169 个物理有效候选；151 个可评分候选 |
| 计算投入 | 9,710 次前向评估；38 次优化器运行 |
| 模型投入 | 48 次 Qwen 调用；输入 329,851 Token；输出 361,249 Token；估算成本 ¥2.9534 |
| 墙钟时间 | 5,141.47 秒，约 1 小时 26 分 |
| 冻结冠军 | `control_route_01` 记忆对照，候选 `optimize_29layer_hr_filter__gradie__1b3223328947` |
| 代表结构 | 29 层 HfO2 / MgF2 膜系，熔融石英基底 |
| 冻结结果 | 得分 2.177978；255–280 nm 平均 T=0.513988；300–700 nm 平均 R=0.790385；700–1100 nm 平均 R=0.873605 |
| 扰动鲁棒性 | 绝对正态扰动，σ=1 nm；16 个样本，0 次失败；运行汇总鲁棒性软分 0.274695，p10=0.273405，最差=0.272490 |
| 物理证书 | `d6c1cb59a226050b0930e803e9a4d03d067394383509d62e7e5ce2fb0fcd6918` |
| 路线对照 | 对照路线 − 文献路线 = +0.188864 |
| 原始产物 | [题面](code/outputs/tmm_research_harness/e2e-solarblind-uv-window-20260829-default4-w10800/REQUEST.json) · [冻结排名](code/outputs/tmm_research_harness/e2e-solarblind-uv-window-20260829-default4-w10800/SCORING_RANKING.json) · [路线汇总](code/outputs/tmm_research_harness/e2e-solarblind-uv-window-20260829-default4-w10800/TOURNAMENT_SUMMARY.json) · [最终回答](code/outputs/tmm_research_harness/e2e-solarblind-uv-window-20260829-default4-w10800/FINAL_ANSWER.md) · [事件流](code/outputs/tmm_research_harness/e2e-solarblind-uv-window-20260829-default4-w10800/RESEARCH_EVENTS.jsonl) |

#### 5. 小卫星双气体中波红外遥感滤光膜

题面：

> 我正在为小卫星上的双气体遥感载荷设计共享前端滤光膜。传感器需要同时透过甲烷 3.25–3.35 μm 和二氧化碳 4.20–4.30 μm 两个窄波段的辐射，同时在 2.50–3.10 μm 和 4.45–5.00 μm 抑制太阳及地球背景。请在空气入射、CaF2 基底条件下，设计一个不含金属层、仅使用常规可沉积红外无机介质材料的平面多层膜，膜系总层数不超过 24 层。

| 项目 | 实际记录 |
|---|---|
| 动态评分公式 | `mean_transmittance_3250_3350nm + mean_transmittance_4200_4300nm + reflectance_stopband_2500_3100nm + reflectance_stopband_4450_5000nm` |
| 运行规模 | 4 条路线；22 条迭代记录；20 条已编译；16 条完成执行 |
| 候选规模 | 142 个物理有效候选；128 个可评分候选 |
| 计算投入 | 7,312 次前向评估；32 次优化器运行 |
| 模型投入 | 59 次 Qwen 调用；输入 400,139 Token；输出 425,532 Token；估算成本 ¥3.3904 |
| 墙钟时间 | 6,565.19 秒，约 1 小时 49 分 |
| 冻结冠军 | `route_03` 文献路线，候选 `ir_dielectric_filter_opt__gradient_thickness__01` |
| 代表结构 | 23 层 Si / Al2O3 双腔膜系，CaF2 基底 |
| 冻结结果 | 得分 3.979005；3250–3350 nm 平均 T=0.986448；4200–4300 nm 平均 T=0.997653；2500–3100 nm 平均 R=0.998391；4450–5000 nm 平均 R=0.996512 |
| 扰动鲁棒性 | 绝对正态扰动，σ=1 nm；16 个样本，0 次失败；`TOURNAMENT_SUMMARY.json` 运行汇总鲁棒性评分 0.722445；对应 `ROBUSTNESS.json` 的 mean soft=0.458122，保留各自原始口径 |
| 物理证书 | `d4b5affd6030cb026f6e42e0a7a275619e9db34381318ae84d10c8753b975364` |
| 路线对照 | 对照路线 − 文献路线 = −0.327489 |
| 原始产物 | [题面](code/outputs/tmm_research_harness/e2e-fifth-dualgas-mwir-20260829-default4-w10800/REQUEST.json) · [冻结排名](code/outputs/tmm_research_harness/e2e-fifth-dualgas-mwir-20260829-default4-w10800/SCORING_RANKING.json) · [路线汇总](code/outputs/tmm_research_harness/e2e-fifth-dualgas-mwir-20260829-default4-w10800/TOURNAMENT_SUMMARY.json) · [最终回答](code/outputs/tmm_research_harness/e2e-fifth-dualgas-mwir-20260829-default4-w10800/FINAL_ANSWER.md) · [事件流](code/outputs/tmm_research_harness/e2e-fifth-dualgas-mwir-20260829-default4-w10800/RESEARCH_EVENTS.jsonl) |

#### 6. 燃气轮机与工业烟气 CO 在线监测滤光膜

题面：

> 我正在为燃气轮机和工业烟气在线监测系统设计一个共享前端红外滤光膜。探测器需要同时通过一氧化碳 4.55–4.75 μm 和二氧化碳 4.15–4.35 μm 两个窄波段的辐射，并在 3.60–4.00 μm 以及 4.85–5.20 μm 范围内抑制背景和其他热辐射。请在空气正入射、CaF2 基底条件下，设计一个不含金属层、仅使用常规可沉积红外无机介质材料的平面多层膜，膜系总层数不超过 24 层。

| 项目 | 实际记录 |
|---|---|
| 动态评分公式 | `mean_transmittance_4150_4350nm + mean_transmittance_4550_4750nm + reflectance_stopband_3600_4000nm + reflectance_stopband_4850_5200nm` |
| 运行规模 | 4 条路线；24 条迭代记录；19 条已编译；15 条完成执行 |
| 候选规模 | 135 个物理有效候选；120 个可评分候选 |
| 计算投入 | 9,277 次前向评估；30 次优化器运行 |
| 模型投入 | 66 次 Qwen 调用；输入 418,731 Token；输出 468,151 Token；估算成本 ¥3.7671 |
| 墙钟时间 | 7,158.41 秒，约 1 小时 59 分 |
| 冻结冠军 | `route_01` 文献路线，候选 `opt_ir_dbr_dual_pass_20l__gradient_thickness__01` |
| 代表结构 | 20 层 Ge / ZnS 双通带膜系，CaF2 基底 |
| 冻结结果 | 得分 3.909151；4150–4350 nm 平均 T=0.985538；4550–4750 nm 平均 T=0.964043；3600–4000 nm 平均 R=0.988678；4850–5200 nm 平均 R=0.970892 |
| 扰动鲁棒性 | 绝对正态扰动，σ=1 nm；16 个样本，0 次失败；运行汇总鲁棒性软分 0.613521，p10=0.612116，最差=0.611392 |
| 物理证书 | `6dc271cdad226e221bbb9e7dad44393b74975cab5f8a2f3c923afa54050a49` |
| 路线对照 | 对照路线 − 文献路线 = −0.107807 |
| 原始产物 | [题面](code/outputs/tmm_research_harness/e2e-sixth-combustion-co-20260829-default4-w10800/REQUEST.json) · [冻结排名](code/outputs/tmm_research_harness/e2e-sixth-combustion-co-20260829-default4-w10800/SCORING_RANKING.json) · [路线汇总](code/outputs/tmm_research_harness/e2e-sixth-combustion-co-20260829-default4-w10800/TOURNAMENT_SUMMARY.json) · [最终回答](code/outputs/tmm_research_harness/e2e-sixth-combustion-co-20260829-default4-w10800/FINAL_ANSWER.md) · [事件流](code/outputs/tmm_research_harness/e2e-sixth-combustion-co-20260829-default4-w10800/RESEARCH_EVENTS.jsonl) |

### 端到端产物如何形成证据链

六组目录保留了从题面到数值结果的完整文件链：

| 阶段 | 代表产物 | 记录内容 |
|---|---|---|
| 用户输入 | `REQUEST.json` | 原始工程问题、运行 ID 和运行参数 |
| 问题理解 | `PROBLEM_ANALYSIS.json` | 研究对象、波段、观测量、约束和能力边界 |
| 指标选择 | `SCORING_STANDARD.json`、`SCORING_STANDARD.ATTESTATION.json` | 本次运行的动态指标、方向、波段和锁定公式 |
| 方法检索 | `METHOD_RESEARCH.json` | 文献方法检索及其结构化结果 |
| 路线规划 | `ROUTE_PLANNING.json`、`STRATEGY_PLAN.json` | 文献路线、记忆对照路线、路线假设和规划状态 |
| 任务编译 | `iterations/iteration_XX/COMPILED_TASK.json` | 材料、波段、入射条件、膜层约束、目标和执行任务 |
| 逐轮观测 | `ITERATION_OBSERVATION.json` | 本轮候选、测量值、物理检查和候选状态 |
| 反馈再规划 | `FEEDBACK_DECISION.json`、`STRATEGY_REPLAN_*.json` | 真实仿真结果如何反馈到下一轮路线调整 |
| 过程审计 | `ITERATION_HISTORY.json`、`ROUTE_TERMINATION_AUDIT.json`、`TOURNAMENT_STATE.json` | 迭代顺序、路线终止/继续状态和运行中的汇总状态 |
| 仿真与证书 | `SIMULATION_RESULT.json`、`OBJECTIVE_REPORT.json`、`PHYSICS_ACCEPTANCE_CERTIFICATE.json`、`ROBUSTNESS.json` | 光谱结果、目标指标、物理可接受性和制造扰动采样 |
| 排名汇总 | `SCORING_RANKING.json`、`TOURNAMENT_SUMMARY.json` | 冻结标准排名、路线对照、冠军候选和鲁棒性汇总 |
| 最终交付 | `FINAL_ANSWER.md`、`RESEARCH_RESULT.json`、`RESEARCH_EVENTS.jsonl` | 面向阅读的结果、程序消费的状态和按时间排序的阶段事件 |

这种目录结构支持两种使用方式：可以直接阅读 `FINAL_ANSWER.md` 和冻结排名，也可以沿 `REQUEST.json → SCORING_STANDARD.json → ROUTE_PLANNING.json → iterations/ → SCORING_RANKING.json` 的顺序回放每个决定是如何由真实实验产物支撑的。每组代表性冠军均带有物理接受证书；鲁棒性记录采用 16 个厚度扰动样本，失败次数也保存在原始 JSON 中。

## 可视化静态回放

仓库内置只读的“静态研究回放台”，用于直接浏览随项目固化的六组完整运行。它会从产物目录实时生成索引，不维护另一份手工录入的数据，也不会调用语言模型、文献服务、优化器或 VeriTMM。评审者可以在不消耗密钥、无需重新计算的情况下查看：

- 六组原始工程题面与每组独立锁定的评分标准；
- 文献启发路线和独立记忆对照路线的同标准比较；
- 24 条路线、126 轮记录及其逐轮冻结得分曲线；
- 每轮任务状态、代表候选、观测值、反馈动作和异常记录；
- 最终路线排名、冠军候选以及从结论返回原始 JSON、JSONL 和 Markdown 的证据链接。

从仓库根目录启动：

```powershell
Set-Location -LiteralPath .\code
python scripts\run_static_replay_ui.py
```

程序优先使用 `http://127.0.0.1:8765/`；如果默认端口不可用，会自动选择本机可用端口并在终端打印实际地址。也可以显式使用 `python scripts\run_static_replay_ui.py --port 0`。页面只监听本机回环地址，支持直接切换六组运行、选择路线、打开逐轮详情并访问对应原始文件。

## 真实研究控制台

除浏览六组固化记录外，项目还提供同源的真实研究入口。使用者可以在网页中输入一段新的中文光学设计需求，提交后由现有研究链路完成问题分析、指标锁定、路线规划、任务编译、VeriTMM 仿真、候选验证、反馈迭代和最终交付。页面显示的是运行目录中实时产生的事件和结果，完成后可以直接切换到同一条运行的完整回放。

真实研究控制台的职责边界是明确的：浏览器只提交问题和受限参数，Qwen、文献服务和 VeriTMM 均在服务端运行；服务端不把密钥返回浏览器，也不允许浏览器指定任意可执行命令。未配置 Qwen 密钥时，开始按钮保持不可用，服务端也会拒绝启动请求。

### 本地运行

从仓库根目录进入 `code/`，安装完整交接环境依赖后启动：

```powershell
Set-Location -LiteralPath .\code
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-handoff.txt
.\.venv\Scripts\python.exe scripts\run_research_console.py
```

macOS 或 Linux 使用同一入口，虚拟环境激活路径改为：

```bash
cd code
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-handoff.txt
.venv/bin/python scripts/run_research_console.py
```

服务默认监听 `127.0.0.1:8765`，如果该端口被占用会自动选择可用端口。也可以使用 `--port 0` 强制选择临时端口，或使用 `--no-open` 禁止自动打开浏览器。静态回放和真实研究使用同一个页面，不需要启动两个服务。

### 服务密钥

真实研究所需的密钥只配置在服务端：

- `QWEN_API_KEY` 或 `DASHSCOPE_API_KEY`：必需；用于自然语言分析、路线规划和任务编译。
- `SEMANTIC_SCHOLAR_API_KEY`：可选；用于在线方法检索。未配置时仍会记录检索不可用状态，并按链路定义继续处理。

可以使用环境变量，也可以在 `code/api_keys/` 的空模板文件中填写本机密钥。密钥文件已经加入忽略规则，真实内容不得提交到 GitHub。对外提供网页访问时，还需要在服务端设置 `OPTOMIND_UI_ACCESS_TOKEN`；它只保护启动和停止真实运行的接口，不能替代模型服务密钥。

### 局域网或远程访问

在一台设备上运行服务、让同一局域网的其他设备访问时，先设置访问口令，再绑定所有网卡地址：

```powershell
$env:OPTOMIND_UI_ACCESS_TOKEN = '<部署者生成的随机长口令>'
python scripts\run_research_console.py --host 0.0.0.0 --port 8765 --no-open
```

将运行设备的局域网地址和端口交给访问者，例如 `http://192.168.x.x:8765/`。不要把口令写入 URL、网页代码、日志、截图或仓库。直接暴露到互联网时，应额外使用云平台的 HTTPS、访问控制和网络策略。

### Docker Compose

项目提供不依赖宿主机路径的容器配置。复制环境模板并填写服务端密钥与访问口令：

```bash
cp .env.example .env
# 编辑 .env，填写 QWEN_API_KEY 和 OPTOMIND_UI_ACCESS_TOKEN
docker compose up --build
```

然后访问 `http://127.0.0.1:8765/`。新研究结果写入名为 `optomind-runs` 的持久卷，容器重启后仍可从网页回放。容器默认使用 `/data/runs`，不依赖 Windows 盘符或用户主目录；宿主机端口可以通过 `.env` 中的 `PORT` 修改。

默认 Docker 镜像不把约 2.9 GB 的六组历史仿真目录复制进镜像，因此镜像适合部署实时研究服务。直接在 GitHub 工作树中用 Python 启动时，六组正式记录仍由静态回放读取；若希望容器也展示六组记录，可将本地 `code/outputs/tmm_research_harness` 作为只读归档卷单独挂载，并为新运行保留 `/data/runs` 可写卷。两类数据不应混写。

### 云端部署

根目录的 `render.yaml` 是一个可选的 Render Blueprint：它使用 Docker 构建单实例 Web 服务，将 `PORT` 交给容器，将 `/data` 挂载到持久磁盘，并把 `/healthz` 作为健康检查地址。首次创建服务时，在平台界面填写 `QWEN_API_KEY`；Semantic Scholar 密钥可按需要填写；访问口令由 Blueprint 生成或由部署者在平台密钥管理界面设置。

该服务使用单实例持久卷，是因为研究运行会持续写入 SQLite、JSON、JSONL 和候选结果；带持久磁盘的服务不适合自动横向扩展。Render 的官方配置参考见 [Blueprint YAML Reference](https://render.com/docs/blueprint-spec)、[Docker Services](https://render.com/docs/docker)、[Health Checks](https://render.com/docs/health-checks) 和 [Persistent Disks](https://render.com/docs/disks)。其他支持 Docker 的云平台也可以直接使用根目录 `Dockerfile`，只需将持久输出目录配置为 `/data` 并把平台分配的端口传入 `PORT`。

云端启动后，访问者打开平台生成的 HTTPS 地址，在页面的“云端运行访问口令”输入框中临时输入口令即可发起或停止任务。Qwen 和文献服务密钥始终留在云端，不进入浏览器。

## 快速开始

以下命令适用于 PowerShell。Python 3.11 或兼容版本通常更适合当前依赖组合。

```powershell
Set-Location -LiteralPath .\code
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-handoff.txt
```

在 `code/api_keys/` 中填入使用者自己的服务密钥：

- `qwen-api-key.txt`：Qwen 调用所需密钥；
- `semantic-scholar-api-key.txt`：在线文献方法检索所需密钥，可按运行配置启用。

安装后直接启动研究控制台即可同时访问真实研究和静态回放两种模式：

```powershell
.\.venv\Scripts\python.exe scripts\run_research_console.py
```


准备好密钥后，在 `code` 目录运行：

```powershell
.\.venv\Scripts\python.exe -u scripts\run_tmm_research_harness.py `
  '我需要一个双功能多层膜：在近红外 800-1500nm 波段透射率尽可能高，同时在紫外 200-400nm 波段反射率尽可能高。衬底是熔融石英，总层数控制在 30 层以内。' `
  --wall-time-seconds 3600 `
  --maximum-iterations 6 `
  --maximum-initial-routes 5 `
  --route-planning-maximum-routes 4
```

运行结果默认写入 `code/outputs/tmm_research_harness/`。离线诊断可以使用 `--force-mock`，但该模式用于检查模型不可用时的诚实失败路径，不代表一次真实科研运行，也不会制造物理结果。

## 运行产物

每次运行的关键记录包括：

- `REQUEST.json`：原始用户题面与运行身份；
- `PROBLEM_ANALYSIS.json`：问题分析和能力边界；
- `SCORING_STANDARD.json`：运行前固定的指标与评分公式；
- `METHOD_RESEARCH.json`、`STRATEGY_PLAN.json`：方法检索和路线规划状态；
- `ROUTE_PLANNING.json`：文献/对照路线及其来源；
- `iterations/`：逐轮编译任务、TMM 运行、观测和反馈；
- `ITERATION_HISTORY.json`、`ROUTE_TERMINATION_AUDIT.json`：迭代顺序与路线继续/终止审计；
- `SCORING_RANKING.json`：冻结标准下的可比较排名；
- `TOURNAMENT_SUMMARY.json`：候选组合、鲁棒性和路线汇总；
- `FINAL_ANSWER.md` 与 `RESEARCH_RESULT.json`：面向阅读和程序消费的最终结果；
- `RESEARCH_EVENTS.jsonl`：按时间顺序记录的阶段事件。

当任务超出当前 TMM 能力、材料或模型服务不可用、预算提前耗尽，系统会在相应产物中标记状态和原因；没有物理证书的候选不会被当作已验证结果。

## 本地验证

不调用在线模型的基础回归测试：

```powershell
Set-Location -LiteralPath .\code
python -m pytest -q `
  tests/test_static_replay.py `
  tests/test_run_harness_smoke.py `
  tests/test_tmm_material_registry.py `
  tests/test_tmm_task_compiler.py
```

VeriTMM 的独立任务入口位于 `veritmm/scripts/run_tmm_task.py`。其输出包含规范化任务、运行清单、仿真结果、物理接受证书和结果摘要；该引擎是 Article-1 harness 的执行组件，而不是整条研究链路本身。

## 许可与引用

`veritmm/` 子目录包含 VeriTMM 项目自身的许可、引用和第三方声明文件。
