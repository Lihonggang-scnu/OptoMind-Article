# OptoMind-Article

OptoMind-Article 是一个面向光学薄膜设计的可审计科学实验任务规划与反馈迭代系统。它接收自然语言形式的工程需求，端到端生成科研实验报告。

## 研究闭环
OptoMind-Article 把一次光学设计研究组织成一条可以回放的证据链：从自然语言需求、问题边界和评价指标开始，经过文献启发路线与独立对照路线的规划，生成可执行的 VeriTMM 任务，完成真实光学计算、候选验证、反馈迭代和最终比较。研究过程中的事件、预算、评分、路线来源、任务编译、物理证书和停止原因都会留下结构化记录。系统同时提供事件链、冻结评分标准、行动效果账本、假设树搜索、提案门、人在回路确认、插件化运行内核、四态运行核验、梯度/灵敏度反馈和确定性运行记录。语言模型负责受约束的问题分析、研究假设和路线表达；任务校验、物理计算、证书签发、评分和产物索引由程序层完成。OptoMind-Article 提供了一个接近真正“自主科学家”的 AI4S 形态：LLM 负责提出科学假设与研究策略，Verifier 负责决定什么结果值得相信，真实物理引擎负责让这些想法接受实验级检验。而整个过程在反思迭代中实现闭环。



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
| `START_OPTOMIND.cmd` | Windows 统一入口：静态回放立即可用，连通检查通过后解锁真实提问。 |
| `START_REPLAY.cmd` | Windows 双击启动只读回放入口；在线证据回放无需安装依赖和配置密钥。 |
| `RUN_LIGHT_TEST.cmd` | Windows 双击执行一次有界的真实轻量研究测试。 |
| `quickstart.py` | Windows、macOS 和 Linux 共用的评审快捷入口。 |
| `code/` | 主 harness、配置、测试、工具和运行脚本。 |
| `code/replay_ui/` | 公开研究记录的只读可视化静态回放前端。 |
| `code/scripts/run_static_replay_ui.py` | 静态回放台本地启动入口。 |
| `code/prompts/optical_harness/` | 运行时使用的结构化提示模板。 |
| `veritmm/` | 与本项目同级挂载的 VeriTMM 物理执行引擎。 |
| `accepted_examples/` | 验收与示例资产。 |
| `article_memory/` | 文章链路使用的记忆边界和清单资产。 |
| `replay_data/` | 精简研究回放摘要，可供静态回放前端读取。 |
| `AGENT_GUIDE.zh-CN.md` | 面向AI Agent的快速运行与摸底指南。 |


## 端到端运行成果

项目中的正式研究运行都从面向工程用户的自然语言题面开始。当前公开了 12 组跨主题运行。每组运行在开始阶段根据对应题面固定自己的评分字段和公式。“冻结标准得分”只用于该组内部的候选与路线比较。代表结构和指标详情可在 `replay_data/runs/` 中逐条查看。

| # | 研究主题 | 代表结构 | 冻结标准得分 | 迭代 | 估算成本 |
|---:|---|---|---:|---:|---:|
| 1 | 甲烷 SWIR 窗口 | 16 层 HfO₂/SiO₂；文献启发路线 | **1.8092** | 12 | ¥1.75 |
| 2 | 星载 QKD C 波段接收 | 30 层 HfO₂/SiO₂；文献启发路线 | **1.9063** | 16 | ¥2.07 |
| 3 | 甲烷 SWIR 梯度膜系 | 24 层 HfO₂/SiO₂；文献启发路线 | **1.9080** | 21 | ¥3.06 |
| 4 | 太阳盲 UV 三指标滤光 | 29 层 HfO₂/MgF₂；独立记忆对照路线 | **2.2211** | 28 | ¥3.77 |
| 5 | 可见光选择性吸收膜 | 13 层 HfO₂/SiO₂/Cr；独立记忆对照路线 | **1.3484** | 28 | ¥3.81 |
| 6 | 热红外发射率调控 | 20 层 HfO₂/SiO₂；独立记忆对照路线 | **0.8591** | 21 | ¥3.20 |
| 7 | QKD C 波段平均口径 | 24 层 HfO₂/SiO₂；文献启发路线 | **1.8995** | 24 | ¥3.93 |
| 8 | 先进封装 UV 激发抑制 | 36 层 Ta₂O₅/SiO₂；文献启发路线 | **0.6444** | 36 | ¥6.50 |
| 9 | 硅光双通信波段减反 | 10 层 Ta₂O₅/SiO₂；文献启发路线 | **−0.0012** | 22 | ¥2.91 |
| 10 | LiDAR 1550 nm 窄带滤光 | 29 层 Ta₂O₅/SiO₂；独立记忆对照路线 | **2.9611** | 36 | ¥5.38 |
| 11 | RGB/NIR 双波段分光 | 24 层 TiO₂/SiO₂；文献启发路线 | **1.9660** | 31 | ¥4.66 |
| 12 | LWIR Ge 窗口 | 8 层 ZnSe/ZnS；文献启发路线 | **0.1884** | 29 | ¥3.76 |

