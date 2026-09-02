# OptoMind-Article Agent 快速评估指南

> 本文件是仓库中唯一面向 AI Agent 的运行说明，服务于评委或维护者的快速摸底。普通使用者请先阅读 [README.md](README.md)。

## 1. 评估边界

OptoMind-Article 的主对象是 `code/` 下的 TMM research harness。`veritmm/` 是它调用的物理执行组件，单独跑 VeriTMM 只能证明执行器可用，不能替代整条 harness 评估。

评估时遵守以下边界：

- 不执行 Git 操作，不修改六组正式原始记录；新的测试写入新的输出目录。
- 不读取、打印、复制或上传任何密钥内容；只检查密钥文件是否存在以及字节长度。
- 不把 `--force-mock` 的结果当作真实科研结果。该模式用于检查模型不可用时是否按协议停止。
- 不把 `FINAL_ANSWER.md` 单独当作证据；数值结论必须回到迭代产物和物理证书。
- 不因单个候选失败就断言引擎故障，先检查 `RESEARCH_EVENTS.jsonl`、编译状态、TMM 运行状态和失败分类。

## 2. 目录与入口

主要入口和资产如下：

| 路径 | 用途 |
|---|---|
| `START_OPTOMIND.cmd` / `python quickstart.py ui` | 统一前端：静态回放，以及检查通过后激活的真实提问 |
| `START_REPLAY.cmd` / `python quickstart.py replay` | 六组固化产物的只读可视化入口 |
| `RUN_LIGHT_TEST.cmd` / `python quickstart.py test` | 自动配置环境并执行有界真实测试 |
| `code/scripts/run_tmm_research_harness.py` | 完整研究链路底层入口 |
| `code/replay_ui/` | 静态回放前端文件 |
| `code/prompts/optical_harness/` | 运行时提示模板 |
| `code/tests/` | 单元、协议、物理链路和回归测试 |
| `code/api_keys/qwen-api-key.txt` | Qwen 空密钥模板 |
| `code/api_keys/semantic-scholar-api-key.txt` | Semantic Scholar 空密钥模板 |
| `code/outputs/tmm_research_harness/` | 六组正式记录与新运行的默认输出位置 |
| `veritmm/scripts/run_tmm_task.py` | 独立 VeriTMM 任务入口 |

主 harness 会优先使用仓库根目录同级的 `veritmm/`，因此不应把它改回原开发机的绝对路径。

## 3. 六组结果的只读可视化回放

无需配置任何密钥即可启动静态研究回放台：

```powershell
python quickstart.py replay
```

该入口只读取 `code/outputs/tmm_research_harness/` 下已经完成的运行，不调用模型、文献服务、优化器或 VeriTMM，也不修改原始记录。评估时可依次核对题面、冻结标准、路线来源、逐轮曲线、反馈状态、最终排名和原始证据链接。默认端口不可用时程序会自动选择可用端口，也可显式追加 `--port 0`。

## 4. 统一前端与十倍速回放

统一前端的“成果回放”页读取 `code/outputs/tmm_research_harness/` 下已完成的六组记录；“真实提问”页默认锁定，只有本地资产、密钥、Python 依赖、Qwen 与 Semantic Scholar 的实际连通检查全部通过后才激活：

```powershell
python quickstart.py ui --port 0
```

页面读取每组运行保存的 `RESEARCH_EVENTS.jsonl`，将原始阶段事件以浏览器端时间线呈现。播放速度可选 1×、2×、5× 或 10×；模拟播放不会调用 Qwen、文献服务、优化器或 VeriTMM，也不会修改任何历史产物。真实任务一次只允许启动一个，题面和结果保存于 Git 忽略的 `local_runs/`；任务完成后可在同一回放页查看。服务器只监听本机回环地址，浏览器端不接收、保存或显示密钥值。

## 5. 离线摸底

先运行不需要在线密钥的最小回归集：

```powershell
python -m pytest -q `
  code/tests/test_static_replay.py `
  code/tests/test_quickstart.py `
  code/tests/test_run_harness_smoke.py `
  code/tests/test_tmm_material_registry.py `
  code/tests/test_tmm_task_compiler.py
```

如需检查 CLI 是否可启动：

```powershell
python -u code/scripts/run_tmm_research_harness.py --help
```

如需检查模型不可用时的关闭行为：

```powershell
python -u code/scripts/run_tmm_research_harness.py `
  '测试一个双波段薄膜设计需求' `
  --force-mock `
  --no-online-method-research `
  --no-qwen-method-synthesis `
  --maximum-iterations 1 `
  --maximum-initial-routes 1 `
  --route-planning-maximum-routes 1 `
  --wall-time-seconds 60
```

预期是得到一个带 `analysis_failed` 或其他诚实终止状态的结果，而不是虚构候选。这个命令不验证在线 Qwen 规划和真实 TMM 结果。

## 6. 真实轻量运行

将私发的 `api_keys` 文件夹复制到 `code/api_keys` 后，从仓库根目录执行：

```powershell
python quickstart.py ui
```

Windows 可直接双击 `START_OPTOMIND.cmd`，不需要设置环境变量。进入“真实提问”后先运行准备检查；检查通过才会激活题面和运行方式。快速模式为 1 条路线、1 轮、最长 30 分钟，完整模式采用当前默认路线配置、最多 6 轮、最长 3 小时。输出写入 Git 忽略的 `local_runs/`，不进入、不覆盖也不改变六组正式记录。原有 `RUN_LIGHT_TEST.cmd` / `python quickstart.py test` 仍保留为固定题面的命令行轻量入口。

评估结果时，至少确认以下阶段是否按顺序出现：

1. `problem_analyzed`；
2. `scoring_standard_fixed`；
3. `routes_planned_from_literature` 或明确的路线不可用状态；
4. `strategy_planned`；
5. 迭代目录中的 `COMPILED_TASK.json` 和 `TASK_COMPILATION.json`；
6. `tmm_run` 中的执行状态、候选验证和物理证书；
7. `SCORING_RANKING.json`、`TOURNAMENT_SUMMARY.json` 和 `RESEARCH_RESULT.json`。

真实链路的前置模型调用可能比 TMM 计算更慢。若在预算内只完成问题分析、评分和路线规划，应据 `RESEARCH_EVENTS.jsonl` 如实记录为“前置阶段完成、尚未进入候选执行”，不能将其误报为物理引擎失败。

## 7. 完整结果判定

一份可用于研究回放的完整运行通常应同时具备：

- 原始题面和问题分析；
- 运行开始前固定的评分标准；
- 路线来源及文献/对照标记；
- 每轮编译任务、观测和反馈；
- 至少一个候选对应的物理接受证书；
- 冻结评分排名和竞赛汇总；
- 运行事件、预算和终止审计。

检查候选时，优先查看 `PHYSICS_ACCEPTANCE_CERTIFICATE.json`、`RESULT_SUMMARY.json`、`FINAL_RESULT.json`（若该迭代产生）以及 `SCORING_RANKING.json`。只有通过物理验证且满足冻结评分字段映射的候选，才进入可比较排名。

对照实验只有在文献规划路线与记忆路线都产生可比的冻结标准结果时才可下跨来源结论。若某一臂没有可评分候选，结果文件中的缺失状态是正式结论，不应人工补齐。

## 8. 六组历史记录

六组记录是随仓库分发的原始证据资产，不是 Agent 的默认输入，也不是待自动修复的缓存：

```text
code/outputs/tmm_research_harness/e2e-methane-swir-window-20260828-default4-w10800
code/outputs/tmm_research_harness/e2e-uav-swir-window-20260829-default4-w10800
code/outputs/tmm_research_harness/e2e-space-qkd-cband-window-20260829-default4-w10800
code/outputs/tmm_research_harness/e2e-solarblind-uv-window-20260829-default4-w10800
code/outputs/tmm_research_harness/e2e-fifth-dualgas-mwir-20260829-default4-w10800
code/outputs/tmm_research_harness/e2e-sixth-combustion-co-20260829-default4-w10800
```

评估或展示时只读这些目录；新问题、新参数和新修复必须写入新 `run_id` 目录。

## 9. 常见状态解释

- `completed`：运行完成，仍需检查候选和物理证书，不能只看终态字符串。
- `completed_best_effort_no_verified_candidate`：流程完成到预算允许的阶段，但没有可验证候选。
- `analysis_failed`、`compilation_failed`、`unavailable`：相应阶段未通过协议，属于真实运行状态。
- `rejected_physics`：执行器完成了计算，但物理验收拒绝了该任务或候选；应读取证书中的失败分类和下一步动作。
- `--force-mock`：离线诊断开关，不产生可发表的实验结果。

## 10. 评估记录建议

每次摸底至少保存以下信息：运行题面、命令参数、`RESEARCH_EVENTS.jsonl`、`RESEARCH_RESULT.json`、评分标准、路线规划、迭代目录、物理证书、终止审计和最终回答。不要用手工摘要替换原始 JSON，也不要在公开记录中包含密钥、临时绝对路径或未验证的模型陈述。