这些运行覆盖环境监测、量子通信、空间探测、先进封装、热管理、硅光互连、激光雷达、多光谱成像和长波红外窗口等应用主题。文献路线与独立对照路线都在同一套冻结标准下接受比较，结果保留各自题面的物理含义。

精简回放数据把每组运行的关键证据压缩到一个只读 JSON 中，保留从科学问题到物理结果的可追踪关系：
| 证据阶段 | 回放字段 | 公开内容 |
|---|---|---|
| 问题与评分 | `problem`、`scoring` | 研究题面、波段、观测量、约束和本组冻结的评分口径 |
| 路线与迭代 | `routes`、`event_timeline` | 文献启发路线、独立对照路线、逐轮状态、候选和反馈动作 |
| 结果与比较 | `leaderboard`、`champion`、`source_comparison` | 路线排名、冠军候选、指标结果及同一口径下的路线比较 |
| 物理与审计 | `evidence`、`telemetry` | 物理证书摘要、证据引用、执行统计和可复核的运行元数据 |

下载源码后进行的新运行会在本地生成更细的 `REQUEST.json`、`SCORING_STANDARD.json`、`iterations/`、物理证书和事件记录。



## 测试

### 方式一：在线查看静态回放，不下载源码
直接访问 [OptoMind 在线证据回放](https://lihonggang-scnu.github.io/OptoMind-Article/)。页面面向公开研究记录，展示原始题面、动态冻结评分、文献路线与独立对照路线、逐轮观测与反馈、冠军候选、物理证书和证据链接。在线版完全只读，不需要 Python、源码或服务密钥。
### 方式二：下载源码，使用统一前端
推荐使用 Python 3.11 或 3.12；Windows 安装 Python 时请勾选“Add Python to PATH”。
1. 从 GitHub 下载并解压本仓库；Windows 用户双击根目录的 `START_OPTOMIND.cmd`。统一前端会自动打开，并提供回放入口与真实运行入口。
2. 如需真实测试，将有效 `api_keys` 文件夹复制到 `code/api_keys`，覆盖其中两个同名空模板，使最终路径成为 `code/api_keys/qwen-api-key.txt` 和 `code/api_keys/semantic-scholar-api-key.txt`，然后双击 `START_OPTOMIND.cmd`。
3. 在统一前端选择“真实提问”，点击“检查并准备真实运行”。程序会核对项目资产、准备隔离 Python 环境，并对 Qwen 和 Semantic Scholar 发起最小真实连通请求；只有全部通过后，问题输入框和运行按钮才会激活。
4. 输入一个自然语言光学设计需求，选择“快速真实验证”或“完整自主研究”。当前任务的阶段事件和进度会显示在同一页面。

或者执行：
```powershell
cd code
python -u scripts/run_tmm_research_harness.py `
  '甲烷泄漏巡检短波红外窗口薄膜设计：T(1000-1700nm) 尽可能高，R(300-450nm) 尽可能高，HfO2/SiO2，熔融石英基底，不超过 30 层' `
  --wall-time-seconds 9000 `
  --minimum-rounds-before-llm-stop 4 `
  --max-rounds-per-route 10 `
  --route-planning-maximum-routes 6 `
  --maximum-initial-routes 6
```
即可运行。输出写入 `local_runs/<run-id>/`。完整模式约 20-40 轮、20-90 分钟、¥2-7。

macOS、Linux 用户在仓库根目录执行：
```bash
python3 quickstart.py ui
```


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
当任务超出当前 TMM 能力、材料或模型服务不可用、预算提前耗尽，系统会在相应产物中标记状态和原因。


VeriTMM 的独立任务入口位于 `veritmm/scripts/run_tmm_task.py`。其输出包含规范化任务、运行清单、仿真结果、物理接受证书和结果摘要；该引擎是OptoMind-Article harness 的执行组件。

## 许可与引用

`veritmm/` 子目录包含 VeriTMM 项目自身的许可、引用和第三方声明文件。
